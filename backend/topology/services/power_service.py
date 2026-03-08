"""
ONU power collection service.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings

from topology.models import ONU
from topology.services.fit_collector_service import fit_collector_service
from topology.services.power_values import normalize_power_value
from topology.services.vendor_profile import COLLECTOR_TYPE_FIT_TELNET, get_collector_type
from topology.services.zabbix_service import zabbix_service


logger = logging.getLogger(__name__)


def _to_int_or_none(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class PowerService:
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

    @staticmethod
    def _resolve_zabbix_power_patterns(olt) -> Tuple[str, str]:
        templates = (olt.vendor_profile.oid_templates or {}) if isinstance(olt.vendor_profile.oid_templates, dict) else {}
        zabbix_cfg = templates.get("zabbix", {}) if isinstance(templates.get("zabbix", {}), dict) else {}
        onu_pattern = str(zabbix_cfg.get("onu_rx_item_key_pattern") or "onuRxPower[{index}]").strip()
        olt_pattern = str(zabbix_cfg.get("olt_rx_item_key_pattern") or "oltRxPower[{index}]").strip()
        return onu_pattern, olt_pattern

    @staticmethod
    def _build_zabbix_power_keys(indexes: List[str], onu_pattern: str, olt_pattern: str) -> List[str]:
        keys: List[str] = []
        for index in indexes:
            normalized_index = str(index or "").strip(".")
            if not normalized_index:
                continue
            keys.append(onu_pattern.replace("{index}", normalized_index))
            if olt_pattern:
                keys.append(olt_pattern.replace("{index}", normalized_index))
        seen = set()
        deduped = []
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def _refresh_for_olt_fit(
        self,
        olt_onus: List[ONU],
        *,
        results: Dict[int, Dict],
        metrics: Dict[str, int],
    ) -> None:
        olt = olt_onus[0].olt
        eligible_onus: List[ONU] = []

        for onu in olt_onus:
            if int(getattr(onu, "onu_id", 0) or 0) > 64:
                metrics["skipped_unsupported"] = int(metrics.get("skipped_unsupported", 0) or 0) + 1
                results[onu.id] = self._build_empty_payload(onu, skipped_reason="unsupported_onu_id")
                continue
            eligible_onus.append(onu)

        if not eligible_onus:
            logger.warning(
                "Power refresh OLT %s: no FIT ONUs eligible for optical DDM (active=%s, online=%s, skipped_unsupported=%s).",
                olt.id,
                metrics.get("total_active", len(olt_onus)),
                metrics.get("online", 0),
                metrics.get("skipped_unsupported", 0),
            )
            return

        fit_map = fit_collector_service.fetch_power_for_onus(olt, eligible_onus)
        for onu in eligible_onus:
            results[onu.id] = fit_map.get(onu.id) or self._build_empty_payload(onu)

        logger.info(
            "Power refresh OLT %s: collected FIT ONU RX values (active=%s, online=%s, skipped_offline=%s, skipped_unknown=%s, skipped_unsupported=%s).",
            olt.id,
            metrics.get("total_active", len(olt_onus)),
            metrics.get("online", len(eligible_onus)),
            metrics.get("skipped_offline", 0),
            metrics.get("skipped_unknown", 0),
            metrics.get("skipped_unsupported", 0),
        )

    def refresh_for_onus(
        self,
        onus: Iterable[ONU],
        force_refresh: bool = True,
        *,
        refresh_upstream: bool = False,
        force_upstream: bool = False,
    ) -> Dict[int, Dict]:
        base_onus = [onu for onu in onus if onu and onu.olt_id and onu.is_active]
        if not base_onus:
            return {}

        stale_margin_seconds = int(getattr(settings, "ZABBIX_POWER_STALE_MARGIN_SECONDS", 90) or 90)
        refresh_upstream_max_items = int(
            getattr(settings, "ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS", 512) or 512
        )
        results: Dict[int, Dict] = {}
        counters_by_olt: Dict[int, Dict[str, int]] = defaultdict(
            lambda: {
                "total_active": 0,
                "online": 0,
                "skipped_offline": 0,
                "skipped_unknown": 0,
                "skipped_not_online": 0,
                "skipped_unsupported": 0,
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
                skipped_reason = "offline"
                counters_by_olt[onu.olt_id]["skipped_offline"] += 1
            elif status_value == ONU.STATUS_UNKNOWN:
                skipped_reason = "unknown"
                counters_by_olt[onu.olt_id]["skipped_unknown"] += 1
            else:
                skipped_reason = "not_online"

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
            if get_collector_type(olt) == COLLECTOR_TYPE_FIT_TELNET:
                self._refresh_for_olt_fit(
                    olt_onus,
                    results=results,
                    metrics=metrics,
                )
                continue
            refresh_requested_epoch: Optional[int] = None
            refresh_clock_grace_seconds = int(
                getattr(settings, "ZABBIX_REFRESH_CLOCK_GRACE_SECONDS", 15) or 15
            )
            refresh_wait_seconds = int(
                getattr(settings, "ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS", 12) or 12
            )
            refresh_wait_step_seconds = max(
                1,
                int(getattr(settings, "ZABBIX_REFRESH_UPSTREAM_WAIT_STEP_SECONDS", 2) or 2),
            )
            stale_power_max_age_seconds = max(
                int(getattr(olt, "power_interval_seconds", 0) or 0) * 3 + stale_margin_seconds,
                stale_margin_seconds + 300,
            )

            onu_pattern, olt_pattern = self._resolve_zabbix_power_patterns(olt)
            if not onu_pattern:
                logger.warning("Power refresh OLT %s: missing Zabbix ONU RX item key pattern.", olt.id)
                for onu in olt_onus:
                    results[onu.id] = self._build_empty_payload(onu)
                continue

            index_to_onu: Dict[str, ONU] = {}
            for onu in olt_onus:
                normalized_index = str(getattr(onu, "snmp_index", "") or "").strip(".")
                if not normalized_index:
                    results[onu.id] = self._build_empty_payload(onu)
                    continue
                index_to_onu[normalized_index] = onu

            if not index_to_onu:
                logger.warning("Power refresh OLT %s: no Zabbix topology indexes available for eligible ONUs.", olt.id)
                continue

            should_refresh_upstream = bool(refresh_upstream)
            if refresh_upstream and len(index_to_onu) > refresh_upstream_max_items and not force_upstream:
                should_refresh_upstream = False
                logger.info(
                    "Power refresh OLT %s: skipping immediate upstream refresh (requested=%s > max=%s).",
                    olt.id,
                    len(index_to_onu),
                    refresh_upstream_max_items,
                )
            elif refresh_upstream and len(index_to_onu) > refresh_upstream_max_items and force_upstream:
                logger.warning(
                    "Power refresh OLT %s: bypassing upstream refresh cap (requested=%s > max=%s).",
                    olt.id,
                    len(index_to_onu),
                    refresh_upstream_max_items,
                )

            if should_refresh_upstream:
                try:
                    refresh_requested_epoch = int(time.time())
                    hostid = zabbix_service.get_hostid(olt)
                    if hostid:
                        keys = self._build_zabbix_power_keys(list(index_to_onu.keys()), onu_pattern, olt_pattern)
                        if keys:
                            executed = zabbix_service.execute_items_now_by_keys(hostid, keys)
                            logger.info(
                                "Power refresh OLT %s: requested immediate Zabbix execution for %s item(s).",
                                olt.id,
                                executed,
                            )
                            if executed:
                                time.sleep(0.8)
                except Exception:
                    logger.exception("Power refresh OLT %s: failed to request immediate Zabbix item execution.", olt.id)

            refresh_required_epoch = None
            if refresh_requested_epoch is not None:
                refresh_required_epoch = max(0, int(refresh_requested_epoch) - refresh_clock_grace_seconds)

            fetch_attempts = 1
            if refresh_required_epoch is not None and refresh_wait_seconds > 0:
                fetch_attempts = max(1, int(refresh_wait_seconds // refresh_wait_step_seconds) + 1)

            zabbix_map = {}
            pending_indexes = 0
            for attempt in range(fetch_attempts):
                try:
                    zabbix_map, _ = zabbix_service.fetch_power_by_index(
                        olt,
                        index_to_onu.keys(),
                        onu_rx_item_key_pattern=onu_pattern,
                        olt_rx_item_key_pattern=olt_pattern,
                    )
                except Exception as exc:
                    logger.warning("Power refresh OLT %s via Zabbix failed: %s", olt.id, exc)
                    zabbix_map = {}
                    break

                if refresh_required_epoch is None:
                    break

                pending_indexes = 0
                for index in index_to_onu.keys():
                    row = zabbix_map.get(index) or {}
                    clock_epoch = _to_int_or_none(row.get("power_clock_epoch")) or 0
                    onu_rx = normalize_power_value(row.get("onu_rx_power"))
                    olt_rx = normalize_power_value(row.get("olt_rx_power"))
                    if (onu_rx is not None or olt_rx is not None) and clock_epoch >= refresh_required_epoch:
                        continue
                    pending_indexes += 1

                if pending_indexes == 0:
                    break

                if attempt < (fetch_attempts - 1):
                    time.sleep(refresh_wait_step_seconds)

            if refresh_required_epoch is not None and pending_indexes > 0:
                logger.info(
                    "Power refresh OLT %s: using partial refresh result (%s item(s) still pending after %s attempt(s)).",
                    olt.id,
                    pending_indexes,
                    fetch_attempts,
                )

            for index, onu in index_to_onu.items():
                row = zabbix_map.get(index) or {}
                onu_rx = normalize_power_value(row.get("onu_rx_power"))
                olt_rx = normalize_power_value(row.get("olt_rx_power"))
                clock_epoch = _to_int_or_none(row.get("power_clock_epoch"))
                if clock_epoch is not None and clock_epoch > 0:
                    power_age_seconds = max(0, int(time.time()) - clock_epoch)
                    if power_age_seconds > stale_power_max_age_seconds:
                        onu_rx = None
                        olt_rx = None
                        clock_epoch = None
                read_at = row.get("power_read_at") if (onu_rx is not None or olt_rx is not None) else None
                payload = {
                    "onu_id": onu.id,
                    "slot_id": onu.slot_id,
                    "pon_id": onu.pon_id,
                    "onu_number": onu.onu_id,
                    "onu_rx_power": onu_rx,
                    "olt_rx_power": olt_rx,
                    "power_read_at": read_at,
                }
                results[onu.id] = payload

            logger.info(
                "Power refresh OLT %s: queried online ONUs (active=%s, online=%s, skipped_offline=%s, skipped_unknown=%s).",
                olt.id,
                metrics.get("total_active", len(olt_onus)),
                metrics.get("online", len(olt_onus)),
                metrics.get("skipped_offline", 0),
                metrics.get("skipped_unknown", 0),
            )

        return results


power_service = PowerService()
