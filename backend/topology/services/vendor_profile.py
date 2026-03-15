"""
Helpers to interpret vendor profile OID templates.
"""
import re
from typing import Any, Dict, Optional

from topology.models import ONU, ONULog


VALID_STATUSES = {ONU.STATUS_ONLINE, ONU.STATUS_OFFLINE, ONU.STATUS_UNKNOWN}
VALID_REASONS = {ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP, ONULog.REASON_UNKNOWN, ''}

COLLECTOR_TYPE_ZABBIX = 'zabbix'
COLLECTOR_TYPE_FIT_TELNET = 'fit_telnet'
COLLECTOR_TRANSPORT_HTTP = 'http'
COLLECTOR_TRANSPORT_TELNET = 'telnet'
DEFAULT_PROTOCOL_SNMP = 'snmp'
DEFAULT_PROTOCOL_TELNET = 'telnet'


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == '':
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def decode_pon_numeric(pon_numeric: int, encoding: str) -> Optional[Dict[str, int]]:
    if encoding == '0x11rrsspp':
        if pon_numeric < 0:
            return None
        if ((pon_numeric >> 24) & 0xFF) != 0x11:
            return None
        return {
            'rack': (pon_numeric >> 16) & 0xFF,
            'shelf': (pon_numeric >> 8) & 0xFF,
            'port': pon_numeric & 0xFF,
        }
    return None


def _extract_templates(source: Any) -> Dict[str, Any]:
    if hasattr(source, 'vendor_profile'):
        source = getattr(source, 'vendor_profile')
    templates = getattr(source, 'oid_templates', source)
    return templates if isinstance(templates, dict) else {}


def get_collector_type(source: Any) -> str:
    templates = _extract_templates(source)
    collector_cfg = templates.get('collector', {}) if isinstance(templates.get('collector', {}), dict) else {}
    collector_type = str(collector_cfg.get('type') or '').strip().lower()
    if collector_type:
        return collector_type
    protocol = str(getattr(source, 'protocol', '') or '').strip().lower()
    if protocol == DEFAULT_PROTOCOL_TELNET:
        return COLLECTOR_TYPE_FIT_TELNET
    return COLLECTOR_TYPE_ZABBIX


def get_default_protocol(source: Any) -> str:
    collector_type = get_collector_type(source)
    if collector_type == COLLECTOR_TYPE_FIT_TELNET:
        return DEFAULT_PROTOCOL_TELNET
    return DEFAULT_PROTOCOL_SNMP


def get_collector_transport(source: Any) -> str:
    templates = _extract_templates(source)
    collector_cfg = templates.get('collector', {}) if isinstance(templates.get('collector', {}), dict) else {}
    transport = str(collector_cfg.get('transport') or '').strip().lower()
    if transport:
        return transport
    collector_type = get_collector_type(source)
    if collector_type == COLLECTOR_TYPE_FIT_TELNET:
        return COLLECTOR_TRANSPORT_TELNET
    return COLLECTOR_TRANSPORT_HTTP


def should_hide_onu_serial(source: Any) -> bool:
    return get_collector_type(source) == COLLECTOR_TYPE_FIT_TELNET


def display_onu_serial(source: Any, serial: Any) -> str:
    if should_hide_onu_serial(source):
        return ''
    return str(serial or '').strip()


def supports_olt_rx_power(source: Any) -> bool:
    templates = _extract_templates(source)
    power_cfg = templates.get('power', {}) if isinstance(templates.get('power', {}), dict) else {}
    explicit_flag = power_cfg.get('supports_olt_rx_power')
    if explicit_flag is not None:
        return bool(explicit_flag)
    return bool(str(power_cfg.get('olt_rx_oid') or '').strip('.'))


def map_disconnect_reason(
    reason_code: Optional[str],
    disconnect_reason_map: Dict[str, str],
) -> str:
    """Map a raw disconnect reason SNMP code to a canonical reason string."""
    if reason_code is None:
        return ONULog.REASON_UNKNOWN
    reason = disconnect_reason_map.get(str(reason_code), ONULog.REASON_UNKNOWN)
    if reason not in VALID_REASONS or reason == '':
        return ONULog.REASON_UNKNOWN
    return reason


def _extract_onu_id_from_flat(flat_int: int, method: str) -> Optional[int]:
    """Extract onu_id from a flat integer SNMP index using a named method."""
    if method == 'byte2':
        # Fiberhome encoding: flat_index = (slot_enc<<24)|(pon_enc<<16)|(onu_id<<8)|0
        return (flat_int >> 8) & 0xFF
    return None


