"""
Helpers to interpret vendor profile OID templates.
"""
import re
from typing import Any, Dict, Optional

from topology.models import ONU, ONULog


VALID_STATUSES = {ONU.STATUS_ONLINE, ONU.STATUS_OFFLINE, ONU.STATUS_UNKNOWN}
VALID_REASONS = {ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP, ONULog.REASON_UNKNOWN, ''}


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


def parse_onu_index(index_str: str, indexing_cfg: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """
    Parse ONU index using a vendor profile indexing strategy.
    Supports:
    - `regex`: named groups (`onu_id`, `slot_id`, `pon_id`, `pon_numeric`, `rack_id`, `shelf_id`, `port_id`)
    - `parts`: mapping field -> split-position (0-based)
    - default fallback: "<pon_numeric>.<onu_id>" (legacy ZTE path)
    """
    if not index_str:
        return None

    raw = str(index_str).strip().strip('.')
    if not raw:
        return None

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
