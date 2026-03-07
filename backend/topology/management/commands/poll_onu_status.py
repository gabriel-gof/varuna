import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional, Set, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from topology.models import OLT, ONU, ONULog
from topology.services.maintenance_runtime import get_status_snapshot_max_age_seconds
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.topology_counter_service import topology_counter_service
from topology.services.zabbix_service import zabbix_service


logger = logging.getLogger(__name__)


def _normalize_snmp_index(value) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip(".")
    if not normalized:
        return None
    return normalized


def _to_int_or_none(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = "Poll ONU status via Zabbix and update status/logs."

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
        parser.add_argument(
            "--refresh-upstream",
            action="store_true",
            help="Ask Zabbix to execute involved status items before reading values.",
        )
        parser.add_argument(
            "--force-upstream",
            action="store_true",
            help="Bypass refresh-upstream item cap for manual/scoped runs.",
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
        return now - timedelta(days=36500)

    @staticmethod
    def _resolve_zabbix_status_patterns(olt: OLT) -> Tuple[str, str]:
        templates = (olt.vendor_profile.oid_templates or {}) if isinstance(olt.vendor_profile.oid_templates, dict) else {}
        zabbix_cfg = templates.get("zabbix", {}) if isinstance(templates.get("zabbix", {}), dict) else {}
        status_pattern = str(zabbix_cfg.get("status_item_key_pattern") or "onuStatusValue[{index}]").strip()
        reason_pattern = str(zabbix_cfg.get("reason_item_key_pattern") or "onuDisconnectReason[{index}]").strip()
        return status_pattern, reason_pattern

    @staticmethod
    def _build_zabbix_status_keys(indexes: List[str], status_pattern: str, reason_pattern: str) -> List[str]:
        keys: List[str] = []
        for index in indexes:
            normalized_index = str(index or "").strip(".")
            if not normalized_index:
                continue
            keys.append(status_pattern.replace("{index}", normalized_index))
            if reason_pattern:
                keys.append(reason_pattern.replace("{index}", normalized_index))
        # stable de-dup while preserving order
        seen = set()
        deduped = []
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        olt_id = options.get("olt_id")
        scope_slot_id = options.get("slot_id")
        scope_pon_id = options.get("pon_id")
        scope_onu_ids_raw = options.get("onu_id") or []
        scope_onu_ids: Set[int] = {int(value) for value in scope_onu_ids_raw if value is not None}
        has_scope = bool(scope_onu_ids) or scope_slot_id is not None or scope_pon_id is not None

        if has_scope and not olt_id:
            raise CommandError("Scoped polling filters (--slot-id/--pon-id/--onu-id) require --olt-id.")

        if force:
            olt_qs = OLT.objects.filter(is_active=True, vendor_profile__is_active=True).select_related("vendor_profile")
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
                olt
                for olt in olts
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
            self.stdout.write(f"Capping polling run to {int(max_olts)} OLTs out of {len(due_olts)} due.")
            due_olts = due_olts[: int(max_olts)]

        for olt in due_olts:
            self._poll_for_olt(
                olt,
                dry_run=options.get("dry_run", False),
                scope_slot_id=scope_slot_id,
                scope_pon_id=scope_pon_id,
                scope_onu_ids=scope_onu_ids,
                refresh_upstream=bool(options.get("refresh_upstream", False)),
                force_upstream=bool(options.get("force_upstream", False)),
            )

    def _poll_for_olt(
        self,
        olt: OLT,
        dry_run: bool = False,
        *,
        scope_slot_id: Optional[int] = None,
        scope_pon_id: Optional[int] = None,
        scope_onu_ids: Optional[Set[int]] = None,
        refresh_upstream: bool = False,
        force_upstream: bool = False,
    ) -> None:
        scoped_refresh = bool(scope_onu_ids) or scope_slot_id is not None or scope_pon_id is not None

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
                self.stdout.write(f"OLT {olt.id}: no active ONUs matched scoped polling filters.")
            else:
                self.stdout.write(f"OLT {olt.id}: no active ONUs found.")
            return

        onu_index_map: Dict[int, str] = {}
        for onu in onus:
            normalized_index = _normalize_snmp_index(onu.snmp_index)
            if normalized_index:
                onu_index_map[onu.id] = normalized_index

        if not onu_index_map:
            self.stdout.write(f"OLT {olt.id}: no active ONUs with topology index.")
            return

        status_item_key_pattern, reason_item_key_pattern = self._resolve_zabbix_status_patterns(olt)
        if not status_item_key_pattern:
            self.stdout.write(f"OLT {olt.id}: missing zabbix status item key pattern.")
            return

        stale_status_max_age_seconds = get_status_snapshot_max_age_seconds(olt)

        refresh_upstream_max_items = int(
            getattr(settings, "ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS", 512) or 512
        )
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

        if refresh_upstream and len(onu_index_map) > refresh_upstream_max_items and not force_upstream:
            logger.info(
                "Status polling OLT %s: skipping immediate upstream refresh (requested=%s > max=%s).",
                olt.id,
                len(onu_index_map),
                refresh_upstream_max_items,
            )
            refresh_upstream = False
        elif refresh_upstream and len(onu_index_map) > refresh_upstream_max_items and force_upstream:
            logger.warning(
                "Status polling OLT %s: bypassing upstream refresh cap (requested=%s > max=%s) due to --force-upstream.",
                olt.id,
                len(onu_index_map),
                refresh_upstream_max_items,
            )

        if refresh_upstream:
            try:
                refresh_requested_epoch = int(time.time())
                hostid = zabbix_service.get_hostid(olt)
                if hostid:
                    keys = self._build_zabbix_status_keys(
                        list(onu_index_map.values()),
                        status_item_key_pattern,
                        reason_item_key_pattern,
                    )
                    if keys:
                        executed = zabbix_service.execute_items_now_by_keys(hostid, keys)
                        logger.info(
                            "Status polling OLT %s: requested immediate Zabbix execution for %s item(s).",
                            olt.id,
                            executed,
                        )
                        if executed:
                            time.sleep(0.8)
            except Exception:
                logger.exception("Status polling OLT %s: failed to request immediate Zabbix item execution.", olt.id)

            # Avoid accepting stale cached status values when a forced upstream
            # refresh is requested and Zabbix already knows the SNMP interface
            # is unavailable (for example, VPN/network outage).
            try:
                reachable, detail = zabbix_service.check_olt_reachability(
                    olt,
                )
            except Exception as exc:
                reachable, detail = False, str(exc)

            if not reachable:
                if not dry_run:
                    mark_olt_unreachable(olt, error=detail or "Zabbix reported OLT unreachable")
                normalized_detail = str(detail or "Zabbix reported OLT unreachable").strip()
                self.stdout.write(
                    f"OLT {olt.id}: collector unreachable before status fetch ({normalized_detail})."
                )
                return

        refresh_required_epoch = None
        if refresh_requested_epoch is not None:
            refresh_required_epoch = max(0, int(refresh_requested_epoch) - refresh_clock_grace_seconds)

        fetch_attempts = 1
        if refresh_required_epoch is not None and refresh_wait_seconds > 0:
            fetch_attempts = max(1, int(refresh_wait_seconds // refresh_wait_step_seconds) + 1)

        zabbix_status_map = {}
        newest_status_clock_epoch = 0
        for attempt in range(fetch_attempts):
            try:
                zabbix_status_map, _ = zabbix_service.fetch_status_by_index(
                    olt,
                    onu_index_map.values(),
                    status_item_key_pattern=status_item_key_pattern,
                    reason_item_key_pattern=reason_item_key_pattern,
                    include_meta=True,
                )
            except Exception as exc:
                logger.warning("Status polling OLT %s via Zabbix failed: %s", olt.id, exc)
                zabbix_status_map = {}
                break

            newest_status_clock_epoch = max(
                (_to_int_or_none((payload or {}).get("status_clock_epoch")) or 0)
                for payload in zabbix_status_map.values()
            ) if zabbix_status_map else 0

            if refresh_required_epoch is None:
                break
            if newest_status_clock_epoch >= refresh_required_epoch:
                break
            if attempt < (fetch_attempts - 1):
                time.sleep(refresh_wait_step_seconds)

        status_by_index: Dict[str, Dict[str, str]] = {}
        for index, payload in zabbix_status_map.items():
            status_value = str((payload or {}).get("status") or "").strip().lower()
            reason_value = str((payload or {}).get("reason") or "").strip().lower()
            status_clock_epoch = _to_int_or_none((payload or {}).get("status_clock_epoch"))
            status_itemid = str((payload or {}).get("status_itemid") or "").strip()

            if status_value == ONU.STATUS_ONLINE:
                status_by_index[index] = {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": status_clock_epoch,
                    "status_itemid": status_itemid,
                }
            elif status_value == ONU.STATUS_OFFLINE:
                normalized_reason = reason_value if reason_value in {
                    ONULog.REASON_LINK_LOSS,
                    ONULog.REASON_DYING_GASP,
                    ONULog.REASON_UNKNOWN,
                } else ONULog.REASON_UNKNOWN
                status_by_index[index] = {
                    "status": ONU.STATUS_OFFLINE,
                    "reason": normalized_reason,
                    "status_clock_epoch": status_clock_epoch,
                    "status_itemid": status_itemid,
                }
            else:
                status_by_index[index] = {
                    "status": ONU.STATUS_UNKNOWN,
                    "reason": ONULog.REASON_UNKNOWN,
                    "status_clock_epoch": status_clock_epoch,
                    "status_itemid": status_itemid,
                }

        now = timezone.now()
        now_epoch = int(now.timestamp())

        if not status_by_index:
            requested_count = len(onu_index_map)
            if not dry_run:
                mark_olt_unreachable(
                    olt,
                    error=(
                        "No status data returned "
                        f"(requested={requested_count}, collector=zabbix)"
                    ),
                )
            self.stdout.write(f"OLT {olt.id}: no status data returned.")
            return

        fresh_indexes: Set[str] = set()
        stale_indexes: Set[str] = set()
        for index, mapped in status_by_index.items():
            status_clock_epoch = _to_int_or_none((mapped or {}).get("status_clock_epoch"))
            if status_clock_epoch is None or status_clock_epoch <= 0:
                stale_indexes.add(index)
                continue
            status_age_seconds = max(0, now_epoch - status_clock_epoch)
            if status_age_seconds > stale_status_max_age_seconds:
                stale_indexes.add(index)
                continue
            fresh_indexes.add(index)

        if not fresh_indexes:
            requested_count = len(onu_index_map)
            if not dry_run:
                mark_olt_unreachable(
                    olt,
                    error=(
                        "Only stale status data returned "
                        f"(requested={requested_count}, collector=zabbix)"
                    ),
                )
            self.stdout.write(f"OLT {olt.id}: only stale status data returned.")
            return

        if not dry_run:
            mark_olt_reachable(olt)

        transition_item_clock_map: Dict[str, int] = {}
        for onu in onus:
            normalized_index = onu_index_map.get(onu.id)
            mapped = status_by_index.get(normalized_index) if normalized_index else None
            if normalized_index and normalized_index in stale_indexes:
                mapped = None
            if not mapped:
                continue
            if onu.status != ONU.STATUS_ONLINE:
                continue
            if mapped.get("status") != ONU.STATUS_OFFLINE:
                continue
            itemid = str(mapped.get("status_itemid") or "").strip()
            clock_epoch = _to_int_or_none(mapped.get("status_clock_epoch"))
            if not itemid or clock_epoch is None or clock_epoch <= 0:
                continue
            transition_item_clock_map[itemid] = clock_epoch

        transition_history_max_items = int(
            getattr(settings, "ZABBIX_DISCONNECT_HISTORY_MAX_ITEMS", 512) or 512
        )
        previous_sample_by_itemid: Dict[str, Dict[str, Optional[str]]] = {}
        if transition_item_clock_map:
            if len(transition_item_clock_map) > transition_history_max_items:
                logger.info(
                    "Status polling OLT %s: skipping transition history lookup (requested=%s > max=%s).",
                    olt.id,
                    len(transition_item_clock_map),
                    transition_history_max_items,
                )
            else:
                try:
                    previous_sample_by_itemid = zabbix_service.fetch_previous_status_samples(
                        item_clock_by_itemid=transition_item_clock_map
                    )
                except Exception:
                    logger.exception(
                        "Status polling OLT %s: failed to query previous status samples from Zabbix.",
                        olt.id,
                    )

        open_logs_by_onu: Dict[int, ONULog] = {}
        open_logs_qs = ONULog.objects.filter(
            onu__olt=olt,
            onu__is_active=True,
            offline_until__isnull=True,
        )
        if scoped_refresh:
            open_logs_qs = open_logs_qs.filter(onu_id__in=[onu.id for onu in onus])
        for log in open_logs_qs.order_by("-offline_since"):
            open_logs_by_onu.setdefault(log.onu_id, log)

        updated = online = offline = unknown = missing = missing_preserved = stale_preserved = 0
        onus_to_update: List[ONU] = []
        logs_to_close: List[ONULog] = []
        logs_to_log_update: List[ONULog] = []
        new_logs: List[ONULog] = []
        disconnect_window_margin_seconds = int(
            getattr(settings, "ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS", 90) or 90
        )

        for onu in onus:
            normalized_index = onu_index_map.get(onu.id)
            mapped = status_by_index.get(normalized_index) if normalized_index else None
            if normalized_index and normalized_index in stale_indexes:
                mapped = None
                stale_preserved += 1

            if mapped is None:
                missing += 1
                if dry_run:
                    continue
                current_status = onu.status if onu.status in {
                    ONU.STATUS_ONLINE,
                    ONU.STATUS_OFFLINE,
                    ONU.STATUS_UNKNOWN,
                } else ONU.STATUS_UNKNOWN
                missing_preserved += 1
                continue

            new_status = mapped["status"]
            reason = mapped["reason"] if new_status != ONU.STATUS_ONLINE else ""
            status_clock_epoch = _to_int_or_none(mapped.get("status_clock_epoch"))
            status_clock_at = (
                datetime.fromtimestamp(status_clock_epoch, tz=dt_timezone.utc)
                if status_clock_epoch is not None and status_clock_epoch > 0
                else None
            )

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
                    open_log.offline_until = status_clock_at or now
                    logs_to_close.append(open_log)
                active_log = None
            elif new_status == ONU.STATUS_OFFLINE:
                if onu.status == ONU.STATUS_ONLINE or not open_log:
                    detected_at = status_clock_at or now
                    window_start = detected_at
                    window_end = detected_at
                    if onu.status == ONU.STATUS_ONLINE and status_clock_epoch:
                        itemid = str(mapped.get("status_itemid") or "").strip()
                        previous_sample = previous_sample_by_itemid.get(itemid) or {}
                        previous_status = str(previous_sample.get("status") or "").strip().lower()
                        previous_clock_epoch = _to_int_or_none(previous_sample.get("clock_epoch"))
                        max_gap_seconds = max(
                            int(olt.polling_interval_seconds or 0) * 2 + disconnect_window_margin_seconds,
                            disconnect_window_margin_seconds + 1,
                        )
                        if (
                            previous_status == ONU.STATUS_ONLINE
                            and previous_clock_epoch is not None
                            and 0 < (status_clock_epoch - previous_clock_epoch) <= max_gap_seconds
                        ):
                            window_start = datetime.fromtimestamp(previous_clock_epoch, tz=dt_timezone.utc)
                            window_end = detected_at
                    active_log = ONULog(
                        onu=onu,
                        offline_since=detected_at,
                        disconnect_reason=reason or ONULog.REASON_UNKNOWN,
                        disconnect_window_start=window_start,
                        disconnect_window_end=window_end,
                    )
                    new_logs.append(active_log)
                    open_logs_by_onu[onu.id] = active_log
                else:
                    needs_log_update = False
                    if reason and open_log.disconnect_reason != reason:
                        open_log.disconnect_reason = reason
                        needs_log_update = True
                    if open_log.offline_since and not open_log.disconnect_window_end:
                        open_log.disconnect_window_end = open_log.offline_since
                        needs_log_update = True
                    if open_log.offline_since and not open_log.disconnect_window_start:
                        open_log.disconnect_window_start = open_log.disconnect_window_end or open_log.offline_since
                        needs_log_update = True
                    if needs_log_update:
                        logs_to_log_update.append(open_log)

            if onu.status != new_status:
                onu.status = new_status
                onus_to_update.append(onu)

            updated += 1

        if not dry_run:
            with transaction.atomic():
                if new_logs:
                    ONULog.objects.bulk_create(new_logs)
                if logs_to_close:
                    ONULog.objects.bulk_update(logs_to_close, ["offline_until"])
                if logs_to_log_update:
                    ONULog.objects.bulk_update(
                        logs_to_log_update,
                        ["disconnect_reason", "disconnect_window_start", "disconnect_window_end"],
                    )
                if onus_to_update:
                    ONU.objects.bulk_update(onus_to_update, ["status"])
                if not scoped_refresh:
                    self._mark_poll_result(olt, now)
            try:
                topology_counter_service.refresh_olt(olt.id)
            except Exception:
                logger.exception("OLT %s polling: failed to refresh cached topology counters.", olt.id)

        self.stdout.write(
            f"OLT {olt.id}: polled {updated} ONUs "
            f"(online={online}, offline={offline}, unknown={unknown}, stale_preserved={stale_preserved}, "
            f"missing={missing}, missing_preserved={missing_preserved})."
        )

    def _mark_poll_result(self, olt: OLT, now):
        next_at = now + timedelta(seconds=olt.polling_interval_seconds or 0)
        olt.last_poll_at = now
        olt.next_poll_at = next_at
        olt.save(update_fields=["last_poll_at", "next_poll_at"])
