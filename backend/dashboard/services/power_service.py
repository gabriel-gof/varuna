"""
Serviço de potência para ONUs
ONU power service
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from dashboard.models import ONU
from dashboard.services.cache_service import cache_service
from dashboard.services.snmp_service import snmp_service


logger = logging.getLogger(__name__)


def _to_int(value) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_olt_rx(raw_value) -> Optional[float]:
    """
    ZTE OLT RX normalization (same behavior used in Zabbix template):
    - -80000 = invalid
    - valid values are thousandths of dBm
    """
    raw = _to_int(raw_value)
    if raw is None or raw == -80000:
        return None
    return round(raw / 1000.0, 2)


def _normalize_onu_rx(raw_value) -> Optional[float]:
    """
    ZTE ONU RX normalization (same behavior used in Zabbix template).
    """
    raw = _to_int(raw_value)
    if raw is None:
        return None

    if 0 <= raw <= 32767:
        value = (raw * 0.002) - 30
    elif 32767 < raw < 65535:
        value = ((raw - 65535) * 0.002) - 30
    else:
        return None

    if value < -50 or value > 10:
        return None
    return round(value, 2)


class PowerService:
    def __init__(self):
        self.chunk_size = 24

    def refresh_for_onus(
        self,
        onus: Iterable[ONU],
        force_refresh: bool = True,
    ) -> Dict[int, Dict]:
        onus = [onu for onu in onus if onu and onu.olt_id and onu.snmp_index]
        if not onus:
            return {}

        ttl = int(getattr(settings, "POWER_CACHE_TTL", 60))
        now_iso = timezone.now().isoformat()

        grouped: Dict[int, List[ONU]] = defaultdict(list)
        for onu in onus:
            grouped[onu.olt_id].append(onu)

        results: Dict[int, Dict] = {}

        for olt_onus in grouped.values():
            olt = olt_onus[0].olt
            profile = olt.vendor_profile
            power_cfg = (profile.oid_templates or {}).get("power", {})
            onu_rx_oid = str(power_cfg.get("onu_rx_oid") or "").strip(".")
            olt_rx_oid = str(power_cfg.get("olt_rx_oid") or "").strip(".")
            onu_rx_suffix = str(power_cfg.get("onu_rx_suffix") or "").strip(".")

            if not (onu_rx_oid and olt_rx_oid):
                for onu in olt_onus:
                    results[onu.id] = {
                        "onu_id": onu.id,
                        "slot_id": onu.slot_id,
                        "pon_id": onu.pon_id,
                        "onu_number": onu.onu_id,
                        "onu_rx_power": None,
                        "olt_rx_power": None,
                        "power_read_at": None,
                    }
                logger.warning("Missing power OIDs for vendor profile %s", profile.id)
                continue

            oid_to_target: Dict[str, Tuple[int, str]] = {}
            target_to_raw: Dict[int, Dict[str, Optional[str]]] = defaultdict(lambda: {"onu_raw": None, "olt_raw": None})
            pending_oids: List[str] = []

            for onu in olt_onus:
                cached = cache_service.get_onu_power(onu.olt_id, onu.id)
                if cached and not force_refresh:
                    results[onu.id] = {
                        "onu_id": onu.id,
                        "slot_id": onu.slot_id,
                        "pon_id": onu.pon_id,
                        "onu_number": onu.onu_id,
                        "onu_rx_power": cached.get("onu_rx_power"),
                        "olt_rx_power": cached.get("olt_rx_power"),
                        "power_read_at": cached.get("power_read_at"),
                    }
                    continue

                index = str(onu.snmp_index).strip(".")
                onu_oid = f"{onu_rx_oid}.{index}"
                if onu_rx_suffix:
                    onu_oid = f"{onu_oid}.{onu_rx_suffix}"
                olt_oid = f"{olt_rx_oid}.{index}"

                oid_to_target[onu_oid] = (onu.id, "onu_raw")
                oid_to_target[olt_oid] = (onu.id, "olt_raw")
                pending_oids.extend([onu_oid, olt_oid])

            for start in range(0, len(pending_oids), self.chunk_size):
                chunk = pending_oids[start : start + self.chunk_size]
                response = snmp_service.get(olt, chunk)
                if not response:
                    continue
                for oid, raw_value in response.items():
                    target = oid_to_target.get(oid)
                    if not target:
                        continue
                    onu_id, field = target
                    target_to_raw[onu_id][field] = None if raw_value is None else str(raw_value).strip()

            for onu in olt_onus:
                if onu.id in results:
                    continue

                raw_values = target_to_raw.get(onu.id, {})
                onu_rx = _normalize_onu_rx(raw_values.get("onu_raw"))
                olt_rx = _normalize_olt_rx(raw_values.get("olt_raw"))
                read_at = now_iso if (onu_rx is not None or olt_rx is not None) else None

                payload = {
                    "onu_id": onu.id,
                    "slot_id": onu.slot_id,
                    "pon_id": onu.pon_id,
                    "onu_number": onu.onu_id,
                    "onu_rx_power": onu_rx,
                    "olt_rx_power": olt_rx,
                    "power_read_at": read_at,
                }
                cache_service.set_onu_power(onu.olt_id, onu.id, payload, ttl=ttl)
                results[onu.id] = payload

        return results


power_service = PowerService()

