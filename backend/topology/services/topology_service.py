"""
Serviço de Topologia para construir hierarquia OLT → Slots → PONs → ONUs
Topology Service to build OLT → Slots → PONs → ONUs hierarchy
"""
import logging
from typing import Any, Dict

from django.utils import timezone

from topology.models import OLT, ONU, ONULog, OLTSlot
from topology.services.cache_service import cache_service

logger = logging.getLogger(__name__)


class TopologyService:
    """
    Serviço para construir topologia de rede
    Service for building network topology
    """

    STATUS_ONLINE = 'online'

    def build_topology(self, olt: OLT) -> Dict[str, Any]:
        """
        Constrói estrutura hierárquica: OLT → Slots → PONs → ONUs
        Builds hierarchical structure: OLT → Slots → PONs → ONUs
        """
        onus = list(
            ONU.objects.filter(olt=olt, is_active=True)
            .select_related('slot_ref', 'pon_ref')
            .order_by('slot_id', 'pon_id', 'onu_id')
        )

        onu_ids = [onu.id for onu in onus]
        status_cache = cache_service.get_many_onu_status(olt.id, onu_ids)
        power_cache = cache_service.get_many_onu_power(olt.id, onu_ids)

        active_logs = {}
        for log in ONULog.objects.filter(onu_id__in=onu_ids, offline_until__isnull=True).order_by('-offline_since'):
            active_logs.setdefault(log.onu_id, log)

        slots = {}
        slot_queryset = OLTSlot.objects.filter(olt=olt, is_active=True).prefetch_related('pons')
        for slot in slot_queryset:
            slot_key = slot.slot_key or str(slot.slot_id)
            slots[slot_key] = {
                'slot_id': slot.slot_id,
                'slot_key': slot.slot_key,
                'slot_name': slot.name or '',
                'rack_id': slot.rack_id,
                'shelf_id': slot.shelf_id,
                'status': 'unknown',
                'online_count': 0,
                'offline_count': 0,
                'pons': {}
            }
            for pon in slot.pons.filter(is_active=True):
                pon_key = pon.pon_key or f"{slot_key}/{pon.pon_id}"
                slots[slot_key]['pons'][pon_key] = {
                    'pon_id': pon.pon_id,
                    'pon_key': pon.pon_key,
                    'pon_name': pon.name or '',
                    'pon_index': pon.pon_index,
                    'rack_id': pon.rack_id,
                    'shelf_id': pon.shelf_id,
                    'port_id': pon.port_id,
                    'status': 'unknown',
                    'online_count': 0,
                    'offline_count': 0,
                    'onus': []
                }

        for onu in onus:
            slot_key = onu.slot_ref.slot_key if onu.slot_ref else str(onu.slot_id)

            if slot_key not in slots:
                slots[slot_key] = {
                    'slot_id': onu.slot_id,
                    'slot_key': onu.slot_ref.slot_key if onu.slot_ref else '',
                    'slot_name': onu.slot_ref.name if onu.slot_ref else '',
                    'rack_id': onu.slot_ref.rack_id if onu.slot_ref else None,
                    'shelf_id': onu.slot_ref.shelf_id if onu.slot_ref else None,
                    'status': 'unknown',
                    'online_count': 0,
                    'offline_count': 0,
                    'pons': {}
                }

            pon_key = onu.pon_ref.pon_key if onu.pon_ref else f"{slot_key}/{onu.pon_id}"
            if pon_key not in slots[slot_key]['pons']:
                slots[slot_key]['pons'][pon_key] = {
                    'pon_id': onu.pon_id,
                    'pon_key': onu.pon_ref.pon_key if onu.pon_ref else '',
                    'pon_name': onu.pon_ref.name if onu.pon_ref else '',
                    'pon_index': onu.pon_ref.pon_index if onu.pon_ref else None,
                    'rack_id': onu.pon_ref.rack_id if onu.pon_ref else None,
                    'shelf_id': onu.pon_ref.shelf_id if onu.pon_ref else None,
                    'port_id': onu.pon_ref.port_id if onu.pon_ref else None,
                    'status': 'unknown',
                    'online_count': 0,
                    'offline_count': 0,
                    'onus': []
                }

            onu_data = self._build_onu_data(
                onu,
                cached_status=status_cache.get(onu.id),
                cached_power=power_cache.get(onu.id),
                active_log=active_logs.get(onu.id),
            )
            slots[slot_key]['pons'][pon_key]['onus'].append(onu_data)

            if onu_data['status'] == self.STATUS_ONLINE:
                slots[slot_key]['online_count'] += 1
                slots[slot_key]['pons'][pon_key]['online_count'] += 1
            else:
                slots[slot_key]['offline_count'] += 1
                slots[slot_key]['pons'][pon_key]['offline_count'] += 1

        for slot in slots.values():
            slot['status'] = self._compute_status(slot['online_count'], slot['offline_count'])
            for pon in slot['pons'].values():
                pon['status'] = self._compute_status(pon['online_count'], pon['offline_count'])

        total_online = sum(slot['online_count'] for slot in slots.values())
        total_offline = sum(slot['offline_count'] for slot in slots.values())

        return {
            'olt': {
                'id': olt.id,
                'name': olt.name,
                'vendor': (olt.vendor_profile.vendor or '').upper(),
                'model': olt.vendor_profile.model_name,
                'status': self._compute_status(total_online, total_offline),
                'online_count': total_online,
                'offline_count': total_offline,
                'snmp_reachable': olt.snmp_reachable,
                'last_snmp_check_at': olt.last_snmp_check_at.isoformat() if olt.last_snmp_check_at else None,
                'last_discovery': olt.last_discovery_at.isoformat() if olt.last_discovery_at else None,
                'last_poll': olt.last_poll_at.isoformat() if olt.last_poll_at else None,
            },
            'slots': slots,
            'generated_at': timezone.localtime(timezone.now()).isoformat(),
        }

    def _build_onu_data(
        self,
        onu: ONU,
        cached_status: Dict[str, Any] | None,
        cached_power: Dict[str, Any] | None,
        active_log: ONULog | None,
    ) -> Dict[str, Any]:
        """
        Constrói dados da ONU para resposta
        Builds ONU data for response
        """
        status_payload = cached_status or {}
        power_payload = cached_power or {}

        status = status_payload.get('status', onu.status)
        disconnect_reason = status_payload.get('disconnect_reason')
        offline_since = status_payload.get('offline_since')

        if not disconnect_reason and active_log:
            disconnect_reason = active_log.disconnect_reason
        if not offline_since and active_log and active_log.offline_since:
            offline_since = active_log.offline_since.isoformat()

        return {
            'id': onu.id,
            'onu_id': onu.onu_id,
            'name': onu.name or '',
            'serial': onu.serial or '',
            'status': status,
            'disconnect_reason': disconnect_reason or '',
            'offline_since': offline_since or '',
            'onu_rx_power': power_payload.get('onu_rx_power'),
            'olt_rx_power': power_payload.get('olt_rx_power'),
            'power_read_at': power_payload.get('power_read_at'),
        }

    def _compute_status(self, online: int, offline: int) -> str:
        """
        Calcula status agregado de um nó da topologia
        """
        if online == 0 and offline == 0:
            return 'unknown'
        if online > 0 and offline == 0:
            return 'online'
        if offline > 0 and online == 0:
            return 'offline'
        return 'partial'
