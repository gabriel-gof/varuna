"""
Serviço de Topologia para construir hierarquia OLT -> Slots -> PONs -> ONUs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from django.utils import timezone

from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog
from topology.services.cache_service import cache_service
from topology.services.unm_service import UNMServiceError, unm_service
from topology.services.vendor_profile import supports_olt_rx_power


logger = logging.getLogger(__name__)


class TopologyService:
    STATUS_ONLINE = 'online'
    STRUCTURE_CACHE_VERSION = 3

    @staticmethod
    def _as_iso(value) -> str | None:
        if not value:
            return None
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return str(value)

    def _supports_olt_rx_power(self, olt: OLT) -> bool:
        return supports_olt_rx_power(olt)

    def _discovery_signature(self, olt: OLT) -> str | None:
        return self._as_iso(getattr(olt, 'last_discovery_at', None))

    def _is_valid_structure_payload(self, olt: OLT, payload: Dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        if int(payload.get('cache_version') or 0) != self.STRUCTURE_CACHE_VERSION:
            return False
        if int(payload.get('olt_id') or 0) != int(olt.id):
            return False
        if payload.get('discovery_signature') != self._discovery_signature(olt):
            return False
        return isinstance(payload.get('slots'), dict)

    def _build_structures_for_olts(self, olts: Iterable[OLT]) -> Dict[int, Dict[str, Any]]:
        olt_list = [olt for olt in olts if olt]
        if not olt_list:
            return {}

        olt_ids = [int(olt.id) for olt in olt_list]
        slots_by_olt: Dict[int, List[OLTSlot]] = {}
        pons_by_slot: Dict[int, List[OLTPON]] = {}
        onus_by_pon: Dict[int, List[ONU]] = {}

        slot_rows = list(
            OLTSlot.objects.filter(olt_id__in=olt_ids, is_active=True)
            .order_by('olt_id', 'slot_id')
        )
        pon_rows = list(
            OLTPON.objects.filter(olt_id__in=olt_ids, is_active=True)
            .order_by('olt_id', 'slot_id', 'pon_id')
        )
        onu_rows = list(
            ONU.objects.filter(olt_id__in=olt_ids, is_active=True)
            .order_by('olt_id', 'slot_id', 'pon_id', 'onu_id')
        )

        for slot in slot_rows:
            slots_by_olt.setdefault(int(slot.olt_id), []).append(slot)
        for pon in pon_rows:
            pons_by_slot.setdefault(int(pon.slot_id), []).append(pon)
        for onu in onu_rows:
            if onu.pon_ref_id is not None:
                onus_by_pon.setdefault(int(onu.pon_ref_id), []).append(onu)

        structures: Dict[int, Dict[str, Any]] = {}
        for olt in olt_list:
            supports_olt_rx_power = self._supports_olt_rx_power(olt)
            slots_payload: Dict[str, Dict[str, Any]] = {}
            for slot in slots_by_olt.get(int(olt.id), []):
                slot_key = slot.slot_key or str(slot.slot_id)
                slot_payload = {
                    'id': int(slot.id),
                    'slot_id': int(slot.slot_id),
                    'slot_key': slot.slot_key,
                    'slot_name': slot.name or '',
                    'rack_id': slot.rack_id,
                    'shelf_id': slot.shelf_id,
                    'is_active': bool(slot.is_active),
                    'pons': {},
                }
                for pon in pons_by_slot.get(int(slot.id), []):
                    pon_key = pon.pon_key or f"{slot_key}/{pon.pon_id}"
                    pon_payload = {
                        'id': int(pon.id),
                        'pon_id': int(pon.pon_id),
                        'pon_key': pon.pon_key,
                        'pon_name': pon.name or '',
                        'description': pon.description or '',
                        'pon_index': pon.pon_index,
                        'rack_id': pon.rack_id,
                        'shelf_id': pon.shelf_id,
                        'port_id': pon.port_id,
                        'is_active': bool(pon.is_active),
                        'onus': [],
                    }
                    for onu in onus_by_pon.get(int(pon.id), []):
                        pon_payload['onus'].append(
                            {
                                'id': int(onu.id),
                                'onu_id': int(onu.onu_id),
                                'onu_number': int(onu.onu_id),
                                'name': onu.name or '',
                                'client_name': onu.name or '',
                                'serial': onu.serial or '',
                                'serial_number': onu.serial or '',
                                'last_discovered_at': self._as_iso(onu.last_discovered_at),
                            }
                        )
                    if pon_payload['onus']:
                        slot_payload['pons'][pon_key] = pon_payload
                if slot_payload['pons']:
                    slots_payload[slot_key] = slot_payload

            structures[int(olt.id)] = {
                'cache_version': self.STRUCTURE_CACHE_VERSION,
                'olt_id': int(olt.id),
                'discovery_signature': self._discovery_signature(olt),
                'olt': {
                    'id': int(olt.id),
                    'vendor_display': (olt.vendor_profile.vendor or '').upper(),
                    'vendor_profile_name': olt.vendor_profile.model_name,
                    'supports_olt_rx_power': supports_olt_rx_power,
                },
                'slots': slots_payload,
            }

        return structures

    def get_structure_map(self, olts: Iterable[OLT]) -> Dict[int, Dict[str, Any]]:
        olt_list = [olt for olt in olts if olt]
        if not olt_list:
            return {}

        cached_by_olt = cache_service.get_many_topology_structures([olt.id for olt in olt_list])
        structures: Dict[int, Dict[str, Any]] = {}
        misses: List[OLT] = []
        hit_count = 0

        for olt in olt_list:
            payload = cached_by_olt.get(int(olt.id))
            if self._is_valid_structure_payload(olt, payload):
                structures[int(olt.id)] = payload
                hit_count += 1
            else:
                misses.append(olt)

        if misses:
            rebuilt = self._build_structures_for_olts(misses)
            for olt in misses:
                payload = rebuilt.get(int(olt.id))
                if not payload:
                    continue
                structures[int(olt.id)] = payload
                if not cache_service.set_topology_structure(int(olt.id), payload):
                    logger.warning("Topology structure cache write failed for OLT %s; continuing with live payload.", olt.id)

        logger.info(
            "Topology structure cache fetch: requested=%s hits=%s misses=%s",
            len(olt_list),
            hit_count,
            len(misses),
        )
        return structures

    def _get_runtime_status_map(self, onu_ids: Iterable[int]) -> Dict[int, str]:
        normalized_ids = [int(onu_id) for onu_id in onu_ids]
        if not normalized_ids:
            return {}
        return {
            int(row['id']): row['status']
            for row in ONU.objects.filter(id__in=normalized_ids, is_active=True).values('id', 'status')
        }

    def _get_active_log_map(self, onu_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
        normalized_ids = [int(onu_id) for onu_id in onu_ids]
        if not normalized_ids:
            return {}

        log_map: Dict[int, Dict[str, Any]] = {}
        queryset = (
            ONULog.objects.filter(onu_id__in=normalized_ids, offline_until__isnull=True)
            .order_by('-offline_since')
            .values(
                'onu_id',
                'disconnect_reason',
                'offline_since',
                'disconnect_window_start',
                'disconnect_window_end',
            )
        )
        for row in queryset:
            onu_id = int(row['onu_id'])
            if onu_id in log_map:
                continue
            log_map[onu_id] = row
        return log_map

    def _get_disconnect_timestamp_formatter(self, olt: OLT):
        if not getattr(olt, 'unm_enabled', False):
            return self._as_iso
        try:
            if not unm_service.is_enabled_for_olt(olt):
                return self._as_iso
        except Exception:
            return self._as_iso

        def formatter(value):
            if not value:
                return None
            try:
                localized = unm_service.localize_alarm_datetime(olt=olt, value=value)
            except UNMServiceError:
                return self._as_iso(value)
            return localized.isoformat() if localized else None

        return formatter

    def _collect_onu_ids(self, structure: Dict[str, Any]) -> List[int]:
        onu_ids: List[int] = []
        for slot in (structure.get('slots') or {}).values():
            for pon in (slot.get('pons') or {}).values():
                for onu in pon.get('onus') or []:
                    onu_ids.append(int(onu['id']))
        return onu_ids

    def _build_runtime_onu_row(
        self,
        onu_payload: Dict[str, Any],
        status_value: str | None,
        active_log: Dict[str, Any] | None,
        *,
        detail: bool,
        disconnect_timestamp_formatter=None,
    ) -> Dict[str, Any]:
        status = str(status_value or ONU.STATUS_UNKNOWN)
        disconnect_reason = None
        offline_since = None
        disconnect_window_start = None
        disconnect_window_end = None
        formatter = disconnect_timestamp_formatter or self._as_iso

        if active_log:
            disconnect_reason = active_log.get('disconnect_reason')
            offline_since = formatter(active_log.get('offline_since'))
            window_anchor = (
                active_log.get('disconnect_window_end')
                or active_log.get('disconnect_window_start')
                or active_log.get('offline_since')
            )
            disconnect_window_start = formatter(active_log.get('disconnect_window_start') or window_anchor)
            disconnect_window_end = formatter(active_log.get('disconnect_window_end') or window_anchor)
        elif status == ONU.STATUS_OFFLINE:
            disconnect_reason = ONULog.REASON_UNKNOWN

        row = {
            'id': int(onu_payload['id']),
            'onu_number': int(onu_payload['onu_number']),
            'name': onu_payload.get('name') or '',
            'client_name': onu_payload.get('client_name') or onu_payload.get('name') or '',
            'serial_number': onu_payload.get('serial_number') or onu_payload.get('serial') or '',
            'status': status,
            'disconnect_reason': disconnect_reason,
            'offline_since': offline_since,
            'disconnect_window_start': disconnect_window_start,
            'disconnect_window_end': disconnect_window_end,
            'onu_rx_power': None,
            'olt_rx_power': None,
            'power_read_at': None,
            'last_discovered_at': onu_payload.get('last_discovered_at'),
        }
        if detail:
            row['onu_id'] = int(onu_payload['onu_id'])
            row['serial'] = onu_payload.get('serial') or onu_payload.get('serial_number') or ''
            row['disconnect_reason'] = disconnect_reason or ''
            row['offline_since'] = offline_since or ''
            row['disconnect_window_start'] = disconnect_window_start or ''
            row['disconnect_window_end'] = disconnect_window_end or ''
            row.pop('serial_number', None)
            row.pop('client_name', None)
        return row

    def _compute_status(self, online: int, offline: int) -> str:
        if online == 0 and offline == 0:
            return 'unknown'
        if online > 0 and offline == 0:
            return 'online'
        if offline > 0 and online == 0:
            return 'offline'
        return 'partial'

    def _overlay_structure(
        self,
        structure: Dict[str, Any],
        *,
        status_by_onu_id: Dict[int, str],
        active_log_by_onu_id: Dict[int, Dict[str, Any]],
        disconnect_timestamp_formatter=None,
    ) -> Dict[str, Any]:
        detail_slots: Dict[str, Dict[str, Any]] = {}
        list_slots: List[Dict[str, Any]] = []
        slot_count = 0
        pon_count = 0
        onu_count = 0
        online_count = 0
        offline_count = 0

        for slot_key, slot_payload in (structure.get('slots') or {}).items():
            slot_online = 0
            slot_offline = 0
            slot_onu_count = 0
            detail_pons: Dict[str, Dict[str, Any]] = {}
            list_pons: List[Dict[str, Any]] = []

            for pon_key, pon_payload in (slot_payload.get('pons') or {}).items():
                pon_online = 0
                pon_offline = 0
                pon_onu_count = 0
                detail_onus: List[Dict[str, Any]] = []
                list_onus: List[Dict[str, Any]] = []

                for onu_payload in pon_payload.get('onus') or []:
                    onu_id = int(onu_payload['id'])
                    status_value = status_by_onu_id.get(onu_id, ONU.STATUS_UNKNOWN)
                    active_log = active_log_by_onu_id.get(onu_id)
                    detail_row = self._build_runtime_onu_row(
                        onu_payload,
                        status_value,
                        active_log,
                        detail=True,
                        disconnect_timestamp_formatter=disconnect_timestamp_formatter,
                    )
                    list_row = self._build_runtime_onu_row(
                        onu_payload,
                        status_value,
                        active_log,
                        detail=False,
                        disconnect_timestamp_formatter=disconnect_timestamp_formatter,
                    )
                    detail_onus.append(detail_row)
                    list_onus.append(list_row)
                    pon_onu_count += 1
                    if status_value == self.STATUS_ONLINE:
                        pon_online += 1
                    else:
                        pon_offline += 1

                if not list_onus:
                    continue

                pon_status = self._compute_status(pon_online, pon_offline)
                detail_pons[pon_key] = {
                    'id': int(pon_payload['id']),
                    'pon_id': int(pon_payload['pon_id']),
                    'pon_key': pon_payload.get('pon_key'),
                    'pon_name': pon_payload.get('pon_name') or '',
                    'description': pon_payload.get('description') or '',
                    'pon_index': pon_payload.get('pon_index'),
                    'rack_id': pon_payload.get('rack_id'),
                    'shelf_id': pon_payload.get('shelf_id'),
                    'port_id': pon_payload.get('port_id'),
                    'status': pon_status,
                    'online_count': pon_online,
                    'offline_count': pon_offline,
                    'onus': detail_onus,
                }
                list_pons.append(
                    {
                        'id': int(pon_payload['id']),
                        'pon_number': int(pon_payload['pon_id']),
                        'pon_key': pon_payload.get('pon_key'),
                        'name': pon_payload.get('pon_name') or '',
                        'description': pon_payload.get('description') or '',
                        'onus': list_onus,
                        'onu_count': pon_onu_count,
                        'online_count': pon_online,
                        'offline_count': pon_offline,
                        'is_active': bool(pon_payload.get('is_active', True)),
                    }
                )

                pon_count += 1
                onu_count += pon_onu_count
                online_count += pon_online
                offline_count += pon_offline
                slot_online += pon_online
                slot_offline += pon_offline
                slot_onu_count += pon_onu_count

            if not list_pons:
                continue

            slot_status = self._compute_status(slot_online, slot_offline)
            detail_slots[slot_key] = {
                'id': int(slot_payload['id']),
                'slot_id': int(slot_payload['slot_id']),
                'slot_key': slot_payload.get('slot_key'),
                'slot_name': slot_payload.get('slot_name') or '',
                'rack_id': slot_payload.get('rack_id'),
                'shelf_id': slot_payload.get('shelf_id'),
                'status': slot_status,
                'online_count': slot_online,
                'offline_count': slot_offline,
                'pons': detail_pons,
            }
            list_slots.append(
                {
                    'id': int(slot_payload['id']),
                    'slot_number': int(slot_payload['slot_id']),
                    'slot_key': slot_payload.get('slot_key'),
                    'name': slot_payload.get('slot_name') or '',
                    'pons': list_pons,
                    'pon_count': len(list_pons),
                    'onu_count': slot_onu_count,
                    'online_count': slot_online,
                    'offline_count': slot_offline,
                    'is_active': bool(slot_payload.get('is_active', True)),
                }
            )
            slot_count += 1

        return {
            'detail_slots': detail_slots,
            'list_slots': list_slots,
            'slot_count': slot_count,
            'pon_count': pon_count,
            'onu_count': onu_count,
            'online_count': online_count,
            'offline_count': offline_count,
            'status': self._compute_status(online_count, offline_count),
        }

    def build_topology_rows(self, olts: Iterable[OLT]) -> List[Dict[str, Any]]:
        olt_list = [olt for olt in olts if olt]
        structures = self.get_structure_map(olt_list)
        all_onu_ids: List[int] = []
        for olt in olt_list:
            structure = structures.get(int(olt.id)) or {}
            all_onu_ids.extend(self._collect_onu_ids(structure))

        status_by_onu_id = self._get_runtime_status_map(all_onu_ids)
        active_log_by_onu_id = self._get_active_log_map(all_onu_ids)

        rows: List[Dict[str, Any]] = []
        for olt in olt_list:
            structure = structures.get(int(olt.id)) or {'slots': {}}
            disconnect_timestamp_formatter = self._get_disconnect_timestamp_formatter(olt)
            overlaid = self._overlay_structure(
                structure,
                status_by_onu_id=status_by_onu_id,
                active_log_by_onu_id=active_log_by_onu_id,
                disconnect_timestamp_formatter=disconnect_timestamp_formatter,
            )
            rows.append(
                {
                    'id': int(olt.id),
                    'name': olt.name,
                    'ip_address': olt.ip_address,
                    'vendor_profile': int(olt.vendor_profile_id),
                    'vendor_display': (olt.vendor_profile.vendor or '').upper(),
                    'vendor_profile_name': olt.vendor_profile.model_name,
                    'protocol': olt.protocol,
                    'snmp_port': olt.snmp_port,
                    'snmp_community': olt.snmp_community,
                    'snmp_version': olt.snmp_version,
                    'telnet_username': olt.telnet_username,
                    'telnet_password_configured': bool(str(olt.telnet_password or '').strip()),
                    'blade_ips': olt.blade_ips,
                    'unm_enabled': bool(olt.unm_enabled),
                    'unm_host': olt.unm_host,
                    'unm_port': olt.unm_port,
                    'unm_username': olt.unm_username,
                    'unm_password_configured': bool(str(olt.unm_password or '').strip()),
                    'unm_mneid': olt.unm_mneid,
                    'collector_reachable': olt.collector_reachable,
                    'last_collector_check_at': self._as_iso(olt.last_collector_check_at),
                    'last_collector_error': olt.last_collector_error,
                    'collector_failure_count': int(olt.collector_failure_count or 0),
                    'snmp_reachable': olt.collector_reachable,
                    'last_snmp_check_at': self._as_iso(olt.last_collector_check_at),
                    'last_snmp_error': olt.last_collector_error,
                    'snmp_failure_count': int(olt.collector_failure_count or 0),
                    'discovery_enabled': bool(olt.discovery_enabled),
                    'discovery_interval_minutes': int(olt.discovery_interval_minutes or 0),
                    'polling_enabled': bool(olt.polling_enabled),
                    'polling_interval_seconds': int(olt.polling_interval_seconds or 0),
                    'power_interval_seconds': int(olt.power_interval_seconds or 0),
                    'history_days': int(olt.history_days or 0),
                    'last_discovery_at': self._as_iso(olt.last_discovery_at),
                    'next_discovery_at': self._as_iso(olt.next_discovery_at),
                    'last_poll_at': self._as_iso(olt.last_poll_at),
                    'next_poll_at': self._as_iso(olt.next_poll_at),
                    'last_power_at': self._as_iso(olt.last_power_at),
                    'next_power_at': self._as_iso(olt.next_power_at),
                    'slots': overlaid['list_slots'],
                    'slot_count': overlaid['slot_count'],
                    'pon_count': overlaid['pon_count'],
                    'onu_count': overlaid['onu_count'],
                    'online_count': overlaid['online_count'],
                    'offline_count': overlaid['offline_count'],
                    'supports_olt_rx_power': self._supports_olt_rx_power(olt),
                    'is_active': bool(olt.is_active),
                    'created_at': self._as_iso(olt.created_at),
                    'updated_at': self._as_iso(olt.updated_at),
                }
            )
        return rows

    def build_topology(self, olt: OLT) -> Dict[str, Any]:
        structure = self.get_structure_map([olt]).get(int(olt.id)) or {'slots': {}}
        onu_ids = self._collect_onu_ids(structure)
        disconnect_timestamp_formatter = self._get_disconnect_timestamp_formatter(olt)
        overlaid = self._overlay_structure(
            structure,
            status_by_onu_id=self._get_runtime_status_map(onu_ids),
            active_log_by_onu_id=self._get_active_log_map(onu_ids),
            disconnect_timestamp_formatter=disconnect_timestamp_formatter,
        )
        return {
            'olt': {
                'id': int(olt.id),
                'name': olt.name,
                'vendor': (olt.vendor_profile.vendor or '').upper(),
                'model': olt.vendor_profile.model_name,
                'status': overlaid['status'],
                'online_count': overlaid['online_count'],
                'offline_count': overlaid['offline_count'],
                'collector_reachable': olt.collector_reachable,
                'last_collector_check_at': self._as_iso(olt.last_collector_check_at),
                'last_collector_error': olt.last_collector_error or '',
                'collector_failure_count': int(olt.collector_failure_count or 0),
                'snmp_reachable': olt.collector_reachable,
                'last_snmp_check_at': self._as_iso(olt.last_collector_check_at),
                'last_snmp_error': olt.last_collector_error or '',
                'snmp_failure_count': int(olt.collector_failure_count or 0),
                'last_discovery': self._as_iso(olt.last_discovery_at),
                'last_poll': self._as_iso(olt.last_poll_at),
                'supports_olt_rx_power': self._supports_olt_rx_power(olt),
            },
            'slots': overlaid['detail_slots'],
            'generated_at': self._as_iso(timezone.localtime(timezone.now())),
        }
