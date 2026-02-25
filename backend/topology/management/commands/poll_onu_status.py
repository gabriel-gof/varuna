import logging
import math
import time
from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterator, List, Optional, Set, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from topology.models import OLT, ONU, ONULog
from topology.services.cache_service import cache_service
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.snmp_service import snmp_service
from topology.services.topology_counter_service import topology_counter_service
from topology.services.vendor_profile import map_disconnect_reason, map_status_code


logger = logging.getLogger(__name__)


def _normalize_snmp_index(value) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip(".")
    if not normalized:
        return None
    return normalized


def _iso_or_empty(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _extract_index(oid: str, base_oid: str) -> Optional[str]:
    if not oid or not base_oid:
        return None
    prefix = f"{base_oid}."
    if oid.startswith(prefix):
        return oid[len(prefix):]
    return None


def _chunked(values: List[str], size: int) -> Iterator[List[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


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


class Command(BaseCommand):
    help = "Poll ONU status via SNMP and update status/logs."

    def __init__(self):
        super().__init__()
        self.chunk_retry_attempts = 2
        self.single_oid_retry_attempts = 2
        self.retry_backoff_seconds = 0.2
        self.snmp_timeout_seconds = 1.8
        self.snmp_retries = 0
        self.max_get_call_multiplier = 18
        self.pause_between_pon_batches_seconds = 0.08

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Run polling for a specific OLT id")
        parser.add_argument("--slot-id", type=int, help="Limit polling to this slot id (requires --olt-id)")
        parser.add_argument("--pon-id", type=int, help="Limit polling to this PON id (requires --olt-id)")
        parser.add_argument(
            "--onu-id",
            action="append",
            type=int,
            help="Limit polling to specific ONU database id(s); can be repeated (requires --olt-id)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Run without writing to the database")
        parser.add_argument("--force", action="store_true", help="Ignore polling_enabled for the selected OLT(s)")
        parser.add_argument(
            "--max-olts",
            type=int,
            help="Process at most this many eligible OLTs in one command run",
        )

    def _is_due(self, olt: OLT, now) -> bool:
        if olt.next_poll_at:
            return olt.next_poll_at <= now
        if olt.last_poll_at:
            interval_seconds = max(int(olt.polling_interval_seconds or 0), 1)
            return (olt.last_poll_at + timedelta(seconds=interval_seconds)) <= now
        return True

    def _due_at(self, olt: OLT, now):
        if olt.next_poll_at:
            return olt.next_poll_at
        if olt.last_poll_at:
            interval_seconds = max(int(olt.polling_interval_seconds or 0), 1)
            return olt.last_poll_at + timedelta(seconds=interval_seconds)
        # Never-polled OLTs should be considered oldest-due.
        return now - timedelta(days=36500)

    def _snmp_get_with_attempts(
        self,
        olt: OLT,
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

    def _fetch_status_chunk_resilient(
        self,
        olt: OLT,
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
            left = self._fetch_status_chunk_resilient(
                olt,
                oids[:midpoint],
                call_budget=call_budget,
                chunk_retry_attempts=chunk_retry_attempts,
                single_oid_retry_attempts=single_oid_retry_attempts,
                timeout_seconds=timeout_seconds,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            right = self._fetch_status_chunk_resilient(
                olt,
                oids[midpoint:],
                call_budget=call_budget,
                chunk_retry_attempts=chunk_retry_attempts,
                single_oid_retry_attempts=single_oid_retry_attempts,
                timeout_seconds=timeout_seconds,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            merged: Dict[str, str] = {}
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

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        olt_id = options.get("olt_id")
        scope_slot_id = options.get("slot_id")
        scope_pon_id = options.get("pon_id")
        scope_onu_ids_raw = options.get("onu_id") or []
        scope_onu_ids: Set[int] = {int(value) for value in scope_onu_ids_raw if value is not None}
        has_scope = bool(scope_onu_ids) or scope_slot_id is not None or scope_pon_id is not None

        if has_scope and not olt_id:
            raise CommandError(
                "Scoped polling filters (--slot-id/--pon-id/--onu-id) require --olt-id."
            )

        if force:
            olt_qs = OLT.objects.filter(
                is_active=True,
                vendor_profile__is_active=True,
            ).select_related("vendor_profile")
        else:
            olt_qs = OLT.objects.filter(
                is_active=True,
                polling_enabled=True,
                vendor_profile__is_active=True,
            ).select_related("vendor_profile")

        if olt_id:
            olt_qs = olt_qs.filter(id=olt_id)

        now = timezone.now()
        olts = list(olt_qs)
        if not olts:
            self.stdout.write("No OLTs eligible for polling.")
            return

        if not force and not olt_id:
            due_olts = [
                olt for olt in olts
                if self._is_due(olt, now)
                and not (olt.snmp_reachable is False and (olt.snmp_failure_count or 0) >= 2)
            ]
        else:
            due_olts = olts

        if not due_olts:
            self.stdout.write("No OLTs due for polling.")
            return

        due_olts.sort(key=lambda candidate: self._due_at(candidate, now))
        max_olts = options.get("max_olts")
        if max_olts is not None and int(max_olts) > 0 and len(due_olts) > int(max_olts):
            self.stdout.write(
                f"Capping polling run to {int(max_olts)} OLTs out of {len(due_olts)} due."
            )
            due_olts = due_olts[: int(max_olts)]

        for olt in due_olts:
            self._poll_for_olt(
                olt,
                dry_run=options.get("dry_run", False),
                scope_slot_id=scope_slot_id,
                scope_pon_id=scope_pon_id,
                scope_onu_ids=scope_onu_ids,
            )

    def _poll_for_olt(
        self,
        olt: OLT,
        dry_run: bool = False,
        *,
        scope_slot_id: Optional[int] = None,
        scope_pon_id: Optional[int] = None,
        scope_onu_ids: Optional[Set[int]] = None,
    ) -> None:
        oid_templates = olt.vendor_profile.oid_templates or {}
        status_cfg = oid_templates.get("status", {})
        status_oid = status_cfg.get("onu_status_oid")
        status_map = status_cfg.get("status_map", {})
        previous_poll_at = olt.last_poll_at
        previous_snmp_reachable = bool(olt.snmp_reachable)
        scoped_refresh = bool(scope_onu_ids) or scope_slot_id is not None or scope_pon_id is not None

        if not status_oid:
            self.stdout.write(f"OLT {olt.id} missing status OID, skipping.")
            return

        onus_qs = ONU.objects.filter(olt=olt, is_active=True)
        if scope_slot_id is not None:
            onus_qs = onus_qs.filter(slot_id=scope_slot_id)
        if scope_pon_id is not None:
            onus_qs = onus_qs.filter(pon_id=scope_pon_id)
        if scope_onu_ids:
            onus_qs = onus_qs.filter(id__in=scope_onu_ids)

        onus = list(onus_qs.order_by("slot_id", "pon_id", "onu_id"))
        if not onus:
            if scoped_refresh:
                self.stdout.write(
                    f"OLT {olt.id}: no active ONUs matched scoped polling filters."
                )
            else:
                self.stdout.write(f"OLT {olt.id}: no active ONUs found.")
            return

        onu_index_map: Dict[int, str] = {}
        for onu in onus:
            normalized_index = _normalize_snmp_index(onu.snmp_index)
            if normalized_index:
                onu_index_map[onu.id] = normalized_index

        status_oids = [f"{status_oid}.{index}" for index in onu_index_map.values()]
        if not status_oids:
            self.stdout.write(f"OLT {olt.id}: no active ONUs with SNMP index.")
            return

        statuses: Dict[str, str] = {}
        chunk_size = _to_int(status_cfg.get("get_chunk_size"))
        if chunk_size is None:
            chunk_size = 20
        chunk_size = max(1, min(chunk_size, 128))
        chunk_retry_attempts = _to_int(status_cfg.get("chunk_retry_attempts"))
        if chunk_retry_attempts is None:
            chunk_retry_attempts = self.chunk_retry_attempts
        chunk_retry_attempts = max(1, min(chunk_retry_attempts, 6))
        single_oid_retry_attempts = _to_int(status_cfg.get("single_oid_retry_attempts"))
        if single_oid_retry_attempts is None:
            single_oid_retry_attempts = self.single_oid_retry_attempts
        single_oid_retry_attempts = max(1, min(single_oid_retry_attempts, 6))
        retry_backoff_seconds = _to_float(status_cfg.get("retry_backoff_seconds"))
        if retry_backoff_seconds is None:
            retry_backoff_seconds = self.retry_backoff_seconds
        retry_backoff_seconds = max(0.0, min(retry_backoff_seconds, 5.0))
        snmp_timeout_seconds = _to_float(status_cfg.get("snmp_timeout_seconds"))
        if snmp_timeout_seconds is None:
            snmp_timeout_seconds = self.snmp_timeout_seconds
        snmp_timeout_seconds = max(0.3, min(snmp_timeout_seconds, 10.0))
        snmp_retries = _to_int(status_cfg.get("snmp_retries"))
        if snmp_retries is None:
            snmp_retries = self.snmp_retries
        snmp_retries = max(0, min(snmp_retries, 3))
        max_get_call_multiplier = _to_int(status_cfg.get("max_get_call_multiplier"))
        if max_get_call_multiplier is None:
            max_get_call_multiplier = self.max_get_call_multiplier
        max_get_call_multiplier = max(2, min(max_get_call_multiplier, 200))
        pause_between_pon_batches_seconds = _to_float(status_cfg.get("pause_between_pon_batches_seconds"))
        if pause_between_pon_batches_seconds is None:
            pause_between_pon_batches_seconds = self.pause_between_pon_batches_seconds
        pause_between_pon_batches_seconds = max(0.0, min(pause_between_pon_batches_seconds, 5.0))
        max_runtime_seconds = _to_float(status_cfg.get("max_runtime_seconds"))
        if max_runtime_seconds is None:
            max_runtime_seconds = 180.0
        max_runtime_seconds = max(30.0, min(max_runtime_seconds, 1800.0))
        failed_chunks = 0
        estimated_calls = max(1, math.ceil(len(status_oids) / chunk_size))
        call_budget = {
            "remaining": max(
                estimated_calls + 32,
                estimated_calls * max_get_call_multiplier,
            )
        }

        pon_groups: Dict[Tuple[int, int], List[ONU]] = defaultdict(list)
        for onu in onus:
            pon_groups[(int(onu.slot_id or -1), int(onu.pon_id or -1))].append(onu)

        ordered_pon_keys = sorted(pon_groups.keys(), key=lambda item: (item[0], item[1]))
        logger.info(
            "Status polling OLT %s: paced PON batches (active_onus=%s, pons=%s, chunk_size=%s, timeout=%.2fs).",
            olt.id,
            len(onus),
            len(ordered_pon_keys),
            chunk_size,
            snmp_timeout_seconds,
        )

        budget_exhausted = False
        runtime_exhausted = False
        poll_started_at = time.monotonic()
        for pon_index, pon_key in enumerate(ordered_pon_keys):
            pon_onus = sorted(pon_groups[pon_key], key=lambda item: int(item.onu_id or 0))
            pon_oids = [
                f"{status_oid}.{onu_index_map[onu.id]}"
                for onu in pon_onus
                if onu.id in onu_index_map
            ]
            for chunk in _chunked(pon_oids, chunk_size):
                if (time.monotonic() - poll_started_at) >= max_runtime_seconds:
                    runtime_exhausted = True
                    budget_exhausted = True
                    logger.warning(
                        "Status polling OLT %s: runtime budget exhausted (max_runtime_seconds=%s); keeping partial status snapshot.",
                        olt.id,
                        max_runtime_seconds,
                    )
                    break
                if call_budget["remaining"] <= 0:
                    budget_exhausted = True
                    logger.warning(
                        "Status polling OLT %s: call budget exhausted; keeping partial status snapshot.",
                        olt.id,
                    )
                    break
                response = self._fetch_status_chunk_resilient(
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
                    failed_chunks += 1
                    continue
                for oid, value in response.items():
                    index = _extract_index(oid, status_oid)
                    if not index:
                        continue
                    statuses[index] = "" if value is None else str(value).strip()

            if budget_exhausted:
                break
            if (
                pause_between_pon_batches_seconds > 0
                and pon_index < len(ordered_pon_keys) - 1
            ):
                time.sleep(pause_between_pon_batches_seconds)

        # Second-pass: fetch disconnect reason for offline ONUs (Huawei-style split OIDs)
        disconnect_reasons: Dict[str, str] = {}
        disconnect_reason_oid = status_cfg.get("disconnect_reason_oid")
        disconnect_reason_map = status_cfg.get("disconnect_reason_map", {})
        if disconnect_reason_oid and statuses and not budget_exhausted:
            offline_indices = []
            for onu in onus:
                normalized_index = onu_index_map.get(onu.id)
                if not normalized_index:
                    continue
                status_code = statuses.get(normalized_index)
                if status_code is None:
                    continue
                mapped = map_status_code(status_code, status_map)
                if mapped["status"] == ONU.STATUS_OFFLINE:
                    offline_indices.append(normalized_index)

            if offline_indices:
                reason_oids = [f"{disconnect_reason_oid}.{idx}" for idx in offline_indices]
                for chunk in _chunked(reason_oids, chunk_size):
                    if call_budget["remaining"] <= 0:
                        break
                    response = self._fetch_status_chunk_resilient(
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
                    for oid, value in response.items():
                        index = _extract_index(oid, disconnect_reason_oid)
                        if index:
                            disconnect_reasons[index] = "" if value is None else str(value).strip()

        now = timezone.now()
        ttl = getattr(settings, "STATUS_CACHE_TTL", 180)

        if not statuses:
            if not dry_run and not scoped_refresh:
                mark_olt_unreachable(
                    olt,
                    error=(
                        "No status data returned "
                        f"(failed_chunks={failed_chunks}, requested={len(status_oids)}, "
                        f"runtime_exhausted={runtime_exhausted})"
                    ),
                )
                self._mark_poll_result(olt, now)
            self.stdout.write(f"OLT {olt.id}: no status data returned.")
            return

        if not dry_run and not scoped_refresh:
            mark_olt_reachable(olt)
        if len(statuses) < len(status_oids):
            logger.warning(
                "Status polling OLT %s: partial snapshot received (%s/%s indexes, failed_chunks=%s); preserving previous state for missing ONUs.",
                olt.id,
                len(statuses),
                len(status_oids),
                failed_chunks,
            )

        open_logs_by_onu: Dict[int, ONULog] = {}
        open_logs_qs = ONULog.objects.filter(
            onu__olt=olt,
            onu__is_active=True,
            offline_until__isnull=True,
        )
        if scoped_refresh:
            open_logs_qs = open_logs_qs.filter(onu_id__in=[onu.id for onu in onus])
        for log in open_logs_qs.order_by('-offline_since'):
            open_logs_by_onu.setdefault(log.onu_id, log)

        updated = online = offline = unknown = missing = missing_preserved = 0
        onus_to_update: List[ONU] = []
        logs_to_close: List[ONULog] = []
        logs_to_reason_update: List[ONULog] = []
        new_logs: List[ONULog] = []
        cache_batch: Dict[int, Dict] = {}

        for onu in onus:
            normalized_index = onu_index_map.get(onu.id)
            status_code = statuses.get(normalized_index) if normalized_index else None
            if status_code is None:
                missing += 1
                if dry_run:
                    continue
                current_status = onu.status if onu.status in {
                    ONU.STATUS_ONLINE,
                    ONU.STATUS_OFFLINE,
                    ONU.STATUS_UNKNOWN,
                } else ONU.STATUS_UNKNOWN
                open_log = open_logs_by_onu.get(onu.id)
                disconnect_reason = ""
                offline_since = ""
                disconnect_window_start = ""
                disconnect_window_end = ""
                if current_status == ONU.STATUS_OFFLINE:
                    disconnect_reason = (
                        open_log.disconnect_reason
                        if open_log and open_log.disconnect_reason
                        else ONULog.REASON_UNKNOWN
                    )
                    if open_log and open_log.offline_since:
                        offline_since = open_log.offline_since.isoformat()
                    disconnect_window_start = _iso_or_empty(
                        open_log.disconnect_window_start if open_log else None
                    )
                    disconnect_window_end = _iso_or_empty(
                        open_log.disconnect_window_end if open_log else None
                    )
                elif current_status == ONU.STATUS_UNKNOWN:
                    disconnect_reason = ONULog.REASON_UNKNOWN
                cache_batch[onu.id] = {
                    "status": current_status,
                    "disconnect_reason": disconnect_reason,
                    "offline_since": offline_since,
                    "disconnect_window_start": disconnect_window_start,
                    "disconnect_window_end": disconnect_window_end,
                }
                missing_preserved += 1
                continue

            mapped = map_status_code(status_code, status_map)
            new_status = mapped["status"]
            reason = mapped["reason"] if new_status != ONU.STATUS_ONLINE else ""

            if new_status == ONU.STATUS_OFFLINE and disconnect_reason_oid and normalized_index:
                raw_reason = disconnect_reasons.get(normalized_index)
                if raw_reason is not None:
                    reason = map_disconnect_reason(raw_reason, disconnect_reason_map)

            if new_status == ONU.STATUS_ONLINE:
                online += 1
            elif new_status == ONU.STATUS_OFFLINE:
                offline += 1
            else:
                unknown += 1

            if dry_run:
                updated += 1
                continue

            open_log = open_logs_by_onu.get(onu.id)
            active_log = open_log

            if new_status == ONU.STATUS_ONLINE:
                if open_log and open_log.offline_until is None:
                    open_log.offline_until = now
                    logs_to_close.append(open_log)
                active_log = None
            elif new_status == ONU.STATUS_OFFLINE:
                if onu.status == ONU.STATUS_ONLINE or not open_log:
                    window_start = None
                    window_end = None
                    if (
                        onu.status == ONU.STATUS_ONLINE
                        and previous_poll_at
                        and previous_snmp_reachable
                    ):
                        window_start = previous_poll_at
                        window_end = now
                    active_log = ONULog(
                        onu=onu,
                        offline_since=now,
                        disconnect_reason=reason or ONULog.REASON_UNKNOWN,
                        disconnect_window_start=window_start,
                        disconnect_window_end=window_end,
                    )
                    new_logs.append(active_log)
                    open_logs_by_onu[onu.id] = active_log
                elif reason and open_log.disconnect_reason != reason:
                    open_log.disconnect_reason = reason
                    logs_to_reason_update.append(open_log)
            else:
                # Keep existing offline log untouched when state is unknown.
                active_log = open_log

            if onu.status != new_status:
                onu.status = new_status
                onus_to_update.append(onu)

            offline_since = ""
            disconnect_window_start = ""
            disconnect_window_end = ""
            if new_status == ONU.STATUS_OFFLINE and active_log:
                offline_since = active_log.offline_since.isoformat()
                disconnect_window_start = _iso_or_empty(active_log.disconnect_window_start)
                disconnect_window_end = _iso_or_empty(active_log.disconnect_window_end)

            cache_batch[onu.id] = {
                "status": new_status,
                "disconnect_reason": reason,
                "offline_since": offline_since,
                "disconnect_window_start": disconnect_window_start,
                "disconnect_window_end": disconnect_window_end,
            }

            updated += 1

        if not dry_run:
            if cache_batch:
                cache_service.set_many_onu_status(olt.id, cache_batch, ttl=ttl)
            with transaction.atomic():
                if new_logs:
                    ONULog.objects.bulk_create(new_logs)
                if logs_to_close:
                    ONULog.objects.bulk_update(logs_to_close, ['offline_until'])
                if logs_to_reason_update:
                    ONULog.objects.bulk_update(logs_to_reason_update, ['disconnect_reason'])
                if onus_to_update:
                    ONU.objects.bulk_update(onus_to_update, ['status'])
                if not scoped_refresh:
                    self._mark_poll_result(olt, now)
            try:
                topology_counter_service.refresh_olt(olt.id)
            except Exception:
                logger.exception("OLT %s polling: failed to refresh cached topology counters.", olt.id)
            cache_service.invalidate_topology_api_cache(olt.id)

        self.stdout.write(
            f"OLT {olt.id}: polled {updated} ONUs "
            f"(online={online}, offline={offline}, unknown={unknown}, missing={missing}, missing_preserved={missing_preserved}, failed_chunks={failed_chunks})."
        )

    def _mark_poll_result(self, olt: OLT, now):
        next_at = now + timedelta(seconds=olt.polling_interval_seconds or 0)
        olt.last_poll_at = now
        olt.next_poll_at = next_at
        olt.save(update_fields=['last_poll_at', 'next_poll_at'])
