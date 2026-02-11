from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q, Prefetch
from django.core.management import call_command
from io import StringIO
import logging
from dashboard.models import OLT, OLTSlot, OLTPON, ONU, VendorProfile
from dashboard.services.topology_service import TopologyService
from dashboard.services.power_service import power_service
from dashboard.api.serializers import (
    VendorProfileSerializer,
    OLTSerializer,
    OLTTopologySerializer,
    OLTSlotSerializer,
    OLTPONSerializer,
    ONUSerializer,
)

logger = logging.getLogger(__name__)


class VendorProfileViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Vendor Profiles
    """
    queryset = VendorProfile.objects.filter(is_active=True).order_by('id')
    serializer_class = VendorProfileSerializer


class OLTViewSet(viewsets.ModelViewSet):
    """
    ViewSet for OLTs
    Supports both flat list and nested topology responses
    """
    serializer_class = OLTSerializer

    def get_queryset(self):
        """
        Optionally prefetch related objects for topology view
        """
        include_topology = self.request.query_params.get('include_topology', 'false').lower() == 'true'
        
        queryset = OLT.objects.filter(is_active=True).select_related('vendor_profile').order_by('id')
        
        if include_topology:
            # Prefetch nested data for better performance
            queryset = queryset.prefetch_related(
                Prefetch(
                    'slots',
                    queryset=OLTSlot.objects.filter(is_active=True).order_by('slot_id')
                ),
                Prefetch(
                    'slots__pons',
                    queryset=OLTPON.objects.filter(is_active=True).order_by('pon_id')
                ),
                'slots__pons__onus'
            )
        
        return queryset
    
    def get_serializer_class(self):
        """
        Use topology serializer when include_topology=true
        """
        include_topology = self.request.query_params.get('include_topology', 'false').lower() == 'true'
        if include_topology and self.action == 'list':
            return OLTTopologySerializer
        return OLTSerializer

    @action(detail=True, methods=['get'])
    def topology(self, request, pk=None):
        """
        Returns complete OLT topology
        """
        olt = get_object_or_404(OLT, pk=pk)
        service = TopologyService()
        topology = service.build_topology(olt)
        return Response(topology)
    
    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Returns OLT statistics
        """
        olt = get_object_or_404(OLT, pk=pk)
        onus = ONU.objects.filter(olt=olt)
        
        online_count = onus.filter(status='online').count()
        offline_count = onus.exclude(status='online').count()
        total_count = onus.count()
        
        return Response({
            'olt_id': olt.id,
            'olt_name': olt.name,
            'total_onus': total_count,
            'online_count': online_count,
            'offline_count': offline_count,
            'offline_percentage': round(offline_count / total_count * 100, 2) if total_count > 0 else 0,
        })
    
    @action(detail=True, methods=['post'])
    def refresh_power(self, request, pk=None):
        """
        Triggers power refresh for all ONUs in this OLT
        """
        olt = get_object_or_404(OLT, pk=pk)
        onus = list(
            ONU.objects.filter(olt=olt)
            .select_related('olt', 'olt__vendor_profile')
            .order_by('slot_id', 'pon_id', 'onu_id')
        )
        result_map = power_service.refresh_for_onus(onus, force_refresh=True)
        results = [result_map.get(onu.id, {'onu_id': onu.id}) for onu in onus]
        return Response({
            'status': 'completed',
            'olt_id': olt.id,
            'count': len(results),
            'results': results,
        })

    @action(detail=True, methods=['post'])
    def run_discovery(self, request, pk=None):
        """
        Run ONU discovery immediately for one OLT.
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        output = StringIO()
        try:
            call_command('discover_onus', olt_id=olt.id, force=True, stdout=output)
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'status': 'completed',
            'olt_id': olt.id,
            'output': output.getvalue().strip()
        })

    @action(detail=True, methods=['post'])
    def snmp_check(self, request, pk=None):
        """
        Quick SNMP connectivity check — queries sysDescr.0 to verify the OLT is reachable.
        Returns { reachable: bool, sys_descr: str|null, olt_id: int }
        """
        olt = get_object_or_404(OLT, pk=pk)
        SYS_DESCR_OID = '1.3.6.1.2.1.1.1.0'
        try:
            from dashboard.services.snmp_service import snmp_service
            result = snmp_service.get(olt, [SYS_DESCR_OID])
            if result and SYS_DESCR_OID in result:
                return Response({
                    'reachable': True,
                    'sys_descr': str(result[SYS_DESCR_OID]) if result[SYS_DESCR_OID] is not None else None,
                    'olt_id': olt.id,
                })
            return Response({
                'reachable': False,
                'sys_descr': None,
                'olt_id': olt.id,
            })
        except Exception as exc:
            logger.warning("SNMP check failed for OLT %s: %s", olt.name, exc)
            return Response({
                'reachable': False,
                'sys_descr': None,
                'olt_id': olt.id,
                'detail': str(exc),
            })

    @action(detail=True, methods=['post'])
    def run_polling(self, request, pk=None):
        """
        Run ONU status polling immediately for one OLT.
        """
        olt = get_object_or_404(OLT, pk=pk, is_active=True)
        output = StringIO()
        try:
            call_command('poll_onu_status', olt_id=olt.id, force=True, stdout=output)
        except Exception as exc:
            return Response(
                {'status': 'error', 'olt_id': olt.id, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'status': 'completed',
            'olt_id': olt.id,
            'output': output.getvalue().strip()
        })


class ONUViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for ONUs (read-only)
    """
    serializer_class = ONUSerializer
    filterset_fields = ['olt', 'status', 'slot_id', 'pon_id', 'slot_ref', 'pon_ref']
    
    def get_queryset(self):
        queryset = ONU.objects.select_related(
            'olt', 'olt__vendor_profile', 'slot_ref', 'pon_ref'
        )
        
        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            if status_filter == 'offline':
                queryset = queryset.exclude(status='online')
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
        refresh = str(request.query_params.get('refresh', 'true')).lower() in {'1', 'true', 'yes', 'on'}
        result_map = power_service.refresh_for_onus([onu], force_refresh=refresh)
        data = result_map.get(onu.id, {
            'onu_id': onu.id,
            'slot_id': onu.slot_id,
            'pon_id': onu.pon_id,
            'onu_number': onu.onu_id,
            'onu_rx_power': None,
            'olt_rx_power': None,
            'power_read_at': None,
        })
        return Response(data)

    @action(detail=False, methods=['post'], url_path='batch-power')
    def batch_power(self, request):
        """
        Returns power information for multiple ONUs.
        Body options:
        - onu_ids: [1,2,3]
        - or olt_id + slot_id + pon_id to refresh one PON quickly
        """
        refresh = str(request.data.get('refresh', True)).lower() in {'1', 'true', 'yes', 'on'}
        onu_ids = request.data.get('onu_ids') or []
        olt_id = request.data.get('olt_id')
        slot_id = request.data.get('slot_id')
        pon_id = request.data.get('pon_id')

        queryset = ONU.objects.select_related('olt', 'olt__vendor_profile')

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
                status=status.HTTP_400_BAD_REQUEST
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
    queryset = OLTSlot.objects.filter(olt__is_active=True).select_related('olt')
    serializer_class = OLTSlotSerializer
    filterset_fields = ['olt', 'slot_id', 'rack_id', 'shelf_id', 'is_active']


class OLTPONViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para PONs (read-only)
    PON ViewSet (read-only)
    """
    queryset = OLTPON.objects.filter(olt__is_active=True).select_related('olt', 'slot')
    serializer_class = OLTPONSerializer
    filterset_fields = ['olt', 'slot', 'pon_id', 'pon_key', 'is_active']
