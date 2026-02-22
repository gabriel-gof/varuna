"""
Serviço de potência para ONUs
ONU power service
"""
from __future__ import annotations

import logging
import math
import re
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from topology.models import ONU
from topology.services.cache_service import cache_service
from topology.services.snmp_service import snmp_service


logger = logging.getLogger(__name__)


def _to_int(value) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _extract_dbm_value(raw_value) -> Optional[float]:
    """
    Parses vendor strings like "-27.214(dBm)".
    """
    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    normalized = text.lower()
    if normalized in {'n/a', 'na', '-', '--'}:
        return None
    if 'dbm' not in normalized:
        return None

    match = re.search(r'-?\d+(?:\.\d+)?', text)
    if not match:
        return None

    try:
        value = float(match.group(0))
    except (TypeError, ValueError):
        return None

    if value < -60 or value > 20:
        return None
    return round(value, 2)


def _normalize_olt_rx(raw_value) -> Optional[float]:
    """
    ZTE OLT RX normalization (same behavior used in Zabbix template):
    - -80000 = invalid
    - valid values are thousandths of dBm
    """
    parsed_dbm = _extract_dbm_value(raw_value)
    if parsed_dbm is not None:
        return parsed_dbm

    raw = _to_int(raw_value)
    if raw is None or raw == -80000:
        return None
    return round(raw / 1000.0, 2)


