import logging
from io import StringIO

from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Prefetch, Q
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
from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, VendorProfile
from topology.services.cache_service import cache_service
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.power_service import power_service
from topology.services.snmp_service import snmp_service
from topology.services.topology_service import TopologyService

logger = logging.getLogger(__name__)


def _is_true(value: str | None) -> bool:
    return str(value or '').lower() in {'1', 'true', 'yes', 'on'}


class VendorProfileViewSet(viewsets.ModelViewSet):
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

    def get_queryset(self):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))

        queryset = (
            OLT.objects.filter(is_active=True)
            .select_related('vendor_profile')
            .annotate(
                slot_count=Count('slots', filter=Q(slots__is_active=True), distinct=True),
                pon_count=Count('pons', filter=Q(pons__is_active=True), distinct=True),
                onu_count=Count('onus', filter=Q(onus__is_active=True), distinct=True),
                online_count=Count(
                    'onus',
                    filter=Q(onus__is_active=True, onus__status=ONU.STATUS_ONLINE),
                    distinct=True,
                ),
                offline_count=Count(
                    'onus',
                    filter=Q(onus__is_active=True) & ~Q(onus__status=ONU.STATUS_ONLINE),
                    distinct=True,
                ),
            )
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
                .annotate(
                    onu_count=Count('onus', filter=Q(onus__is_active=True), distinct=True),
                    online_count=Count(
                        'onus',
                        filter=Q(onus__is_active=True, onus__status=ONU.STATUS_ONLINE),
                        distinct=True,
                    ),
                    offline_count=Count(
                        'onus',
                        filter=Q(onus__is_active=True) & ~Q(onus__status=ONU.STATUS_ONLINE),
                        distinct=True,
                    ),
                )
                .prefetch_related(Prefetch('onus', queryset=onus_qs))
                .order_by('pon_id')
            )
            slots_qs = (
                OLTSlot.objects.filter(is_active=True)
                .annotate(
                    pon_count=Count('pons', filter=Q(pons__is_active=True), distinct=True),
                    onu_count=Count('pons__onus', filter=Q(pons__onus__is_active=True), distinct=True),
                    online_count=Count(
                        'pons__onus',
                        filter=Q(pons__onus__is_active=True, pons__onus__status=ONU.STATUS_ONLINE),
                        distinct=True,
                    ),
                    offline_count=Count(
                        'pons__onus',
                        filter=Q(pons__onus__is_active=True) & ~Q(pons__onus__status=ONU.STATUS_ONLINE),
                        distinct=True,
                    ),
                )
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

    def list(self, request, *args, **kwargs):
        include_topology = _is_true(self.request.query_params.get('include_topology', 'false'))
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)

        context = self.get_serializer_context()
        if include_topology:
            context['power_map'] = self._build_power_map(rows)

        serializer = self.get_serializer(rows, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

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

    @action(detail=True, methods=['get'])
    def topology(self, request, pk=None):
        """
        Returns complete OLT topology
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        service = TopologyService()
        topology = service.build_topology(olt)
        return Response(topology)

    @action(detail=True, methods=['post'])
    def refresh_power(self, request, pk=None):
        """
        Triggers power refresh for all ONUs in this OLT
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_power_monitoring',
            required_template_paths=[('power', 'onu_rx_oid'), ('power', 'olt_rx_oid')],
            action_name='power refresh',
        )
        if validation_error is not None:
            return validation_error

        onus = list(
            ONU.objects.filter(olt=olt, is_active=True)
            .select_related('olt', 'olt__vendor_profile')
            .order_by('slot_id', 'pon_id', 'onu_id')
        )
        result_map = power_service.refresh_for_onus(onus, force_refresh=True)
        results = [result_map.get(onu.id, {'onu_id': onu.id}) for onu in onus]
        return Response(
            {
                'status': 'completed',
                'olt_id': olt.id,
                'count': len(results),
                'results': results,
            }
        )

    @action(detail=True, methods=['post'])
    def run_discovery(self, request, pk=None):
        """
        Run ONU discovery immediately for one OLT.
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_onu_discovery',
            required_template_paths=[('discovery', 'onu_name_oid'), ('discovery', 'onu_serial_oid')],
            action_name='ONU discovery',
        )
        if validation_error is not None:
            return validation_error

        output = StringIO()
        try:
            call_command('discover_onus', olt_id=olt.id, force=True, stdout=output)
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({'status': 'completed', 'olt_id': olt.id, 'output': output.getvalue().strip()})

    @action(detail=True, methods=['post'])
    def snmp_check(self, request, pk=None):
        """
        Quick SNMP connectivity check — queries sysDescr.0 to verify the OLT is reachable.
        Returns { reachable: bool, sys_descr: str|null, olt_id: int }
        """
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
            mark_olt_unreachable(olt, error='No sysDescr response')
            return Response({'reachable': False, 'sys_descr': None, 'olt_id': olt.id, 'failure_count': olt.snmp_failure_count})
        except Exception as exc:
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
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        validation_error = self._validate_vendor_action(
            olt,
            capability_field='supports_onu_status',
            required_template_paths=[('status', 'onu_status_oid')],
            action_name='ONU status polling',
        )
        if validation_error is not None:
            return validation_error

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
        refresh = _is_true(request.query_params.get('refresh', 'true'))
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

    @action(detail=False, methods=['post'], url_path='batch-power')
    def batch_power(self, request):
        """
        Returns power information for multiple ONUs.
        Body options:
        - onu_ids: [1,2,3]
        - or olt_id + slot_id + pon_id to refresh one PON quickly
        """
        refresh = _is_true(request.data.get('refresh', True))
        onu_ids = request.data.get('onu_ids') or []
        olt_id = request.data.get('olt_id')
        slot_id = request.data.get('slot_id')
        pon_id = request.data.get('pon_id')

        queryset = ONU.objects.filter(is_active=True).select_related('olt', 'olt__vendor_profile')

        if isinstance(onu_ids, list) and onu_ids:
            queryset = queryset.filter(id__in=onu_ids)
        elif olt_id is not None and slot_id is not None and pon_id is not None:
            queryset = queryset.filter(
                olt_id=olt_id,
                slot_id=slot_id,
                pon_id=pon_id,
            )
        else:
            return Response(
                {'detail': 'Provide onu_ids or (olt_id + slot_id + pon_id).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        onus = list(queryset.order_by('slot_id', 'pon_id', 'onu_id'))
        if not onus:
            return Response({'count': 0, 'results': []})

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


class OLTPONViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for PONs (read-only)
    """

    queryset = OLTPON.objects.filter(olt__is_active=True, is_active=True).select_related('olt', 'slot')
    serializer_class = OLTPONSerializer
    filterset_fields = ['olt', 'slot', 'pon_id', 'pon_key', 'is_active']