def parse_onu_index(
    index_str: str,
    indexing_cfg: Dict[str, Any],
    *,
    pon_map: Optional[Dict[int, Dict[str, int]]] = None,
    column_map: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[Dict[str, int]]:
    """
    Parse ONU index using a vendor profile indexing strategy.
    Supports:
    - `index_from: oid_columns`: slot/pon from column_map, onu_id from flat int via `onu_id_extract`
    - `regex`: named groups (`onu_id`, `slot_id`, `pon_id`, `pon_numeric`, `rack_id`, `shelf_id`, `port_id`)
    - `parts`: mapping field -> split-position (0-based)
    - default fallback: "<pon_numeric>.<onu_id>" (legacy ZTE path)
    """
    if not index_str:
        return None

    raw = str(index_str).strip().strip('.')
    if not raw:
        return None

    # --- oid_columns mode: slot/pon from separate SNMP columns, onu_id from flat index ---
    if indexing_cfg.get('index_from') == 'oid_columns':
        if not column_map or raw not in column_map:
            return None
        entry = column_map[raw]
        slot_id = entry.get('slot_id')
        pon_id = entry.get('pon_id')
        if slot_id is None or pon_id is None:
            return None
        try:
            flat_int = int(raw)
        except (TypeError, ValueError):
            return None
        extract_method = str(indexing_cfg.get('onu_id_extract', 'byte2'))
        onu_id = _extract_onu_id_from_flat(flat_int, extract_method)
        if onu_id is None:
            return None
        return {
            'pon_numeric': None,
            'onu_id': int(onu_id),
            'slot_id': int(slot_id),
            'pon_id': int(pon_id),
            'rack_id': None,
            'shelf_id': None,
            'port_id': None,
        }

    values: Dict[str, Optional[int]] = {
        'pon_numeric': None,
        'onu_id': None,
        'slot_id': None,
        'pon_id': None,
        'rack_id': None,
        'shelf_id': None,
        'port_id': None,
    }

    regex = indexing_cfg.get('regex')
    if regex:
        match = re.match(regex, raw)
        if not match:
            return None
        for key, value in match.groupdict().items():
            if key in values:
                values[key] = _to_int(value)
    else:
        parts = raw.split('.')
        part_map = indexing_cfg.get('parts') if isinstance(indexing_cfg.get('parts'), dict) else None
        if part_map:
            for key, index in part_map.items():
                if key not in values:
                    continue
                pos = _to_int(index)
                if pos is None or pos < 0 or pos >= len(parts):
                    continue
                values[key] = _to_int(parts[pos])
        else:
            if len(parts) < 2:
                return None
            values['pon_numeric'] = _to_int(parts[0])
            values['onu_id'] = _to_int(parts[1])

    if values['onu_id'] is None:
        onu_position = _to_int(indexing_cfg.get('onu_id_position'))
        if onu_position is not None:
            parts = raw.split('.')
            if 0 <= onu_position < len(parts):
                values['onu_id'] = _to_int(parts[onu_position])

    location = {}
    if values['pon_numeric'] is not None:
        location['pon_id'] = values['pon_numeric']
        pon_encoding = str(indexing_cfg.get('pon_encoding') or '').strip()
        if pon_encoding:
            decoded = decode_pon_numeric(values['pon_numeric'], pon_encoding)
            if decoded:
                location.update(decoded)

    pon_resolve = str(indexing_cfg.get('pon_resolve') or '').strip()
    if pon_resolve == 'interface_map' and pon_map and values['pon_numeric'] is not None:
        map_entry = pon_map.get(values['pon_numeric'])
        if map_entry:
            for key in ('slot_id', 'pon_id', 'rack_id', 'shelf_id', 'port_id'):
                if values.get(key) is None and key in map_entry:
                    values[key] = map_entry[key]
            location.setdefault('rack', map_entry.get('rack_id'))
            location.setdefault('shelf', map_entry.get('shelf_id'))
            location.setdefault('port', map_entry.get('port_id'))

    if values['rack_id'] is not None:
        location['rack'] = values['rack_id']
    if values['shelf_id'] is not None:
        location['shelf'] = values['shelf_id']
    if values['port_id'] is not None:
        location['port'] = values['port_id']

    fixed_cfg = indexing_cfg.get('fixed') if isinstance(indexing_cfg.get('fixed'), dict) else {}

    slot_id = values['slot_id']
    pon_id = values['pon_id']
    if slot_id is None:
        slot_from = indexing_cfg.get('slot_from', 'shelf')
        slot_id = _to_int(location.get(slot_from))
    if slot_id is None:
        slot_id = _to_int(fixed_cfg.get('slot_id'))
    if pon_id is None:
        pon_from = indexing_cfg.get('pon_from', 'port')
        pon_id = _to_int(location.get(pon_from))
    if pon_id is None:
        pon_id = _to_int(fixed_cfg.get('pon_id'))

    onu_id = values['onu_id']
    if slot_id is None or pon_id is None or onu_id is None:
        return None

    return {
        'pon_numeric': values['pon_numeric'],
        'onu_id': int(onu_id),
        'slot_id': int(slot_id),
        'pon_id': int(pon_id),
        'rack_id': _to_int(location.get('rack')),
        'shelf_id': _to_int(location.get('shelf')),
        'port_id': _to_int(location.get('port')),
    }


def map_status_code(status_code: Optional[str], status_map: Dict[str, Any]) -> Dict[str, str]:
    if status_code is None:
        return {'status': ONU.STATUS_UNKNOWN, 'reason': ONULog.REASON_UNKNOWN}

    info = status_map.get(str(status_code), {})
    status = info.get('status', ONU.STATUS_UNKNOWN)
    reason = info.get('reason', ONULog.REASON_UNKNOWN)

    if status not in VALID_STATUSES:
        status = ONU.STATUS_UNKNOWN
    if status == ONU.STATUS_ONLINE:
        reason = ''
    elif reason not in VALID_REASONS:
        reason = ONULog.REASON_UNKNOWN

    return {'status': status, 'reason': reason}