def _normalize_onu_rx(raw_value) -> Optional[float]:
    """
    ZTE ONU RX normalization (same behavior used in Zabbix template).
    """
    parsed_dbm = _extract_dbm_value(raw_value)
    if parsed_dbm is not None:
        return parsed_dbm

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
        self.chunk_size = 16
        self.chunk_retry_attempts = 2
        self.single_oid_retry_attempts = 2
        self.retry_backoff_seconds = 0.2
        self.snmp_timeout_seconds = 1.8
        self.snmp_retries = 0
        self.max_get_call_multiplier = 18
        self.pause_between_pon_batches_seconds = 0.08
        self.max_online_retry_onus = 256
        self.pause_between_single_retries_seconds = 0.02

    @staticmethod
    def _resolve_int(
        value,
        default: int,
        *,
        minimum: int,
        maximum: Optional[int] = None,
    ) -> int:
        parsed = _to_int(value)
        if parsed is None:
            parsed = int(default)
        if parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

    @staticmethod
    def _resolve_float(
        value,
        default: float,
        *,
        minimum: float,
        maximum: Optional[float] = None,
    ) -> float:
        parsed = _to_float(value)
        if parsed is None:
            parsed = float(default)
        if parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

    @staticmethod
    def _build_empty_payload(onu: ONU, *, skipped_reason: Optional[str] = None) -> Dict:
        payload = {
            "onu_id": onu.id,
            "slot_id": onu.slot_id,
            "pon_id": onu.pon_id,
            "onu_number": onu.onu_id,
            "onu_rx_power": None,
            "olt_rx_power": None,
            "power_read_at": None,
        }
        if skipped_reason:
            payload["skipped_reason"] = skipped_reason
        return payload

    def _snmp_get_with_attempts(
        self,
        olt,
        oids: List[str],
        *,
        attempts: int,
        call_budget: Dict[str, int],
        timeout_seconds: float,
        retries: int,
        retry_backoff_seconds: float,
    ) -> Optional[Dict[str, str]]:
        for attempt in range(attempts):
            if call_budget.get("remaining", 0) <= 0:
                return None
            call_budget["remaining"] -= 1
            response = snmp_service.get(
                olt,
                oids,
                timeout=timeout_seconds,
                retries=retries,
            )
            if response is not None:
                return response
            if attempt < attempts - 1:
                time.sleep(retry_backoff_seconds * (attempt + 1))
        return None

    def _fetch_oids_resilient(
        self,
        olt,
        oids: List[str],
        *,
        call_budget: Dict[str, int],
        chunk_retry_attempts: int,
        single_oid_retry_attempts: int,
        timeout_seconds: float,
        retries: int,
        retry_backoff_seconds: float,
    ) -> Dict[str, str]:
        if not oids:
            return {}

        response = self._snmp_get_with_attempts(
            olt,
            oids,
            attempts=chunk_retry_attempts,
            call_budget=call_budget,
            timeout_seconds=timeout_seconds,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        if response is None:
            if len(oids) == 1:
                return {}

            midpoint = len(oids) // 2
            left = self._fetch_oids_resilient(
                olt,
                oids[:midpoint],
                call_budget=call_budget,
                chunk_retry_attempts=chunk_retry_attempts,
                single_oid_retry_attempts=single_oid_retry_attempts,
                timeout_seconds=timeout_seconds,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            right = self._fetch_oids_resilient(
                olt,
                oids[midpoint:],
                call_budget=call_budget,
                chunk_retry_attempts=chunk_retry_attempts,
                single_oid_retry_attempts=single_oid_retry_attempts,
                timeout_seconds=timeout_seconds,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            merged = {}
            merged.update(left)
            merged.update(right)
            return merged

        if len(oids) > 1:
            missing_oids = [oid for oid in oids if oid not in response]
            for oid in missing_oids:
                single = self._snmp_get_with_attempts(
                    olt,
                    [oid],
                    attempts=single_oid_retry_attempts,
                    call_budget=call_budget,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    retry_backoff_seconds=retry_backoff_seconds,
                )
                if isinstance(single, dict) and oid in single:
                    response[oid] = single[oid]

        return response

    @staticmethod
    def _build_power_oids_for_onu(
        onu: ONU,
        *,
        onu_rx_oid: str,
        onu_rx_suffix: str,
        olt_rx_oid: str,
        supports_olt_rx: bool,
    ) -> List[str]:
        index = str(onu.snmp_index).strip(".")
        onu_oid = f"{onu_rx_oid}.{index}"
        if onu_rx_suffix:
            onu_oid = f"{onu_oid}.{onu_rx_suffix}"

        oids = [onu_oid]
        if supports_olt_rx:
            oids.append(f"{olt_rx_oid}.{index}")
        return oids

    def refresh_for_onus(
        self,
        onus: Iterable[ONU],
        force_refresh: bool = True,
    ) -> Dict[int, Dict]:
        base_onus = [onu for onu in onus if onu and onu.olt_id and onu.snmp_index and onu.is_active]
        if not base_onus:
            return {}

        base_ttl = int(getattr(settings, "POWER_CACHE_TTL", 60))
        now_iso = timezone.now().isoformat()
        results: Dict[int, Dict] = {}
        counters_by_olt: Dict[int, Dict[str, int]] = defaultdict(
            lambda: {
                "total_active": 0,
                "online": 0,
                "skipped_offline": 0,
                "skipped_unknown": 0,
                "skipped_not_online": 0,
            }
        )

        eligible_onus: List[ONU] = []
        for onu in base_onus:
            status_value = str(getattr(onu, "status", "") or "").lower()
            counters_by_olt[onu.olt_id]["total_active"] += 1

            if status_value == ONU.STATUS_ONLINE:
                counters_by_olt[onu.olt_id]["online"] += 1
                eligible_onus.append(onu)
                continue

            if status_value == ONU.STATUS_OFFLINE:
                skipped_reason = 'offline'
                counters_by_olt[onu.olt_id]["skipped_offline"] += 1
            elif status_value == ONU.STATUS_UNKNOWN:
                skipped_reason = 'unknown'
                counters_by_olt[onu.olt_id]["skipped_unknown"] += 1
            else:
                skipped_reason = 'not_online'

            counters_by_olt[onu.olt_id]["skipped_not_online"] += 1
            results[onu.id] = self._build_empty_payload(onu, skipped_reason=skipped_reason)

        if not eligible_onus:
            for olt_id, metrics in counters_by_olt.items():
                logger.warning(
                    "Power refresh OLT %s: no online ONUs to query (active=%s, skipped_offline=%s, skipped_unknown=%s).",
                    olt_id,
                    metrics["total_active"],
                    metrics["skipped_offline"],
                    metrics["skipped_unknown"],
                )
            return results

        grouped: Dict[int, List[ONU]] = defaultdict(list)
        for onu in eligible_onus:
            grouped[onu.olt_id].append(onu)

        for olt_onus in grouped.values():
            olt = olt_onus[0].olt
            metrics = counters_by_olt.get(olt.id, {})
            profile = olt.vendor_profile
            power_cfg = (profile.oid_templates or {}).get("power", {})
            onu_rx_oid = str(power_cfg.get("onu_rx_oid") or "").strip(".")
            olt_rx_oid = str(power_cfg.get("olt_rx_oid") or "").strip(".")
            onu_rx_suffix = str(power_cfg.get("onu_rx_suffix") or "").strip(".")
            chunk_size = self._resolve_int(
                power_cfg.get("get_chunk_size"),
                self.chunk_size,
                minimum=1,
                maximum=128,
            )
            chunk_retry_attempts = self._resolve_int(
                power_cfg.get("chunk_retry_attempts"),
                self.chunk_retry_attempts,
                minimum=1,
                maximum=6,
            )
            single_oid_retry_attempts = self._resolve_int(
                power_cfg.get("single_oid_retry_attempts"),
                self.single_oid_retry_attempts,
                minimum=1,
                maximum=6,
            )
            retry_backoff_seconds = self._resolve_float(
                power_cfg.get("retry_backoff_seconds"),
                self.retry_backoff_seconds,
                minimum=0.0,
                maximum=5.0,
            )
            snmp_timeout_seconds = self._resolve_float(
                power_cfg.get("snmp_timeout_seconds"),
                self.snmp_timeout_seconds,
                minimum=0.3,
                maximum=10.0,
            )
            snmp_retries = self._resolve_int(
                power_cfg.get("snmp_retries"),
                self.snmp_retries,
                minimum=0,
                maximum=3,
            )
            max_get_call_multiplier = self._resolve_int(
                power_cfg.get("max_get_call_multiplier"),
                self.max_get_call_multiplier,
                minimum=2,
                maximum=200,
            )
            pause_between_pon_batches_seconds = self._resolve_float(
                power_cfg.get("pause_between_pon_batches_seconds"),
                self.pause_between_pon_batches_seconds,
                minimum=0.0,
                maximum=5.0,
            )
            max_online_retry_onus = self._resolve_int(
                power_cfg.get("max_online_retry_onus"),
                self.max_online_retry_onus,
                minimum=0,
                maximum=4000,
            )
            pause_between_single_retries_seconds = self._resolve_float(
                power_cfg.get("pause_between_single_retries_seconds"),
                self.pause_between_single_retries_seconds,
                minimum=0.0,
                maximum=5.0,
            )
            logger.info(
                "Power refresh OLT %s: querying online ONUs only (active=%s, online=%s, skipped_offline=%s, skipped_unknown=%s).",
                olt.id,
                metrics.get("total_active", len(olt_onus)),
                metrics.get("online", len(olt_onus)),
                metrics.get("skipped_offline", 0),
                metrics.get("skipped_unknown", 0),
            )
            logger.info(
                "Power refresh OLT %s: paced PON batches (online_onus=%s, chunk_size=%s, timeout=%.2fs).",
                olt.id,
                len(olt_onus),
                chunk_size,
                snmp_timeout_seconds,
            )
            estimated_calls = max(1, math.ceil((len(olt_onus) * 2) / chunk_size))
            call_budget = {
                "remaining": max(
                    estimated_calls + 32,
                    estimated_calls * max_get_call_multiplier,
                )
            }
            interval_ttl = int(getattr(olt, "power_interval_seconds", 0) or 0) * 2
            ttl = max(base_ttl, interval_ttl, 300)

            supports_olt_rx = bool(olt_rx_oid)
            power_cache_batch: Dict[int, Dict] = {}
            cached_power_by_onu = cache_service.get_many_onu_power(
                olt.id,
                [onu.id for onu in olt_onus],
            )

            if not onu_rx_oid:
                for onu in olt_onus:
                    results[onu.id] = self._build_empty_payload(onu)
                logger.warning("Missing ONU power OID for vendor profile %s", profile.id)
                continue

            pon_groups: Dict[Tuple[int, int], List[ONU]] = defaultdict(list)
            for onu in olt_onus:
                pon_groups[(int(onu.slot_id or -1), int(onu.pon_id or -1))].append(onu)

            ordered_keys = sorted(pon_groups.keys(), key=lambda item: (item[0], item[1]))
            for key_index, pon_key in enumerate(ordered_keys):
                pon_onus = sorted(pon_groups[pon_key], key=lambda item: int(item.onu_id or 0))
                oid_to_target: Dict[str, Tuple[int, str]] = {}
                target_to_raw: Dict[int, Dict[str, Optional[str]]] = defaultdict(lambda: {"onu_raw": None, "olt_raw": None})
                pending_oids: List[str] = []

                for onu in pon_onus:
                    cached = cached_power_by_onu.get(onu.id)
                    if cached and not force_refresh:
                        cached_onu_rx = cached.get("onu_rx_power")
                        cached_olt_rx = cached.get("olt_rx_power") if supports_olt_rx else None
                        cached_read_at = cached.get("power_read_at") if (cached_onu_rx is not None or cached_olt_rx is not None) else None
                        results[onu.id] = {
                            "onu_id": onu.id,
                            "slot_id": onu.slot_id,
                            "pon_id": onu.pon_id,
                            "onu_number": onu.onu_id,
                            "onu_rx_power": cached_onu_rx,
                            "olt_rx_power": cached_olt_rx,
                            "power_read_at": cached_read_at,
                        }
                        continue

                    onu_oids = self._build_power_oids_for_onu(
                        onu,
                        onu_rx_oid=onu_rx_oid,
                        onu_rx_suffix=onu_rx_suffix,
                        olt_rx_oid=olt_rx_oid,
                        supports_olt_rx=supports_olt_rx,
                    )
                    onu_oid = onu_oids[0]
                    oid_to_target[onu_oid] = (onu.id, "onu_raw")
                    pending_oids.append(onu_oid)

                    if supports_olt_rx and len(onu_oids) > 1:
                        olt_oid = onu_oids[1]
                        oid_to_target[olt_oid] = (onu.id, "olt_raw")
                        pending_oids.append(olt_oid)

                for start in range(0, len(pending_oids), chunk_size):
                    if call_budget["remaining"] <= 0:
                        logger.warning(
                            "Power refresh call budget exhausted for OLT %s; keeping partial results.",
                            olt.id,
                        )
                        break
                    chunk = pending_oids[start : start + chunk_size]
                    response = self._fetch_oids_resilient(
                        olt,
                        chunk,
                        call_budget=call_budget,
                        chunk_retry_attempts=chunk_retry_attempts,
                        single_oid_retry_attempts=single_oid_retry_attempts,
                        timeout_seconds=snmp_timeout_seconds,
                        retries=snmp_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
                    if not response:
                        continue
                    for oid, raw_value in response.items():
                        target = oid_to_target.get(oid)
                        if not target:
                            continue
                        onu_id, field = target
                        target_to_raw[onu_id][field] = None if raw_value is None else str(raw_value).strip()

                for onu in pon_onus:
                    if onu.id in results:
                        continue

                    raw_values = target_to_raw.get(onu.id, {})
                    onu_rx = _normalize_onu_rx(raw_values.get("onu_raw"))
                    olt_rx = _normalize_olt_rx(raw_values.get("olt_raw")) if supports_olt_rx else None
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
                    if read_at is not None:
                        power_cache_batch[onu.id] = payload
                    results[onu.id] = payload

                if (
                    pause_between_pon_batches_seconds > 0
                    and key_index < len(ordered_keys) - 1
                ):
                    time.sleep(pause_between_pon_batches_seconds)

            retry_candidates = [
                onu
                for onu in olt_onus
                if results.get(onu.id, {}).get("power_read_at") is None
            ]
            if retry_candidates and max_online_retry_onus > 0:
                retry_candidates = retry_candidates[:max_online_retry_onus]
                logger.warning(
                    "Power refresh OLT %s: retrying %s online ONUs with missing readings.",
                    olt.id,
                    len(retry_candidates),
                )
                for retry_index, onu in enumerate(retry_candidates):
                    if call_budget["remaining"] <= 0:
                        break

                    response = self._fetch_oids_resilient(
                        olt,
                        self._build_power_oids_for_onu(
                            onu,
                            onu_rx_oid=onu_rx_oid,
                            onu_rx_suffix=onu_rx_suffix,
                            olt_rx_oid=olt_rx_oid,
                            supports_olt_rx=supports_olt_rx,
                        ),
                        call_budget=call_budget,
                        chunk_retry_attempts=chunk_retry_attempts,
                        single_oid_retry_attempts=single_oid_retry_attempts,
                        timeout_seconds=snmp_timeout_seconds,
                        retries=snmp_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
                    if not response:
                        continue

                    raw_onu = None
                    raw_olt = None
                    for oid, value in response.items():
                        raw = None if value is None else str(value).strip()
                        if oid.startswith(f"{onu_rx_oid}."):
                            raw_onu = raw
                        elif supports_olt_rx and oid.startswith(f"{olt_rx_oid}."):
                            raw_olt = raw

                    onu_rx = _normalize_onu_rx(raw_onu)
                    olt_rx = _normalize_olt_rx(raw_olt) if supports_olt_rx else None
                    if onu_rx is None and olt_rx is None:
                        continue

                    payload = {
                        "onu_id": onu.id,
                        "slot_id": onu.slot_id,
                        "pon_id": onu.pon_id,
                        "onu_number": onu.onu_id,
                        "onu_rx_power": onu_rx,
                        "olt_rx_power": olt_rx,
                        "power_read_at": now_iso,
                    }
                    power_cache_batch[onu.id] = payload
                    results[onu.id] = payload

                    if (
                        pause_between_single_retries_seconds > 0
                        and retry_index < len(retry_candidates) - 1
                    ):
                        time.sleep(pause_between_single_retries_seconds)

            # Do not regress to empty power snapshots: retain last known cache values
            # when a forced refresh cannot produce fresh readings.
            if force_refresh:
                for onu in olt_onus:
                    current = results.get(onu.id) or {}
                    if current.get("power_read_at") is not None:
                        continue

                    cached = cached_power_by_onu.get(onu.id) or {}
                    cached_onu_rx = cached.get("onu_rx_power")
                    cached_olt_rx = cached.get("olt_rx_power") if supports_olt_rx else None
                    cached_read_at = cached.get("power_read_at")
                    if cached_onu_rx is None and cached_olt_rx is None:
                        continue

                    cached_payload = {
                        "onu_id": onu.id,
                        "slot_id": onu.slot_id,
                        "pon_id": onu.pon_id,
                        "onu_number": onu.onu_id,
                        "onu_rx_power": cached_onu_rx,
                        "olt_rx_power": cached_olt_rx,
                        "power_read_at": cached_read_at,
                    }
                    results[onu.id] = cached_payload
                    power_cache_batch[onu.id] = cached_payload

            if power_cache_batch:
                cache_service.set_many_onu_power(olt.id, power_cache_batch, ttl=ttl)

        return results


power_service = PowerService()
