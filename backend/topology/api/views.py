import logging
from collections import defaultdict
import datetime as _dt
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.db.models import Q
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
from topology.api.auth_utils import can_modify_settings, can_operate_topology
from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, ONUPowerSample, VendorProfile
from topology.services.cache_service import cache_service
from topology.services.collector_service import check_olt_reachability, collector_name_for_olt
from topology.services.fit_collector_service import FITCollectorError
from topology.services.history_service import (
    get_latest_power_snapshot_map,
    persist_power_samples,
    sync_latest_power_snapshots,
)
from topology.services.maintenance_job_service import maintenance_job_service
from topology.services.maintenance_runtime import (
    collect_power_for_olt,
    ensure_status_snapshot_for_power,
    get_power_history_max_age_minutes,
    has_usable_status_snapshot,
)
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.power_values import normalize_power_value
from topology.services.power_service import power_service
from topology.services.topology_service import TopologyService
from topology.services.unm_service import UNMServiceError, unm_service
from topology.services.vendor_profile import (
    COLLECTOR_TYPE_FIT_TELNET,
    display_onu_serial,
    get_collector_type,
    map_disconnect_reason,
    map_status_code,
    supports_olt_rx_power,
)
from topology.services.zabbix_service import zabbix_service

logger = logging.getLogger(__name__)
_COMMAND_FAILURE_MARKERS = (
    ' failed ',
    ' failed(',
    ' failed:',
    'collector unreachable',
    'no status data returned',
    'only stale status data returned',
    'no parseable onu entries',
)


def _is_true(value: str | None) -> bool:
    return str(value or '').lower() in {'1', 'true', 'yes', 'on'}


def _settings_forbidden_response():
    return Response(
        {'detail': 'Insufficient permissions for this action.'},
        status=status.HTTP_403_FORBIDDEN,
    )


