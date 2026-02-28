import logging
from collections import defaultdict
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from topology.api.serializers import (
    OLTSerializer,
    OLTSlotSerializer,
    OLTTopologySerializer,
    OLTPONSerializer,
    ONUSerializer,
    VendorProfileSerializer,
)
from topology.api.auth_utils import can_modify_settings
from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, VendorProfile
from topology.services.cache_service import cache_service
from topology.services.maintenance_job_service import maintenance_job_service
from topology.services.maintenance_runtime import collect_power_for_olt, ensure_status_snapshot_for_power, has_usable_status_snapshot
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.power_service import power_service
from topology.services.snmp_service import snmp_service
from topology.services.topology_service import TopologyService

logger = logging.getLogger(__name__)


def _is_true(value: str | None) -> bool:
    return str(value or '').lower() in {'1', 'true', 'yes', 'on'}


def _settings_forbidden_response():
    return Response(
        {'detail': 'Insufficient permissions for this action.'},
        status=status.HTTP_403_FORBIDDEN,
    )


class VendorProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Vendor Profiles
    """

    queryset = VendorProfile.objects.filter(is_active=True).order_by('id')
    serializer_class = VendorProfileSerializer


class OLTViewSet(viewsets.ModelViewSet):
    """
    ViewSet for OLTs
    Supports both flat list and nested topology responses.
    """

    serializer_class = OLTSerializer

    def _ensure_settings_write_access(self, request):
        if can_modify_settings(request.user):
            return None
        return _settings_forbidden_response()

    def create(self, request, *args, **kwargs):
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))

        queryset = (
            OLT.objects.filter(is_active=True)
            .select_related('vendor_profile')
            .order_by('id')
        )

        if include_topology:
            active_log_prefetch = Prefetch(
                'logs',
                queryset=ONULog.objects.filter(offline_until__isnull=True).order_by('-offline_since'),
                to_attr='active_logs',
            )
            onus_qs = (
                ONU.objects.filter(is_active=True)
                .select_related('slot_ref', 'pon_ref')
                .prefetch_related(active_log_prefetch)
                .order_by('onu_id')
            )
            pons_qs = (
                OLTPON.objects.filter(is_active=True)
                .prefetch_related(Prefetch('onus', queryset=onus_qs))
                .order_by('pon_id')
            )
            slots_qs = (
                OLTSlot.objects.filter(is_active=True)
                .prefetch_related(Prefetch('pons', queryset=pons_qs))
                .order_by('slot_id')
            )
            queryset = queryset.prefetch_related(Prefetch('slots', queryset=slots_qs))

        return queryset

    def get_serializer_class(self):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))
        if include_topology and self.action == 'list':
            return OLTTopologySerializer
        return OLTSerializer

    @staticmethod
    def _dt_to_iso(value):
        return value.isoformat() if value else None

    @staticmethod
    def _as_list(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
        return []

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _iter_topology_onus_from_list_row(self, row):
        if not isinstance(row, dict):
            return
        for slot in self._as_list(row.get('slots')):
            if not isinstance(slot, dict):
                continue
            for pon in self._as_list(slot.get('pons')):
                if not isinstance(pon, dict):
                    continue
                for onu in self._as_list(pon.get('onus')):
                    if isinstance(onu, dict):
                        yield onu

    def _build_list_olt_onu_map(self, payload):
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get('results'), list):
            rows = payload.get('results') or []
        else:
            return {}

        per_olt_ids = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            olt_id = self._to_int(row.get('id'))
            if olt_id is None:
                continue
            onu_ids = per_olt_ids.setdefault(olt_id, set())
            for onu in self._iter_topology_onus_from_list_row(row):
                onu_id = self._to_int(onu.get('id'))
                if onu_id is not None:
                    onu_ids.add(onu_id)
        return {olt_id: sorted(list(onu_ids)) for olt_id, onu_ids in per_olt_ids.items() if onu_ids}

    def _build_detail_olt_onu_map(self, payload, *, olt_id=None):
        if not isinstance(payload, dict):
            return {}
        olt_block = payload.get('olt')
        if not isinstance(olt_block, dict):
            return {}

        resolved_olt_id = self._to_int(olt_block.get('id')) or self._to_int(olt_id)
        if resolved_olt_id is None:
            return {}

        onu_ids = set()
        for slot in self._as_list(payload.get('slots')):
            if not isinstance(slot, dict):
                continue
            for pon in self._as_list(slot.get('pons')):
                if not isinstance(pon, dict):
                    continue
                for onu in self._as_list(pon.get('onus')):
                    if not isinstance(onu, dict):
                        continue
                    onu_id = self._to_int(onu.get('id'))
                    if onu_id is not None:
                        onu_ids.add(onu_id)
        if not onu_ids:
            return {}
        return {resolved_olt_id: sorted(list(onu_ids))}

    def _build_runtime_onu_overlay_map(self, olt_to_onu_ids):
        status_map_by_olt = {}
        power_map_by_olt = {}
        for olt_id, onu_ids in olt_to_onu_ids.items():
            if not onu_ids:
                continue
            status_map_by_olt[olt_id] = cache_service.get_many_onu_status(olt_id, onu_ids)
            power_map_by_olt[olt_id] = cache_service.get_many_onu_power(olt_id, onu_ids)
        return status_map_by_olt, power_map_by_olt

    @staticmethod
    def _overlay_runtime_onu_payload(
        onu_payload,
        *,
        status_payload,
        power_payload,
    ):
        if not isinstance(onu_payload, dict):
            return

        if isinstance(status_payload, dict) and status_payload:
            if 'status' in onu_payload and status_payload.get('status'):
                onu_payload['status'] = status_payload.get('status')
            if 'disconnect_reason' in onu_payload and 'disconnect_reason' in status_payload:
                reason = status_payload.get('disconnect_reason')
                onu_payload['disconnect_reason'] = reason or None
            if 'offline_since' in onu_payload and 'offline_since' in status_payload:
                offline_since = status_payload.get('offline_since')
                onu_payload['offline_since'] = offline_since or None
            if 'disconnect_window_start' in onu_payload and 'disconnect_window_start' in status_payload:
                window_start = status_payload.get('disconnect_window_start')
                onu_payload['disconnect_window_start'] = window_start or None
            if 'disconnect_window_end' in onu_payload and 'disconnect_window_end' in status_payload:
                window_end = status_payload.get('disconnect_window_end')
                onu_payload['disconnect_window_end'] = window_end or None

        if isinstance(power_payload, dict) and power_payload:
            if 'onu_rx_power' in onu_payload and 'onu_rx_power' in power_payload:
                onu_payload['onu_rx_power'] = power_payload.get('onu_rx_power')
            if 'olt_rx_power' in onu_payload and 'olt_rx_power' in power_payload:
                onu_payload['olt_rx_power'] = power_payload.get('olt_rx_power')
            if 'power_read_at' in onu_payload and 'power_read_at' in power_payload:
                onu_payload['power_read_at'] = power_payload.get('power_read_at')

    def _overlay_runtime_onu_data_on_list_payload(self, payload):
        olt_to_onu_ids = self._build_list_olt_onu_map(payload)
        if not olt_to_onu_ids:
            return payload

        status_map_by_olt, power_map_by_olt = self._build_runtime_onu_overlay_map(olt_to_onu_ids)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get('results'), list):
            rows = payload.get('results') or []
        else:
            return payload

        for row in rows:
            if not isinstance(row, dict):
                continue
            olt_id = self._to_int(row.get('id'))
            if olt_id is None:
                continue
            status_map = status_map_by_olt.get(olt_id, {})
            power_map = power_map_by_olt.get(olt_id, {})
            for onu in self._iter_topology_onus_from_list_row(row):
                onu_id = self._to_int(onu.get('id'))
                if onu_id is None:
                    continue
                self._overlay_runtime_onu_payload(
                    onu,
                    status_payload=status_map.get(onu_id),
                    power_payload=power_map.get(onu_id),
                )
        return payload

    def _overlay_runtime_onu_data_on_detail_payload(self, payload, *, olt_id=None):
        olt_to_onu_ids = self._build_detail_olt_onu_map(payload, olt_id=olt_id)
        if not olt_to_onu_ids:
            return payload

        status_map_by_olt, power_map_by_olt = self._build_runtime_onu_overlay_map(olt_to_onu_ids)
        resolved_olt_id = next(iter(olt_to_onu_ids.keys()))
        status_map = status_map_by_olt.get(resolved_olt_id, {})
        power_map = power_map_by_olt.get(resolved_olt_id, {})

        for slot in self._as_list(payload.get('slots')):
            if not isinstance(slot, dict):
                continue
            for pon in self._as_list(slot.get('pons')):
                if not isinstance(pon, dict):
                    continue
                for onu in self._as_list(pon.get('onus')):
                    if not isinstance(onu, dict):
                        continue
                    onu_id = self._to_int(onu.get('id'))
                    if onu_id is None:
                        continue
                    self._overlay_runtime_onu_payload(
                        onu,
                        status_payload=status_map.get(onu_id),
                        power_payload=power_map.get(onu_id),
                    )
        return payload

    def _build_runtime_overlay_map(self, olt_ids):
        ids = []
        seen = set()
        for raw in olt_ids:
            if raw in (None, ''):
                continue
            try:
                normalized = int(raw)
            except (TypeError, ValueError):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            ids.append(normalized)

        if not ids:
            return {}

        overlays = {}
        runtime_rows = OLT.objects.filter(id__in=ids).values(
            'id',
            'snmp_reachable',
            'snmp_failure_count',
            'last_snmp_error',
            'last_snmp_check_at',
            'discovery_interval_minutes',
            'polling_interval_seconds',
            'power_interval_seconds',
            'last_discovery_at',
            'last_poll_at',
            'last_power_at',
            'next_discovery_at',
            'next_poll_at',
            'next_power_at',
        )
        for row in runtime_rows:
            overlays[str(row['id'])] = {
                'snmp_reachable': row['snmp_reachable'],
                'snmp_failure_count': int(row['snmp_failure_count'] or 0),
                'last_snmp_error': row['last_snmp_error'] or '',
                'last_snmp_check_at': self._dt_to_iso(row['last_snmp_check_at']),
                'discovery_interval_minutes': row['discovery_interval_minutes'],
                'polling_interval_seconds': row['polling_interval_seconds'],
                'power_interval_seconds': row['power_interval_seconds'],
                'last_discovery_at': self._dt_to_iso(row['last_discovery_at']),
                'last_poll_at': self._dt_to_iso(row['last_poll_at']),
                'last_power_at': self._dt_to_iso(row['last_power_at']),
                'next_discovery_at': self._dt_to_iso(row['next_discovery_at']),
                'next_poll_at': self._dt_to_iso(row['next_poll_at']),
                'next_power_at': self._dt_to_iso(row['next_power_at']),
            }
        return overlays

    def _overlay_runtime_health_on_list_payload(self, payload):
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get('results'), list):
            rows = payload.get('results') or []
        else:
            return payload

        overlay_map = self._build_runtime_overlay_map([row.get('id') for row in rows if isinstance(row, dict)])
        if not overlay_map:
            return payload

        for row in rows:
            if not isinstance(row, dict):
                continue
            overlay = overlay_map.get(str(row.get('id')))
            if not overlay:
                continue
            for key, value in overlay.items():
                if key in row:
                    row[key] = value
        return payload

    def _overlay_runtime_health_on_detail_payload(self, payload, *, olt_id=None):
        if not isinstance(payload, dict):
            return payload

        olt_block = payload.get('olt')
        if not isinstance(olt_block, dict):
            return payload

        resolved_olt_id = olt_block.get('id') if olt_block.get('id') is not None else olt_id
        overlay_map = self._build_runtime_overlay_map([resolved_olt_id])
        overlay = overlay_map.get(str(resolved_olt_id))
        if not overlay:
            return payload

        for key, value in overlay.items():
            if key in olt_block:
                olt_block[key] = value

        if 'last_discovery' in olt_block:
            olt_block['last_discovery'] = overlay['last_discovery_at']
        if 'last_poll' in olt_block:
            olt_block['last_poll'] = overlay['last_poll_at']
        return payload

    def list(self, request, *args, **kwargs):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))
        cache_ttl = int(
            getattr(
                settings,
                'OLT_TOPOLOGY_LIST_CACHE_TTL' if include_topology else 'OLT_LIST_CACHE_TTL',
                0,
            )
            or 0
        )
        cache_key = None
        if cache_ttl > 0:
            cache_key = cache_service.get_api_olts_key(
                include_topology=include_topology,
                query_signature=request.get_full_path(),
            )
            cached_payload = cache_service.get(cache_key)
            if cached_payload is not None:
                cached_payload = self._overlay_runtime_health_on_list_payload(cached_payload)
                if include_topology:
                    cached_payload = self._overlay_runtime_onu_data_on_list_payload(cached_payload)
                return Response(cached_payload)

        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)

        context = self.get_serializer_context()
        if include_topology:
            context['power_map'] = self._build_power_map(rows)

        serializer = self.get_serializer(rows, many=True, context=context)
        response = None
        payload = serializer.data
        if page is not None:
            response = self.get_paginated_response(serializer.data)
            payload = response.data

        payload = self._overlay_runtime_health_on_list_payload(payload)
        if cache_key:
            cache_service.set(cache_key, payload, ttl=cache_ttl)
        if response is not None:
            return response
        return Response(payload)

    def perform_create(self, serializer):
        olt = serializer.save()
        cache_service.invalidate_olt_cache(olt.id)

    def perform_update(self, serializer):
        tracked_fields = {
            'vendor_profile_id',
            'protocol',
            'ip_address',
            'snmp_port',
            'snmp_community',
            'snmp_version',
            'discovery_interval_minutes',
            'polling_interval_seconds',
            'power_interval_seconds',
            'discovery_enabled',
            'polling_enabled',
        }
        current = serializer.instance
        before = {field: getattr(current, field) for field in tracked_fields}
        olt = serializer.save()
        if any(before[field] != getattr(olt, field) for field in tracked_fields):
            cache_service.invalidate_olt_cache(olt.id)

    def destroy(self, request, *args, **kwargs):
        """
        Soft-delete OLT and deactivate discovered topology to preserve history.
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = self.get_object()
        if not olt.is_active:
            return Response(status=status.HTTP_204_NO_CONTENT)

        now = timezone.now()
        with transaction.atomic():
            OLT.objects.filter(id=olt.id).update(
                is_active=False,
                discovery_enabled=False,
                polling_enabled=False,
                next_discovery_at=None,
                next_poll_at=None,
                next_power_at=None,
            )
            ONULog.objects.filter(onu__olt=olt, offline_until__isnull=True).update(offline_until=now)
            ONU.objects.filter(olt=olt, is_active=True).update(
                is_active=False,
                status=ONU.STATUS_UNKNOWN,
            )
            OLTPON.objects.filter(olt=olt, is_active=True).update(is_active=False)
            OLTSlot.objects.filter(olt=olt, is_active=True).update(is_active=False)

        cache_service.invalidate_olt_cache(olt.id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _validate_vendor_action(self, olt, *, capability_field, required_template_paths, action_name):
        profile = olt.vendor_profile
        if not profile.is_active:
            return Response(
                {
                    'status': 'error',
                    'olt_id': olt.id,
                    'detail': 'Vendor profile is inactive.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if capability_field and not getattr(profile, capability_field, False):
            return Response(
                {
                    'status': 'error',
                    'olt_id': olt.id,
                    'detail': f'Vendor profile does not support {action_name}.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        oid_templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        missing_paths = []
        for path in required_template_paths:
            node = oid_templates
            for key in path:
                if not isinstance(node, dict):
                    node = None
                    break
                node = node.get(key)
            if node in (None, ''):
                missing_paths.append('.'.join(path))

        if missing_paths:
            return Response(
                {
                    'status': 'error',
                    'olt_id': olt.id,
                    'detail': 'Vendor profile is missing required OID templates.',
                    'missing_templates': missing_paths,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def _build_power_map(self, olts):
        power_map = {}
        for olt in olts:
            onu_ids = []
            for slot in olt.slots.all():
                for pon in slot.pons.all():
                    onu_ids.extend([onu.id for onu in pon.onus.all()])
            if onu_ids:
                power_map.update(cache_service.get_many_onu_power(olt.id, onu_ids))
        return power_map

    def _collect_power_for_olt(self, olt: OLT, *, force_refresh=True, include_results=True):
        return collect_power_for_olt(
            olt,
            force_refresh=force_refresh,
            include_results=include_results,
        )

    def _serialize_maintenance_job(self, job):
        return maintenance_job_service.serialize_job(job)

    def _enqueue_maintenance_job(self, *, olt: OLT, kind: str, request):
        accepted_detail_map = {
            'discovery': 'Discovery scheduled in background.',
            'polling': 'Polling scheduled in background.',
            'power': 'Power refresh scheduled in background.',
        }
        job, queued = maintenance_job_service.enqueue_job(
            olt_id=olt.id,
            kind=kind,
            requested_by=request.user,
        )
        payload = self._serialize_maintenance_job(job)
        if not queued:
            return Response(
                {
                    'status': 'already_running',
                    'olt_id': olt.id,
                    'detail': 'Another maintenance task is already running for this OLT.',
                    'job': payload,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        return Response(
            {
                'status': 'accepted',
                'olt_id': olt.id,
                'detail': accepted_detail_map.get(kind, 'Maintenance task queued successfully.'),
                'job': payload,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['get'])
    def topology(self, request, pk=None):
        """
        Returns complete OLT topology
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        cache_ttl = int(getattr(settings, 'OLT_TOPOLOGY_DETAIL_CACHE_TTL', 0) or 0)
        cache_key = None
        if cache_ttl > 0:
            cache_key = cache_service.get_api_olt_topology_key(olt.id)
            cached_payload = cache_service.get(cache_key)
            if isinstance(cached_payload, dict):
                cached_payload = self._overlay_runtime_health_on_detail_payload(cached_payload, olt_id=olt.id)
                cached_payload = self._overlay_runtime_onu_data_on_detail_payload(cached_payload, olt_id=olt.id)
                return Response(cached_payload)
        service = TopologyService()
        topology = service.build_topology(olt)
        topology = self._overlay_runtime_health_on_detail_payload(topology, olt_id=olt.id)
        if cache_key:
            cache_service.set(cache_key, topology, ttl=cache_ttl)
        return Response(topology)

    @action(detail=True, methods=['get'], url_path='maintenance_status')
    def maintenance_status(self, request, pk=None):
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        active_job = maintenance_job_service.get_active_job(olt.id)
        latest_job = active_job or maintenance_job_service.get_latest_job(olt.id)
        return Response(
            {
                'olt_id': olt.id,
                'has_active_job': bool(active_job),
                'active_job': self._serialize_maintenance_job(active_job),
                'latest_job': self._serialize_maintenance_job(latest_job),
            }
        )

    @action(detail=True, methods=['post'])
    def refresh_power(self, request, pk=None):
        """
        Triggers power refresh for all ONUs in this OLT
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_power_monitoring',
            required_template_paths=[('power', 'onu_rx_oid')],
            action_name='power refresh',
        )
        if validation_error is not None:
            return validation_error

        if _is_true(request.data.get('background', 'false')):
            return self._enqueue_maintenance_job(
                olt=olt,
                kind='power',
                request=request,
            )

        payload = self._collect_power_for_olt(olt, force_refresh=True, include_results=True)
        return Response(payload)

    @action(detail=False, methods=['post'], url_path='refresh_power')
    def refresh_power_all(self, request):
        """
        Triggers power refresh for every active OLT and stores a fresh batch snapshot.
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        force_refresh = _is_true(request.data.get('force_refresh', True))
        olts = list(
            OLT.objects.filter(is_active=True, vendor_profile__is_active=True)
            .select_related('vendor_profile')
            .order_by('id')
        )

        results = []
        total_onu_count = 0
        total_attempted_count = 0
        total_skipped_not_online_count = 0
        total_skipped_offline_count = 0
        total_skipped_unknown_count = 0
        total_collected_count = 0
        completed_count = 0
        skipped_count = 0
        error_count = 0

        for olt in olts:
            validation_error = self._validate_vendor_action(
                olt,
                capability_field='supports_power_monitoring',
                required_template_paths=[('power', 'onu_rx_oid')],
                action_name='power refresh',
            )
            if validation_error is not None:
                skipped_count += 1
                payload = dict(validation_error.data)
                payload['status'] = 'skipped'
                results.append(payload)
                continue

            try:
                payload = self._collect_power_for_olt(
                    olt,
                    force_refresh=force_refresh,
                    include_results=False,
                )
                completed_count += 1
                total_onu_count += payload['count']
                total_attempted_count += payload.get('attempted_count', payload['count'])
                total_skipped_not_online_count += payload.get('skipped_not_online_count', 0)
                total_skipped_offline_count += payload.get('skipped_offline_count', 0)
                total_skipped_unknown_count += payload.get('skipped_unknown_count', 0)
                total_collected_count += payload['collected_count']
                results.append(payload)
            except Exception as exc:
                error_count += 1
                logger.exception("Power refresh failed for OLT %s", olt.id)
                results.append(
                    {
                        'status': 'error',
                        'olt_id': olt.id,
                        'detail': str(exc),
                    }
                )

        return Response(
            {
                'status': 'completed',
                'olt_count': len(olts),
                'completed_count': completed_count,
                'skipped_count': skipped_count,
                'error_count': error_count,
                'total_onu_count': total_onu_count,
                'total_attempted_count': total_attempted_count,
                'total_skipped_not_online_count': total_skipped_not_online_count,
                'total_skipped_offline_count': total_skipped_offline_count,
                'total_skipped_unknown_count': total_skipped_unknown_count,
                'total_collected_count': total_collected_count,
                'results': results,
            }
        )

    @action(detail=True, methods=['post'])
    def run_discovery(self, request, pk=None):
        """
        Run ONU discovery immediately for one OLT.
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_onu_discovery',
            required_template_paths=[('discovery', 'onu_serial_oid')],
            action_name='ONU discovery',
        )
        if validation_error is not None:
            return validation_error

        if _is_true(request.data.get('background', 'false')):
            return self._enqueue_maintenance_job(
                olt=olt,
                kind='discovery',
                request=request,
            )

        output = StringIO()
        try:
            call_command('discover_onus', olt_id=olt.id, force=True, stdout=output)
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        cache_service.invalidate_topology_api_cache(olt.id)
        return Response({'status': 'completed', 'olt_id': olt.id, 'output': output.getvalue().strip()})

    @action(detail=True, methods=['post'])
    def snmp_check(self, request, pk=None):
        """
        Quick SNMP connectivity check — queries sysDescr.0 to verify the OLT is reachable.
        Returns { reachable: bool, sys_descr: str|null, olt_id: int }
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        if str(olt.snmp_version).lower() != 'v2c':
            detail = 'SNMP v3 is not yet supported by backend runtime credentials.'
            mark_olt_unreachable(olt, error=detail)
            return Response(
                {
                    'reachable': False,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'detail': detail,
                    'failure_count': olt.snmp_failure_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        sys_descr_oid = '1.3.6.1.2.1.1.1.0'
        has_running_job = maintenance_job_service.has_active_job(olt.id)
        try:
            result = snmp_service.get(olt, [sys_descr_oid])
            if result and sys_descr_oid in result:
                mark_olt_reachable(olt)
                return Response(
                    {
                        'reachable': True,
                        'sys_descr': str(result[sys_descr_oid]) if result[sys_descr_oid] is not None else None,
                        'olt_id': olt.id,
                        'failure_count': olt.snmp_failure_count,
                    }
                )
            if has_running_job:
                return Response({
                    'reachable': True,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'failure_count': olt.snmp_failure_count,
                    'busy': True,
                })
            mark_olt_unreachable(olt, error='No sysDescr response')
            return Response({'reachable': False, 'sys_descr': None, 'olt_id': olt.id, 'failure_count': olt.snmp_failure_count})
        except Exception as exc:
            if has_running_job:
                logger.info("SNMP check for OLT %s timed out during active maintenance job; treating as busy.", olt.name)
                return Response({
                    'reachable': True,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'failure_count': olt.snmp_failure_count,
                    'busy': True,
                })
            logger.warning("SNMP check failed for OLT %s: %s", olt.name, exc)
            mark_olt_unreachable(olt, error=str(exc))
            return Response(
                {
                    'reachable': False,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'detail': str(exc),
                    'failure_count': olt.snmp_failure_count,
                }
            )

    @action(detail=True, methods=['post'])
    def run_polling(self, request, pk=None):
        """
        Run ONU status polling immediately for one OLT.
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_onu_status',
            required_template_paths=[('status', 'onu_status_oid')],
            action_name='ONU status polling',
        )
        if validation_error is not None:
            return validation_error

        if _is_true(request.data.get('background', 'false')):
            return self._enqueue_maintenance_job(
                olt=olt,
                kind='polling',
                request=request,
            )

        output = StringIO()
        try:
            call_command('poll_onu_status', olt_id=olt.id, force=True, stdout=output)
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({'status': 'completed', 'olt_id': olt.id, 'output': output.getvalue().strip()})


class ONUViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for ONUs (read-only)
    """

    serializer_class = ONUSerializer
    filterset_fields = ['olt', 'status', 'slot_id', 'pon_id', 'slot_ref', 'pon_ref', 'is_active']

    def _has_usable_status_snapshot(self, olt: OLT) -> bool:
        return has_usable_status_snapshot(olt)

    def _ensure_status_snapshot_for_power(self, olt: OLT):
        ensure_status_snapshot_for_power(olt)

    def _resolve_onu_batch_selection(self, request):
        onu_ids = request.data.get('onu_ids') or []
        olt_id = request.data.get('olt_id')
        slot_id = request.data.get('slot_id')
        pon_id = request.data.get('pon_id')

        queryset = ONU.objects.filter(is_active=True).select_related('olt', 'olt__vendor_profile')
        selection_scope = None

        if isinstance(onu_ids, list) and onu_ids:
            try:
                parsed_onu_ids = [int(value) for value in onu_ids]
            except (TypeError, ValueError):
                return None, None, Response(
                    {'detail': 'onu_ids must contain only integers.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(id__in=parsed_onu_ids)
            selection_scope = {'mode': 'onu_ids'}
        elif olt_id is not None and slot_id is not None and pon_id is not None:
            try:
                parsed_olt_id = int(olt_id)
                parsed_slot_id = int(slot_id)
                parsed_pon_id = int(pon_id)
            except (TypeError, ValueError):
                return None, None, Response(
                    {'detail': 'olt_id, slot_id and pon_id must be integers.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(
                olt_id=parsed_olt_id,
                slot_id=parsed_slot_id,
                pon_id=parsed_pon_id,
            )
            selection_scope = {
                'mode': 'pon',
                'olt_id': parsed_olt_id,
                'slot_id': parsed_slot_id,
                'pon_id': parsed_pon_id,
            }
        else:
            return None, None, Response(
                {'detail': 'Provide onu_ids or (olt_id + slot_id + pon_id).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        onus = list(queryset.order_by('slot_id', 'pon_id', 'onu_id'))
        return onus, selection_scope, None

    def _run_scoped_status_refresh(self, onus, selection_scope):
        if not onus:
            return

        by_olt = defaultdict(list)
        for onu in onus:
            by_olt[onu.olt_id].append(onu)

        for olt_onus in by_olt.values():
            olt = olt_onus[0].olt
            status_templates = (olt.vendor_profile.oid_templates or {}).get('status', {})
            status_oid = status_templates.get('onu_status_oid')
            if not olt.vendor_profile.supports_onu_status or not status_oid:
                logger.warning(
                    "Scoped status refresh OLT %s skipped: status polling capability unavailable.",
                    olt.id,
                )
                continue

            kwargs = {
                'olt_id': olt.id,
                'force': True,
                'stdout': StringIO(),
            }
            if selection_scope.get('mode') == 'pon' and int(selection_scope.get('olt_id')) == int(olt.id):
                kwargs['slot_id'] = int(selection_scope.get('slot_id'))
                kwargs['pon_id'] = int(selection_scope.get('pon_id'))
            else:
                kwargs['onu_id'] = [int(onu.id) for onu in olt_onus]

            call_command('poll_onu_status', **kwargs)

    def _serialize_status_rows(self, onus):
        if not onus:
            return []

        onu_ids = [int(onu.id) for onu in onus]
        active_logs = {}
        for log in ONULog.objects.filter(
            onu_id__in=onu_ids,
            offline_until__isnull=True,
        ).order_by('-offline_since'):
            active_logs.setdefault(int(log.onu_id), log)

        rows = []
        for onu in onus:
            active_log = active_logs.get(int(onu.id))
            if active_log:
                disconnect_reason = active_log.disconnect_reason
                offline_since = active_log.offline_since.isoformat() if active_log.offline_since else None
                disconnect_window_start = (
                    active_log.disconnect_window_start.isoformat()
                    if active_log.disconnect_window_start else None
                )
                disconnect_window_end = (
                    active_log.disconnect_window_end.isoformat()
                    if active_log.disconnect_window_end else None
                )
            else:
                disconnect_reason = None if onu.status == ONU.STATUS_ONLINE else ONULog.REASON_UNKNOWN
                offline_since = None
                disconnect_window_start = None
                disconnect_window_end = None

            rows.append(
                {
                    'id': onu.id,
                    'olt_id': onu.olt_id,
                    'slot_id': onu.slot_id,
                    'pon_id': onu.pon_id,
                    'onu_number': onu.onu_id,
                    'status': onu.status,
                    'disconnect_reason': disconnect_reason,
                    'offline_since': offline_since,
                    'disconnect_window_start': disconnect_window_start,
                    'disconnect_window_end': disconnect_window_end,
                }
            )
        return rows

    def get_queryset(self):
        include_inactive = _is_true(self.request.query_params.get('include_inactive', 'false'))
        queryset = ONU.objects.select_related('olt', 'olt__vendor_profile', 'slot_ref', 'pon_ref')
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            if status_filter == 'offline':
                queryset = queryset.exclude(status=ONU.STATUS_ONLINE)
            else:
                queryset = queryset.filter(status=status_filter)

        return queryset.order_by('olt', 'slot_id', 'pon_id', 'onu_id')

    @action(detail=True, methods=['get'])
    def power(self, request, pk=None):
        """
        Returns power information for one ONU.
        Query param refresh=true/false controls SNMP refresh vs cache read.
        """
        onu = self.get_object()
        refresh = _is_true(request.query_params.get('refresh', 'false'))
        if refresh:
            self._ensure_status_snapshot_for_power(onu.olt)
        result_map = power_service.refresh_for_onus([onu], force_refresh=refresh)
        data = result_map.get(
            onu.id,
            {
                'onu_id': onu.id,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'onu_rx_power': None,
                'olt_rx_power': None,
                'power_read_at': None,
            },
        )
        return Response(data)

    @action(detail=True, methods=['post'], url_path='refresh-status')
    def refresh_status(self, request, pk=None):
        """
        Returns status information for one ONU.
        Body/query option: refresh=true/false controls SNMP refresh vs cached DB/log read.
        """
        onu = self.get_object()
        refresh = _is_true(request.data.get('refresh', request.query_params.get('refresh', 'false')))

        if refresh:
            self._run_scoped_status_refresh([onu], {'mode': 'onu_ids'})
            onu.refresh_from_db(fields=['status'])

        payload = self._serialize_status_rows([onu])[0]
        return Response(payload)

    @action(detail=False, methods=['post'], url_path='batch-status')
    def batch_status(self, request):
        """
        Returns status information for multiple ONUs.
        Body options:
        - onu_ids: [1,2,3]
        - or olt_id + slot_id + pon_id to refresh one PON quickly
        """
        refresh = _is_true(request.data.get('refresh', False))

        onus, selection_scope, error_response = self._resolve_onu_batch_selection(request)
        if error_response is not None:
            return error_response
        if not onus:
            return Response({'count': 0, 'results': []})

        if refresh:
            self._run_scoped_status_refresh(onus, selection_scope)
            refreshed_ids = [int(onu.id) for onu in onus]
            onus = list(
                ONU.objects.filter(id__in=refreshed_ids, is_active=True)
                .select_related('olt', 'olt__vendor_profile')
                .order_by('slot_id', 'pon_id', 'onu_id')
            )

        results = self._serialize_status_rows(onus)
        return Response({'count': len(results), 'results': results})

    @action(detail=False, methods=['post'], url_path='batch-power')
    def batch_power(self, request):
        """
        Returns power information for multiple ONUs.
        Body options:
        - onu_ids: [1,2,3]
        - or olt_id + slot_id + pon_id to refresh one PON quickly
        """
        refresh = _is_true(request.data.get('refresh', False))
        onus, selection_scope, error_response = self._resolve_onu_batch_selection(request)
        if error_response is not None:
            return error_response
        if not onus:
            return Response({'count': 0, 'results': []})

        if refresh:
            refreshable_olts = {}
            for onu in onus:
                if onu.olt_id in refreshable_olts:
                    continue
                refreshable_olts[onu.olt_id] = not self._has_usable_status_snapshot(onu.olt)

            onus_requiring_status_refresh = [
                onu for onu in onus
                if refreshable_olts.get(onu.olt_id)
            ]
            if onus_requiring_status_refresh:
                self._run_scoped_status_refresh(onus_requiring_status_refresh, selection_scope)

        result_map = power_service.refresh_for_onus(onus, force_refresh=refresh)
        results = [result_map.get(onu.id, {'onu_id': onu.id}) for onu in onus]
        return Response({'count': len(results), 'results': results})


class OLTSlotViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Slots (read-only)
    """

    queryset = OLTSlot.objects.filter(olt__is_active=True, is_active=True).select_related('olt')
    serializer_class = OLTSlotSerializer
    filterset_fields = ['olt', 'slot_id', 'rack_id', 'shelf_id', 'is_active']


class OLTPONViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PONs (read + partial update)
    """

    http_method_names = ['get', 'head', 'options', 'patch']
    queryset = OLTPON.objects.filter(olt__is_active=True, is_active=True).select_related('olt', 'slot')
    serializer_class = OLTPONSerializer
    filterset_fields = ['olt', 'slot', 'pon_id', 'pon_key', 'is_active']

    def perform_update(self, serializer):
        pon = serializer.save()
        cache_service.invalidate_topology_api_cache(pon.olt_id)

    def partial_update(self, request, *args, **kwargs):
        if not can_modify_settings(request.user):
            return _settings_forbidden_response()
        return super().partial_update(request, *args, **kwargs)
