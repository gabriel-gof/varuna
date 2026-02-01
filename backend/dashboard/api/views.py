from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q, Prefetch
from dashboard.models import OLT, OLTSlot, OLTPON, ONU, VendorProfile
from dashboard.services.topology_service import TopologyService
from dashboard.api.serializers import (
    VendorProfileSerializer,
    OLTSerializer,
    OLTSlotSerializer,
    OLTPONSerializer,
    ONUSerializer,
)


class VendorProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para Perfis de Fabricante (read-only)
    Vendor Profile ViewSet (read-only)
    """
    queryset = VendorProfile.objects.filter(is_active=True)
    serializer_class = VendorProfileSerializer


class OLTViewSet(viewsets.ModelViewSet):
    """
    ViewSet para OLTs
    OLT ViewSet
    """
    queryset = OLT.objects.filter(is_active=True).select_related('vendor_profile')
    serializer_class = OLTSerializer

    @action(detail=True, methods=['get'])
    def topology(self, request, pk=None):
        """
        Retorna topologia completa da OLT
        Returns complete OLT topology
        """
        olt = get_object_or_404(OLT, pk=pk)
        service = TopologyService()
        topology = service.build_topology(olt)
        return Response(topology)
    
    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Retorna estatísticas da OLT
        Returns OLT statistics
        """
        olt = get_object_or_404(OLT, pk=pk)
        onus = ONU.objects.filter(olt=olt)
        
        online_count = onus.filter(status='online').count()
        offline_count = onus.filter(status='offline').count()
        total_count = onus.count()
        
        return Response({
            'olt_id': olt.id,
            'olt_name': olt.name,
            'total_onus': total_count,
            'online_count': online_count,
            'offline_count': offline_count,
            'offline_percentage': round(offline_count / total_count * 100, 2) if total_count > 0 else 0,
        })


class ONUViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para ONUs (read-only)
    ONU ViewSet (read-only)
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
            queryset = queryset.filter(status=status_filter)
        
        return queryset.order_by('olt', 'slot_id', 'pon_id', 'onu_id')


class OLTSlotViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para Slots (read-only)
    Slot ViewSet (read-only)
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