def _serialize_disconnect_timestamp_for_olt(olt, value):
    if not value:
        return None
    if not getattr(olt, 'unm_enabled', False):
        return value.isoformat() if hasattr(value, 'isoformat') else str(value)
    try:
        if not unm_service.is_enabled_for_olt(olt):
            return value.isoformat() if hasattr(value, 'isoformat') else str(value)
        localized = unm_service.localize_alarm_datetime(olt=olt, value=value)
    except UNMServiceError:
        return value.isoformat() if hasattr(value, 'isoformat') else str(value)
    return localized.isoformat() if localized else None


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
        queryset = (
            OLT.objects.filter(is_active=True)
            .select_related('vendor_profile')
            .order_by('id')
        )
        return queryset

    def get_serializer_class(self):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))
        if include_topology and self.action == 'list':
            return OLTTopologySerializer
        return OLTSerializer

    def list(self, request, *args, **kwargs):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)

        if include_topology:
            payload = TopologyService().build_topology_rows(rows)
            if page is not None:
                return self.get_paginated_response(payload)
            return Response(payload)

        serializer = self.get_serializer(rows, many=True, context=self.get_serializer_context())
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def perform_create(self, serializer):
        olt = serializer.save()
        self._sync_zabbix_host_runtime(olt)
        cache_service.invalidate_topology_structure_cache(olt.id)

    def _sync_zabbix_host_runtime(self, olt, *, previous=None):
        if get_collector_type(olt) != 'zabbix':
            return
        try:
            synced = zabbix_service.sync_olt_host_runtime(olt, previous=previous)
        except Exception:
            logger.exception(
                "Failed to sync Zabbix host runtime for OLT id=%s name=%s",
                olt.id,
                olt.name,
            )
            return
        if not synced:
            logger.warning(
                "Skipped Zabbix host runtime sync because host was not resolved for OLT id=%s name=%s",
                olt.id,
                olt.name,
            )

    def perform_update(self, serializer):
        tracked_fields = {
            'name',
            'vendor_profile_id',
            'protocol',
            'ip_address',
            'snmp_port',
            'snmp_community',
            'snmp_version',
            'telnet_username',
            'unm_enabled',
            'unm_host',
            'unm_port',
            'unm_username',
            'unm_mneid',
            'discovery_interval_minutes',
            'polling_interval_seconds',
            'power_interval_seconds',
            'history_days',
            'discovery_enabled',
            'polling_enabled',
        }
        current = serializer.instance
        before = {field: getattr(current, field) for field in tracked_fields}
        olt = serializer.save()
        self._sync_zabbix_host_runtime(olt, previous=before)
        cache_service.invalidate_topology_structure_cache(olt.id)

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

        zabbix_snapshot = {
            'name': olt.name,
            'ip_address': olt.ip_address,
        }
        if get_collector_type(olt) == 'zabbix':
            try:
                zabbix_service.delete_olt_host(olt, previous=zabbix_snapshot)
            except Exception:
                logger.exception(
                    "Failed to delete Zabbix host for OLT id=%s name=%s",
                    olt.id,
                    olt.name,
                )

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

        cache_service.invalidate_topology_structure_cache(olt.id)
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
        if get_collector_type(olt) == 'zabbix':
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

    def _collect_power_for_olt(
        self,
        olt: OLT,
        *,
        force_refresh=True,
        include_results=True,
        refresh_upstream=False,
    ):
        return collect_power_for_olt(
            olt,
            force_refresh=force_refresh,
            include_results=include_results,
            refresh_upstream=refresh_upstream,
            force_upstream=refresh_upstream,
        )

    def _serialize_maintenance_job(self, job):
        return maintenance_job_service.serialize_job(job)

    @staticmethod
    def _command_output_indicates_failure(output: str) -> bool:
        normalized = f" {str(output or '').strip().lower()} "
        return any(marker in normalized for marker in _COMMAND_FAILURE_MARKERS)

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
        service = TopologyService()
        topology = service.build_topology(olt)
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
            required_template_paths=[('zabbix', 'onu_rx_item_key_pattern')],
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

        try:
            payload = self._collect_power_for_olt(
                olt,
                force_refresh=True,
                include_results=True,
                refresh_upstream=True,
            )
        except FITCollectorError as exc:
            mark_olt_unreachable(olt, error=str(exc))
            return Response(
                {'detail': f"{olt.name}: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
        total_skipped_unsupported_count = 0
        total_collected_count = 0
        completed_count = 0
        skipped_count = 0
        error_count = 0

        for olt in olts:
            validation_error = self._validate_vendor_action(
                olt,
                capability_field='supports_power_monitoring',
                required_template_paths=[('zabbix', 'onu_rx_item_key_pattern')],
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
                    refresh_upstream=force_refresh,
                )
                completed_count += 1
                total_onu_count += payload['count']
                total_attempted_count += payload.get('attempted_count', payload['count'])
                total_skipped_not_online_count += payload.get('skipped_not_online_count', 0)
                total_skipped_offline_count += payload.get('skipped_offline_count', 0)
                total_skipped_unknown_count += payload.get('skipped_unknown_count', 0)
                total_skipped_unsupported_count += payload.get('skipped_unsupported_count', 0)
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
                'total_skipped_unsupported_count': total_skipped_unsupported_count,
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
            required_template_paths=[('zabbix', 'discovery_item_key')],
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
            call_command(
                'discover_onus',
                olt_id=olt.id,
                force=True,
                refresh_upstream=True,
                stdout=output,
            )
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        olt.refresh_from_db(fields=['discovery_healthy', 'last_collector_error'])
        output_text = output.getvalue().strip()
        if olt.discovery_healthy is False:
            return Response(
                {
                    'status': 'error',
                    'olt_id': olt.id,
                    'detail': output_text or olt.last_collector_error or 'Discovery failed.',
                    'output': output_text,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({'status': 'completed', 'olt_id': olt.id, 'output': output_text})

    def _collector_check(self, request, pk=None):
        """
        Quick collector connectivity check for the configured OLT transport.
        Returns { reachable: bool, sys_descr: null, olt_id: int }
        """
        access_error = self._ensure_settings_write_access(request)
        if access_error is not None:
            return access_error

        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        collector_name = collector_name_for_olt(olt)
        try:
            reachable, detail = check_olt_reachability(olt)
            if reachable:
                mark_olt_reachable(olt)
                return Response(
                    {
                        'reachable': True,
                        'sys_descr': None,
                        'olt_id': olt.id,
                        'failure_count': olt.collector_failure_count,
                        'collector': collector_name,
                    }
                )
            mark_olt_unreachable(olt, error=detail or 'Collector reported OLT unreachable')
            return Response(
                {
                    'reachable': False,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'detail': detail or 'Collector reported OLT unreachable',
                    'failure_count': olt.collector_failure_count,
                    'collector': collector_name,
                }
            )
        except Exception as exc:
            logger.warning("Collector reachability check failed for OLT %s: %s", olt.name, exc)
            mark_olt_unreachable(olt, error=str(exc))
            return Response(
                {
                    'reachable': False,
                    'sys_descr': None,
                    'olt_id': olt.id,
                    'detail': str(exc),
                    'failure_count': olt.collector_failure_count,
                    'collector': collector_name,
                }
            )

    @action(detail=True, methods=['post'], url_path='collector_check')
    def collector_check(self, request, pk=None):
        return self._collector_check(request, pk=pk)

    @action(detail=True, methods=['post'], url_path='snmp_check')
    def snmp_check(self, request, pk=None):
        return self._collector_check(request, pk=pk)

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
            required_template_paths=[('zabbix', 'status_item_key_pattern')],
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
            call_command(
                'poll_onu_status',
                olt_id=olt.id,
                force=True,
                refresh_upstream=True,
                force_upstream=True,
                stdout=output,
            )
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        olt.refresh_from_db(fields=['collector_reachable', 'last_collector_error'])
        output_text = output.getvalue().strip()
        if self._command_output_indicates_failure(output_text) or olt.collector_reachable is False:
            return Response(
                {
                    'status': 'error',
                    'olt_id': olt.id,
                    'detail': output_text or olt.last_collector_error or 'Polling failed.',
                    'output': output_text,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({'status': 'completed', 'olt_id': olt.id, 'output': output_text})


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

        collector_errors = []
        for olt_onus in by_olt.values():
            olt = olt_onus[0].olt
            if not olt.vendor_profile.supports_onu_status:
                logger.warning(
                    "Scoped status refresh OLT %s skipped: status polling capability unavailable.",
                    olt.id,
                )
                continue

            kwargs = {
                'olt_id': olt.id,
                'force': True,
                'refresh_upstream': True,
                'force_upstream': True,
                'stdout': StringIO(),
            }
            if selection_scope.get('mode') == 'pon' and int(selection_scope.get('olt_id')) == int(olt.id):
                kwargs['slot_id'] = int(selection_scope.get('slot_id'))
                kwargs['pon_id'] = int(selection_scope.get('pon_id'))
            else:
                kwargs['onu_id'] = [int(onu.id) for onu in olt_onus]

            call_command('poll_onu_status', **kwargs)
            olt.refresh_from_db(fields=['collector_reachable', 'collector_failure_count', 'last_collector_error'])
            if olt.collector_reachable is False:
                detail = str(olt.last_collector_error or 'Collector reported OLT unreachable').strip()
                collector_errors.append(f"{olt.name}: {detail}")

        if collector_errors:
            raise RuntimeError("; ".join(collector_errors))

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
                offline_since = _serialize_disconnect_timestamp_for_olt(onu.olt, active_log.offline_since)
                window_anchor = (
                    active_log.disconnect_window_end
                    or active_log.disconnect_window_start
                    or active_log.offline_since
                )
                disconnect_window_start = (
                    _serialize_disconnect_timestamp_for_olt(onu.olt, active_log.disconnect_window_start or window_anchor)
                    if (active_log.disconnect_window_start or window_anchor) else None
                )
                disconnect_window_end = (
                    _serialize_disconnect_timestamp_for_olt(onu.olt, active_log.disconnect_window_end or window_anchor)
                    if (active_log.disconnect_window_end or window_anchor) else None
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

    def _serialize_power_row(self, onu, payload=None):
        row = payload or {}
        onu_rx_power = normalize_power_value(row.get('onu_rx_power'))
        olt_rx_power = (
            normalize_power_value(row.get('olt_rx_power'))
            if supports_olt_rx_power(onu.olt) else None
        )
        power_read_at = row.get('power_read_at') if (onu_rx_power is not None or olt_rx_power is not None) else None
        return {
            'onu_id': onu.id,
            'slot_id': onu.slot_id,
            'pon_id': onu.pon_id,
            'onu_number': onu.onu_id,
            'onu_rx_power': onu_rx_power,
            'olt_rx_power': olt_rx_power,
            'power_read_at': power_read_at,
        }

    @staticmethod
    def _latest_power_reads_use_zabbix() -> bool:
        # Production-grade latest-power reads should automatically prefer the
        # read-only Zabbix DB path whenever that alias is enabled. The legacy
        # feature flag remains as an explicit opt-in for non-DB environments.
        return bool(
            getattr(settings, 'ZABBIX_DB_ENABLED', False)
            or getattr(settings, 'POWER_LATEST_READS_USE_ZABBIX', False)
        )

    def _read_latest_power_rows(self, onus):
        normalized_onus = []
        seen = set()
        for onu in onus or []:
            if not onu:
                continue
            onu_id = int(onu.id)
            if onu_id in seen:
                continue
            seen.add(onu_id)
            normalized_onus.append(onu)

        if not normalized_onus:
            return {}

        snapshot_map = get_latest_power_snapshot_map([onu.id for onu in normalized_onus])
        if not self._latest_power_reads_use_zabbix():
            return snapshot_map

        results = {}
        grouped = defaultdict(list)
        for onu in normalized_onus:
            grouped[int(onu.olt_id)].append(onu)

        for olt_onus in grouped.values():
            olt = olt_onus[0].olt
            if get_collector_type(olt) == COLLECTOR_TYPE_FIT_TELNET:
                for onu in olt_onus:
                    payload = snapshot_map.get(int(onu.id))
                    if payload:
                        results[int(onu.id)] = payload
                continue

            onu_pattern, olt_pattern = power_service._resolve_zabbix_power_patterns(olt)
            if not onu_pattern:
                for onu in olt_onus:
                    payload = snapshot_map.get(int(onu.id))
                    if payload:
                        results[int(onu.id)] = payload
                continue

            indexes = []
            index_to_onu_ids = defaultdict(list)
            for onu in olt_onus:
                index = str(getattr(onu, 'snmp_index', '') or '').strip('.')
                if not index:
                    continue
                if index not in index_to_onu_ids:
                    indexes.append(index)
                index_to_onu_ids[index].append(int(onu.id))

            if not indexes:
                for onu in olt_onus:
                    payload = snapshot_map.get(int(onu.id))
                    if payload:
                        results[int(onu.id)] = payload
                continue

            history_fallback_max_items = max(
                int(getattr(settings, 'POWER_LATEST_READS_HISTORY_FALLBACK_MAX_ITEMS', 256) or 0),
                0,
            )
            use_history_fallback = history_fallback_max_items > 0 and len(indexes) <= history_fallback_max_items
            if not use_history_fallback:
                logger.info(
                    "Latest power live read OLT %s: skipping history fallback for %s ONU indexes (max=%s).",
                    olt.id,
                    len(indexes),
                    history_fallback_max_items,
                )

            try:
                live_map, _ = zabbix_service.fetch_power_by_index(
                    olt,
                    indexes,
                    onu_rx_item_key_pattern=onu_pattern,
                    olt_rx_item_key_pattern=olt_pattern,
                    history_fallback=use_history_fallback,
                )
            except Exception:
                logger.exception("Failed to read latest Zabbix power for olt_id=%s; falling back to snapshot.", olt.id)
                for onu in olt_onus:
                    payload = snapshot_map.get(int(onu.id))
                    if payload:
                        results[int(onu.id)] = payload
                continue

            for index, onu_ids in index_to_onu_ids.items():
                payload = live_map.get(index)
                has_live_power = (
                    payload
                    and (
                        normalize_power_value(payload.get('onu_rx_power')) is not None
                        or normalize_power_value(payload.get('olt_rx_power')) is not None
                    )
                )
                if has_live_power:
                    for onu_id in onu_ids:
                        results[onu_id] = payload
                else:
                    # Fall back to snapshot when Zabbix has no usable power data
                    for onu_id in onu_ids:
                        fallback = snapshot_map.get(onu_id)
                        if fallback:
                            results[onu_id] = fallback

        return results

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

    @staticmethod
    def _positive_int(value, *, default: int, minimum: int = 1, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default)
        if parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

    @staticmethod
    def _downsample_samples(samples, *, max_points: int = 240):
        if len(samples) <= max_points:
            return samples
        if max_points <= 1:
            return [samples[-1]]

        last_index = len(samples) - 1
        step = last_index / float(max_points - 1)
        selected = []
        seen = set()
        for i in range(max_points):
            index = int(round(i * step))
            if index in seen:
                continue
            seen.add(index)
            selected.append(samples[index])
        if selected[0] is not samples[0]:
            selected[0] = samples[0]
        if selected[-1] is not samples[-1]:
            selected[-1] = samples[-1]
        return selected

    @action(detail=False, methods=['get'], url_path='power-report')
    def power_report(self, request):
        search = str(request.query_params.get('search') or '').strip()
        queryset = ONU.objects.filter(is_active=True, olt__is_active=True).select_related('olt')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(serial__icontains=search)
                | Q(olt__name__icontains=search)
            )

        rows_source = list(queryset.order_by('olt__name', 'slot_id', 'pon_id', 'onu_id'))
        power_map = self._read_latest_power_rows(rows_source)
        rows = []
        for onu in rows_source:
            power_row = self._serialize_power_row(onu, power_map.get(int(onu.id)))

            rows.append({
                'id': onu.id,
                'olt_id': onu.olt_id,
                'olt_name': onu.olt.name,
                'power_interval_seconds': onu.olt.power_interval_seconds,
                'slot_id': onu.slot_id,
                'slot_ref_id': onu.slot_ref_id,
                'pon_id': onu.pon_id,
                'pon_ref_id': onu.pon_ref_id,
                'onu_number': onu.onu_id,
                'client_name': onu.name or '',
                'serial': display_onu_serial(onu.olt, onu.serial),
                'status': onu.status,
                'onu_rx_power': power_row.get('onu_rx_power'),
                'olt_rx_power': power_row.get('olt_rx_power'),
                'power_read_at': power_row.get('power_read_at'),
            })
        return Response({'count': len(rows), 'results': rows})

    @staticmethod
    def _parse_epoch(value):
        try:
            if value in (None, ''):
                return None
            parsed = int(str(value).strip())
            if parsed <= 0:
                return None
            return parsed
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _epoch_to_iso(epoch):
        parsed = ONUViewSet._parse_epoch(epoch)
        if parsed is None:
            return None
        try:
            return _dt.datetime.fromtimestamp(parsed, tz=_dt.timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _normalize_reason_value(raw_reason, disconnect_reason_map):
        value = str(raw_reason or '').strip().lower()
        if value in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP, ONULog.REASON_UNKNOWN):
            return value
        if not value:
            return ONULog.REASON_UNKNOWN
        return map_disconnect_reason(value, disconnect_reason_map or {})

    @staticmethod
    def _normalize_status_value(raw_status, status_map):
        value = str(raw_status or '').strip().lower()
        if value == ONU.STATUS_ONLINE:
            return {'status': ONU.STATUS_ONLINE, 'reason': ''}
        if value == ONU.STATUS_OFFLINE:
            return {'status': ONU.STATUS_OFFLINE, 'reason': ONULog.REASON_UNKNOWN}
        if value == ONU.STATUS_UNKNOWN:
            return {'status': ONU.STATUS_UNKNOWN, 'reason': ONULog.REASON_UNKNOWN}
        if value in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP):
            return {'status': ONU.STATUS_OFFLINE, 'reason': value}

        mapped = map_status_code(str(raw_status or ''), status_map or {})
        mapped_status = mapped.get('status', ONU.STATUS_UNKNOWN)
        mapped_reason = mapped.get('reason', ONULog.REASON_UNKNOWN)
        if mapped_status == ONU.STATUS_ONLINE:
            mapped_reason = ''
        elif mapped_reason not in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP):
            mapped_reason = ONULog.REASON_UNKNOWN
        return {'status': mapped_status, 'reason': mapped_reason}

    def _build_zabbix_alarm_rows(
        self,
        *,
        onu,
        status_samples,
        previous_status_sample,
        reason_samples,
        alarm_cutoff,
        alarm_end,
        alarm_limit,
        status_map,
        disconnect_reason_map,
    ):
        normalized_reason_samples = []
        for sample in reason_samples or []:
            epoch = self._parse_epoch((sample or {}).get('clock_epoch'))
            if epoch is None:
                continue
            normalized_reason_samples.append(
                {
                    'clock_epoch': epoch,
                    'reason': self._normalize_reason_value((sample or {}).get('value'), disconnect_reason_map),
                }
            )
        normalized_reason_samples.sort(key=lambda row: row.get('clock_epoch') or 0)

        timeline = []
        reason_cursor = 0
        current_reason = ONULog.REASON_UNKNOWN
        for sample in (status_samples or []):
            epoch = self._parse_epoch((sample or {}).get('clock_epoch'))
            if epoch is None:
                continue

            while (
                reason_cursor < len(normalized_reason_samples)
                and normalized_reason_samples[reason_cursor].get('clock_epoch', 0) <= epoch
            ):
                current_reason = normalized_reason_samples[reason_cursor].get('reason') or ONULog.REASON_UNKNOWN
                reason_cursor += 1

            mapped = self._normalize_status_value((sample or {}).get('value'), status_map)
            status_value = mapped.get('status', ONU.STATUS_UNKNOWN)
            reason_value = mapped.get('reason', ONULog.REASON_UNKNOWN)
            if status_value == ONU.STATUS_ONLINE:
                reason_value = ''
            elif reason_value == ONULog.REASON_UNKNOWN and current_reason:
                reason_value = current_reason

            timeline.append(
                {
                    'clock_epoch': epoch,
                    'clock': self._epoch_to_iso(epoch),
                    'status': status_value,
                    'reason': reason_value,
                }
            )
        timeline.sort(key=lambda row: row.get('clock_epoch') or 0)

        previous = None
        if previous_status_sample:
            prev_epoch = self._parse_epoch((previous_status_sample or {}).get('clock_epoch'))
            if prev_epoch is not None:
                previous_mapped = self._normalize_status_value(
                    (previous_status_sample or {}).get('value'),
                    status_map,
                )
                previous = {
                    'clock_epoch': prev_epoch,
                    'status': previous_mapped.get('status', ONU.STATUS_UNKNOWN),
                    'reason': previous_mapped.get('reason', ONULog.REASON_UNKNOWN),
                }

        max_gap_seconds = max(
            int(onu.olt.polling_interval_seconds or 0) * 2
            + max(int(getattr(settings, 'ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS', 90) or 90), 0),
            1,
        )
        cutoff_iso = alarm_cutoff.isoformat()
        end_epoch = int(alarm_end.timestamp())

        alarms = []
        open_alarm = None
        sequence = 0

        def start_alarm(*, start_iso, reason, window_start_iso, window_end_iso):
            nonlocal sequence
            sequence += 1
            return {
                'id': f"zbx-{onu.id}-{sequence}",
                'event_type': reason or ONULog.REASON_UNKNOWN,
                'start_at': start_iso,
                'end_at': None,
                'status': 'active',
                'duration_seconds': None,
                'disconnect_window_start': window_start_iso,
                'disconnect_window_end': window_end_iso,
            }

        if previous and previous.get('status') == ONU.STATUS_OFFLINE:
            previous_reason = previous.get('reason') or ONULog.REASON_UNKNOWN
            open_alarm = start_alarm(
                start_iso=cutoff_iso,
                reason=previous_reason,
                window_start_iso=cutoff_iso,
                window_end_iso=cutoff_iso,
            )

        for sample in timeline:
            sample_status = sample.get('status')
            sample_reason = sample.get('reason') or ONULog.REASON_UNKNOWN
            sample_iso = sample.get('clock')
            sample_epoch = self._parse_epoch(sample.get('clock_epoch'))
            if sample_iso is None or sample_epoch is None:
                previous = sample
                continue

            if sample_status == ONU.STATUS_OFFLINE:
                if open_alarm is None:
                    window_start_iso = sample_iso
                    window_end_iso = sample_iso
                    prev_epoch = self._parse_epoch((previous or {}).get('clock_epoch'))
                    prev_status = (previous or {}).get('status')
                    if (
                        prev_epoch is not None
                        and prev_status == ONU.STATUS_ONLINE
                        and 0 < (sample_epoch - prev_epoch) <= max_gap_seconds
                    ):
                        prev_iso = self._epoch_to_iso(prev_epoch)
                        if prev_iso:
                            window_start_iso = prev_iso
                        window_end_iso = sample_iso

                    open_alarm = start_alarm(
                        start_iso=sample_iso,
                        reason=sample_reason,
                        window_start_iso=window_start_iso,
                        window_end_iso=window_end_iso,
                    )
                else:
                    if (
                        open_alarm.get('event_type') == ONULog.REASON_UNKNOWN
                        and sample_reason in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP)
                    ):
                        open_alarm['event_type'] = sample_reason
            elif sample_status == ONU.STATUS_ONLINE and open_alarm is not None:
                open_alarm['end_at'] = sample_iso
                open_alarm['status'] = 'resolved'
                start_at = open_alarm.get('start_at')
                if start_at:
                    start_dt = _dt.datetime.fromisoformat(start_at)
                    end_dt = _dt.datetime.fromisoformat(sample_iso)
                    duration = int((end_dt - start_dt).total_seconds())
                    open_alarm['duration_seconds'] = max(duration, 0)
                alarms.append(open_alarm)
                open_alarm = None

            previous = sample

        if open_alarm is not None:
            start_iso = open_alarm.get('start_at')
            if start_iso:
                start_dt = _dt.datetime.fromisoformat(start_iso)
                end_dt = _dt.datetime.fromtimestamp(end_epoch, tz=_dt.timezone.utc)
                duration = int((end_dt - start_dt).total_seconds())
                open_alarm['duration_seconds'] = max(duration, 0)
            alarms.append(open_alarm)

        alarms.sort(key=lambda row: row.get('start_at') or '', reverse=True)
        if alarm_limit > 0:
            alarms = alarms[:alarm_limit]

        stats = {
            'total': 0,
            'link_loss': 0,
            'dying_gasp': 0,
            'unknown': 0,
            'active': 0,
            'resolved': 0,
        }
        for alarm in alarms:
            reason = alarm.get('event_type') or ONULog.REASON_UNKNOWN
            if reason not in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP):
                reason = ONULog.REASON_UNKNOWN
            status_value = 'active' if alarm.get('status') == 'active' else 'resolved'
            stats['total'] += 1
            stats[reason] += 1
            stats[status_value] += 1
            alarm['event_type'] = reason
            alarm['status'] = status_value

        return alarms, stats

    def _build_zabbix_power_history(
        self,
        *,
        onu_rx_samples,
        olt_rx_samples,
        max_power_points,
        merge_window_seconds: int = 15,
    ):
        window = max(1, int(merge_window_seconds or 1))
        merged_rows = []

        def _upsert(sample, *, field_name: str, field_epoch_key: str):
            epoch = self._parse_epoch((sample or {}).get('clock_epoch'))
            if epoch is None:
                return
            value = normalize_power_value((sample or {}).get('value'))
            if value is None:
                return

            target = None
            best_delta = None
            for row in merged_rows:
                row_epoch = self._parse_epoch(row.get('clock_epoch'))
                if row_epoch is None:
                    continue
                delta = abs(epoch - row_epoch)
                if delta > window:
                    continue
                if best_delta is None or delta < best_delta:
                    target = row
                    best_delta = delta

            if target is None:
                target = {
                    'clock_epoch': epoch,
                    'timestamp': self._epoch_to_iso(epoch),
                    'onu_rx_power': None,
                    'olt_rx_power': None,
                    'onu_rx_epoch': None,
                    'olt_rx_epoch': None,
                }
                merged_rows.append(target)

            previous_field_epoch = self._parse_epoch(target.get(field_epoch_key))
            if previous_field_epoch is None or epoch >= previous_field_epoch:
                target[field_name] = value
                target[field_epoch_key] = epoch

            row_epoch = self._parse_epoch(target.get('clock_epoch')) or 0
            if epoch > row_epoch:
                target['clock_epoch'] = epoch
                target['timestamp'] = self._epoch_to_iso(epoch)

        for sample in onu_rx_samples or []:
            _upsert(sample, field_name='onu_rx_power', field_epoch_key='onu_rx_epoch')

        for sample in olt_rx_samples or []:
            _upsert(sample, field_name='olt_rx_power', field_epoch_key='olt_rx_epoch')

        rows = []
        for row in sorted(merged_rows, key=lambda entry: self._parse_epoch(entry.get('clock_epoch')) or 0):
            if row.get('onu_rx_power') is None and row.get('olt_rx_power') is None:
                continue
            rows.append(
                {
                    'timestamp': row.get('timestamp'),
                    'onu_rx_power': row.get('onu_rx_power'),
                    'olt_rx_power': row.get('olt_rx_power'),
                }
            )

        rows = self._downsample_samples(rows, max_points=max_power_points)
        return rows

    @action(detail=False, methods=['get'], url_path='alarm-clients')
    def alarm_clients(self, request):
        search = str(request.query_params.get('search') or '').strip()
        limit = self._positive_int(request.query_params.get('limit'), default=7, minimum=1, maximum=50)
        if not search:
            return Response({'count': 0, 'results': []})

        queryset = (
            ONU.objects.filter(is_active=True, olt__is_active=True)
            .select_related('olt')
            .filter(
                Q(name__icontains=search)
                | Q(serial__icontains=search)
            )
            .order_by('name', 'serial', 'olt__name', 'slot_id', 'pon_id', 'onu_id')[:limit]
        )
        rows = [
            {
                'id': onu.id,
                'client_name': (onu.name or '').strip() or '-',
                'serial': display_onu_serial(onu.olt, onu.serial),
                'olt_id': onu.olt_id,
                'olt_name': onu.olt.name,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'history_days': onu.olt.history_days,
            }
            for onu in queryset
        ]
        return Response({'count': len(rows), 'results': rows})

    @action(detail=True, methods=['get'], url_path='alarm-history')
    def alarm_history(self, request, pk=None):
        onu = self.get_object()
        alarm_limit = self._positive_int(request.query_params.get('alarm_limit'), default=200, minimum=1, maximum=1000)
        max_power_points = self._positive_int(request.query_params.get('max_power_points'), default=240, minimum=20, maximum=1000)

        now = timezone.now()
        today = now.date()

        # Try exact date range (start_date / end_date)
        raw_start = request.query_params.get('start_date')
        raw_end = request.query_params.get('end_date')
        use_date_range = False
        if raw_start and raw_end:
            try:
                start_d = _dt.date.fromisoformat(raw_start)
                end_d = _dt.date.fromisoformat(raw_end)
                earliest = today - _dt.timedelta(days=365)
                start_d = max(start_d, earliest)
                end_d = min(end_d, today)
                use_date_range = True
            except (ValueError, TypeError):
                pass

        if use_date_range:
            alarm_cutoff = timezone.make_aware(_dt.datetime.combine(start_d, _dt.time.min))
            alarm_end = timezone.make_aware(_dt.datetime.combine(end_d, _dt.time.max))
            power_cutoff = alarm_cutoff
            power_end = alarm_end
        else:
            alarm_days = self._positive_int(request.query_params.get('alarm_days'), default=30, minimum=1, maximum=365)
            power_days = self._positive_int(request.query_params.get('power_days'), default=7, minimum=1, maximum=30)
            alarm_cutoff = now - timedelta(days=alarm_days)
            alarm_end = now
            power_cutoff = now - timedelta(days=power_days)
            power_end = now

        templates = getattr(getattr(onu.olt, 'vendor_profile', None), 'oid_templates', {}) or {}
        zabbix_cfg = templates.get('zabbix', {}) if isinstance(templates.get('zabbix', {}), dict) else {}
        status_cfg = templates.get('status', {}) if isinstance(templates.get('status', {}), dict) else {}
        status_map_cfg = status_cfg.get('status_map', {}) if isinstance(status_cfg.get('status_map', {}), dict) else {}
        disconnect_reason_map = (
            status_cfg.get('disconnect_reason_map', {})
            if isinstance(status_cfg.get('disconnect_reason_map', {}), dict)
            else {}
        )

        status_pattern = str(zabbix_cfg.get('status_item_key_pattern') or '').strip()
        reason_pattern = str(zabbix_cfg.get('reason_item_key_pattern') or '').strip()
        onu_rx_pattern = str(zabbix_cfg.get('onu_rx_item_key_pattern') or '').strip()
        olt_rx_pattern = str(zabbix_cfg.get('olt_rx_item_key_pattern') or '').strip()

        data_source = 'varuna'
        alarms = []
        stats = {
            'total': 0,
            'link_loss': 0,
            'dying_gasp': 0,
            'unknown': 0,
            'active': 0,
            'resolved': 0,
        }
        power_history = []

        zabbix_payload = None
        if onu.snmp_index and (status_pattern or onu_rx_pattern or olt_rx_pattern):
            try:
                status_time_from = int(alarm_cutoff.timestamp())
                status_time_till = int(alarm_end.timestamp())
                power_time_from = int(power_cutoff.timestamp())
                power_time_till = int(power_end.timestamp())
                zabbix_payload = zabbix_service.fetch_onu_item_timelines(
                    onu.olt,
                    index=str(onu.snmp_index),
                    status_item_key_pattern=status_pattern if not onu.olt.unm_enabled else '',
                    reason_item_key_pattern=reason_pattern if not onu.olt.unm_enabled else '',
                    onu_rx_item_key_pattern=onu_rx_pattern,
                    olt_rx_item_key_pattern=olt_rx_pattern,
                    status_time_from=status_time_from,
                    status_time_till=status_time_till,
                    power_time_from=power_time_from,
                    power_time_till=power_time_till,
                    status_limit=max(alarm_limit * 20, 5000),
                    power_limit=max(max_power_points * 10, 2000),
                )
            except Exception:
                logger.exception("Failed to build alarm-history timelines from Zabbix for onu_id=%s", onu.id)
                zabbix_payload = None

        if onu.olt.unm_enabled:
            try:
                alarms = unm_service.fetch_onu_alarm_history(
                    olt=onu.olt,
                    onu=onu,
                    alarm_cutoff=alarm_cutoff,
                    alarm_end=alarm_end,
                    alarm_limit=alarm_limit,
                )
            except UNMServiceError as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            except Exception:
                logger.exception("Failed to build alarm-history from UNM for onu_id=%s", onu.id)
                return Response(
                    {'detail': 'UNM query failed.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            data_source = 'unm'
            for alarm in alarms:
                event_bucket = alarm.get('event_type')
                if event_bucket not in ('link_loss', 'dying_gasp'):
                    event_bucket = 'unknown'
                status_value = alarm.get('status') or 'resolved'
                stats['total'] += 1
                stats[event_bucket] += 1
                if status_value in ('active', 'resolved'):
                    stats[status_value] += 1
        elif zabbix_payload and zabbix_payload.get('status_samples'):
            alarms, stats = self._build_zabbix_alarm_rows(
                onu=onu,
                status_samples=zabbix_payload.get('status_samples') or [],
                previous_status_sample=zabbix_payload.get('status_previous'),
                reason_samples=zabbix_payload.get('reason_samples') or [],
                alarm_cutoff=alarm_cutoff,
                alarm_end=alarm_end,
                alarm_limit=alarm_limit,
                status_map=status_map_cfg,
                disconnect_reason_map=disconnect_reason_map,
            )
            data_source = 'zabbix'
        else:
            alarm_logs = list(
                ONULog.objects.filter(
                    onu_id=onu.id,
                    offline_since__gte=alarm_cutoff,
                    offline_since__lte=alarm_end,
                )
                .order_by('-offline_since')[:alarm_limit]
            )

            for log in alarm_logs:
                reason = log.disconnect_reason or ONULog.REASON_UNKNOWN
                if reason not in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP):
                    reason = ONULog.REASON_UNKNOWN

                status_value = 'active' if log.offline_until is None else 'resolved'
                duration_seconds = None
                if log.offline_since and log.offline_until:
                    duration_seconds = max(0, int((log.offline_until - log.offline_since).total_seconds()))

                stats['total'] += 1
                stats[reason] += 1
                stats[status_value] += 1

                alarms.append(
                    {
                        'id': log.id,
                        'event_type': reason,
                        'start_at': log.offline_since.isoformat() if log.offline_since else None,
                        'end_at': log.offline_until.isoformat() if log.offline_until else None,
                        'status': status_value,
                        'duration_seconds': duration_seconds,
                        'disconnect_window_start': (
                            log.disconnect_window_start.isoformat()
                            if log.disconnect_window_start else None
                        ),
                        'disconnect_window_end': (
                            log.disconnect_window_end.isoformat()
                            if log.disconnect_window_end else None
                        ),
                    }
                )
        if zabbix_payload and (
            (zabbix_payload.get('onu_rx_samples') or [])
            or (zabbix_payload.get('olt_rx_samples') or [])
        ):
            configured_merge_window = int(getattr(settings, 'ALARM_HISTORY_POWER_MERGE_WINDOW_SECONDS', 0) or 0)
            if configured_merge_window > 0:
                merge_window_seconds = max(1, configured_merge_window)
            else:
                merge_window_seconds = max(
                    5,
                    min(
                        60,
                        int((onu.olt.power_interval_seconds or 300) * 0.2),
                    ),
                )
            power_history = self._build_zabbix_power_history(
                onu_rx_samples=zabbix_payload.get('onu_rx_samples') or [],
                olt_rx_samples=zabbix_payload.get('olt_rx_samples') or [],
                max_power_points=max_power_points,
                merge_window_seconds=merge_window_seconds,
            )
        else:
            power_samples = list(
                ONUPowerSample.objects.filter(
                    onu_id=onu.id,
                    read_at__gte=power_cutoff,
                    read_at__lte=power_end,
                )
                .order_by('read_at')
            )
            power_samples = self._downsample_samples(power_samples, max_points=max_power_points)
            power_history = []
            for sample in power_samples:
                onu_rx = normalize_power_value(sample.onu_rx_power)
                olt_rx = normalize_power_value(sample.olt_rx_power)
                if onu_rx is None and olt_rx is None:
                    continue
                power_history.append(
                    {
                        'timestamp': sample.read_at.isoformat(),
                        'onu_rx_power': onu_rx,
                        'olt_rx_power': olt_rx,
                    }
                )

        return Response(
            {
                'onu': {
                    'id': onu.id,
                    'olt_id': onu.olt_id,
                    'olt_name': onu.olt.name,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'client_name': onu.name or '',
                'serial': display_onu_serial(onu.olt, onu.serial),
            },
                'stats': stats,
                'alarm_days': alarm_days if not use_date_range else None,
                'power_days': power_days if not use_date_range else None,
                'start_date': str(start_d) if use_date_range else None,
                'end_date': str(end_d) if use_date_range else None,
                'alarms': alarms,
                'power_history': power_history,
                'source': data_source,
                'generated_at': now.isoformat(),
            }
        )

    @action(detail=True, methods=['get'])
    def power(self, request, pk=None):
        """
        Returns power information for one ONU.
        Query param refresh=true/false controls upstream collection vs latest snapshot read.
        """
        onu = self.get_object()
        refresh = _is_true(request.query_params.get('refresh', 'false'))
        if refresh and not can_operate_topology(request.user):
            return _settings_forbidden_response()
        if refresh:
            self._ensure_status_snapshot_for_power(onu.olt)
            onu.olt.refresh_from_db(
                fields=['last_poll_at', 'collector_reachable', 'collector_failure_count', 'last_collector_error']
            )
            if not self._has_usable_status_snapshot(onu.olt):
                detail = str(onu.olt.last_collector_error or 'Collector reported OLT unreachable').strip()
                return Response(
                    {'detail': f"{onu.olt.name}: {detail}"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        if refresh:
            try:
                result_map = power_service.refresh_for_onus(
                    [onu],
                    force_refresh=True,
                    refresh_upstream=True,
                    force_upstream=True,
                )
            except FITCollectorError as exc:
                mark_olt_unreachable(onu.olt, error=str(exc))
                return Response(
                    {'detail': f"{onu.olt.name}: {exc}"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            sync_latest_power_snapshots([onu], result_map)
            history_min_read_at = None
            history_max_age_minutes = get_power_history_max_age_minutes(onu.olt)
            persist_power_samples(
                [onu],
                result_map,
                source=ONUPowerSample.SOURCE_SCOPED,
                min_read_at=history_min_read_at,
                max_age_minutes=history_max_age_minutes,
            )
            data = result_map.get(onu.id) or {}
        else:
            data = self._read_latest_power_rows([onu]).get(int(onu.id)) or {}

        data = self._serialize_power_row(onu, data)
        return Response(data)

    @action(detail=True, methods=['post'], url_path='refresh-status')
    def refresh_status(self, request, pk=None):
        """
        Returns status information for one ONU.
        Body/query option: refresh=true/false controls upstream Zabbix refresh vs cached DB/log read.
        """
        onu = self.get_object()
        refresh = _is_true(request.data.get('refresh', request.query_params.get('refresh', 'false')))

        if refresh and not can_operate_topology(request.user):
            return _settings_forbidden_response()

        if refresh:
            try:
                self._run_scoped_status_refresh([onu], {'mode': 'onu_ids'})
            except RuntimeError as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
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

        if refresh and not can_operate_topology(request.user):
            return _settings_forbidden_response()

        onus, selection_scope, error_response = self._resolve_onu_batch_selection(request)
        if error_response is not None:
            return error_response
        if not onus:
            return Response({'count': 0, 'results': []})

        if refresh:
            try:
                self._run_scoped_status_refresh(onus, selection_scope)
            except RuntimeError as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
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
        if refresh and not can_operate_topology(request.user):
            return _settings_forbidden_response()
        onus, selection_scope, error_response = self._resolve_onu_batch_selection(request)
        if error_response is not None:
            return error_response
        if not onus:
            return Response({'count': 0, 'results': []})

        if not refresh:
            power_map = self._read_latest_power_rows(onus)
            results = [self._serialize_power_row(onu, power_map.get(int(onu.id))) for onu in onus]
            return Response({'count': len(results), 'results': results})

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
                try:
                    self._run_scoped_status_refresh(onus_requiring_status_refresh, selection_scope)
                except RuntimeError as exc:
                    return Response(
                        {'detail': str(exc)},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
            collector_errors = []
            for olt_id in sorted({int(onu.olt_id) for onu in onus}):
                olt = next((candidate.olt for candidate in onus if int(candidate.olt_id) == olt_id), None)
                if olt is None:
                    continue
                olt.refresh_from_db(
                    fields=['last_poll_at', 'collector_reachable', 'collector_failure_count', 'last_collector_error']
                )
                if self._has_usable_status_snapshot(olt):
                    continue
                detail = str(olt.last_collector_error or 'Collector reported OLT unreachable').strip()
                collector_errors.append(f"{olt.name}: {detail}")
            if collector_errors:
                return Response(
                    {'detail': '; '.join(collector_errors)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        try:
            result_map = power_service.refresh_for_onus(
                onus,
                force_refresh=refresh,
                refresh_upstream=refresh,
                force_upstream=refresh,
            )
        except FITCollectorError as exc:
            for olt in {candidate.olt for candidate in onus}:
                mark_olt_unreachable(olt, error=str(exc))
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if refresh:
            sync_latest_power_snapshots(onus, result_map)
            history_min_read_at = None
            history_max_age_minutes = max(get_power_history_max_age_minutes(onu.olt) for onu in onus)
            persist_power_samples(
                onus,
                result_map,
                source=ONUPowerSample.SOURCE_SCOPED,
                min_read_at=history_min_read_at,
                max_age_minutes=history_max_age_minutes,
            )
        results = [self._serialize_power_row(onu, result_map.get(int(onu.id))) for onu in onus]
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
        cache_service.invalidate_topology_structure_cache(pon.olt_id)

    def partial_update(self, request, *args, **kwargs):
        if not can_operate_topology(request.user):
            return _settings_forbidden_response()
        return super().partial_update(request, *args, **kwargs)
