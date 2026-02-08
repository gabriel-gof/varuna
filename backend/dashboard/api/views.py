from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q, Prefetch
from django.core.management import call_command
from io import StringIO
from dashboard.models import OLT, OLTSlot, OLTPON, ONU, VendorProfile
from dashboard.services.topology_service import TopologyService
from dashboard.api.serializers import (
    VendorProfileSerializer,
    OLTSerializer,
    OLTTopologySerializer,
    OLTSlotSerializer,
    OLTPONSerializer,
    ONUSerializer,
)


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
        # TODO: Implement power refresh logic
        return Response({'status': 'requested', 'olt_id': olt.id})

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
