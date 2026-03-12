from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from topology.models import OLT, ONU, ONULog

try:
    import pymysql
    from pymysql.cursors import DictCursor
except Exception:  # pragma: no cover - exercised in runtime environments
    pymysql = None
    DictCursor = None


logger = logging.getLogger(__name__)

_ALARM_CATALOG_PATH = Path(__file__).with_name("unm_alarm_codes.json")
_UNM_ALARM_CURRENT_TABLE = "t_alarmlogcur"
_UNM_ALARM_HISTORY_MERGE_TABLE = "t_alarmloghist_merge"
_UNM_ALARM_HISTORY_TABLE_PATTERN = re.compile(r"^t_alarmloghist(?:_[A-Za-z0-9]+)*$")
_UNM_ALARM_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
_UNM_TIMEZONE_CACHE_SECONDS = 21600
_UNM_TIMEZONE_CACHE: Dict[Tuple[str, int, str, int], Tuple[float, dt_timezone]] = {}
_UNM_ALARM_WINDOW_FALLBACK_LIMIT = 10000


class UNMServiceError(RuntimeError):
    pass


def _normalize_alarm_label(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return ""
    return normalized.replace("_", " ")


def _normalize_inventory_name(row: Dict) -> str:
    alias = str((row or {}).get("caliasname") or "").strip()
    if alias:
        return alias
    return str((row or {}).get("cobjectname") or "").strip()


def _normalize_inventory_serial(row: Dict) -> str:
    return str((row or {}).get("clogicalsn") or "").strip().upper().replace("-", "")


def _as_aware_datetime(value, source_timezone=None):
    if value in (None, ""):
        return None
    target_timezone = source_timezone or timezone.get_current_timezone()
    if timezone.is_naive(value):
        # UNM alarm columns are UTC-based (`...utctime`). Convert them into the
        # UNM source timezone for operator-facing rendering.
        return timezone.make_aware(value, dt_timezone.utc).astimezone(target_timezone)
    return timezone.localtime(value, target_timezone)


def _as_db_datetime(value):
    if value in (None, ""):
        return None
    if timezone.is_aware(value):
        return timezone.make_naive(value, dt_timezone.utc)
    return value


@lru_cache(maxsize=1)
def _load_alarm_catalog() -> Dict[int, str]:
    if not _ALARM_CATALOG_PATH.exists():
        logger.warning("UNM alarm catalog file not found at %s", _ALARM_CATALOG_PATH)
        return {}
    try:
        payload = json.loads(_ALARM_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load UNM alarm catalog from %s", _ALARM_CATALOG_PATH)
        return {}

    catalog: Dict[int, str] = {}
    for raw_code, raw_label in (payload or {}).items():
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            continue
        label = _normalize_alarm_label(raw_label)
        if label:
            catalog[code] = label
    return catalog


class UNMService:
    def is_enabled_for_olt(self, olt: OLT) -> bool:
        return bool(
            getattr(olt, "unm_enabled", False)
            and str(getattr(olt, "unm_host", "") or "").strip()
            and str(getattr(olt, "unm_username", "") or "").strip()
            and str(getattr(olt, "unm_password", "") or "").strip()
            and getattr(olt, "unm_mneid", None)
        )

    def fetch_onu_inventory_map(self, olt: OLT) -> Dict[Tuple[int, int, int], Dict]:
        self._ensure_olt_configured(olt)
        rows = self._query(
            olt,
            """
            SELECT
                cobjectid,
                cslotno,
                cponno,
                cauthno,
                cobjectname,
                caliasname,
                clogicalsn
            FROM integratecfgdb.t_ontdevice
            WHERE cneid = %s
            ORDER BY cslotno, cponno, cauthno
            """,
            [int(olt.unm_mneid)],
        )

        result: Dict[Tuple[int, int, int], Dict] = {}
        for row in rows:
            try:
                key = (
                    int(row.get("cslotno")),
                    int(row.get("cponno")),
                    int(row.get("cauthno")),
                )
            except (TypeError, ValueError):
                continue
            result[key] = {
                "unm_object_id": int(row.get("cobjectid")),
                "name": _normalize_inventory_name(row),
                "serial": _normalize_inventory_serial(row),
            }
        return result

    def fetch_onu_alarm_history(
        self,
        *,
        olt: OLT,
        onu: ONU,
        alarm_cutoff,
        alarm_end,
        alarm_limit: int,
    ) -> List[Dict]:
        self._ensure_olt_configured(olt)
        source_timezone = self._get_unm_alarm_timezone(olt)
        inventory_row = self._fetch_single_onu_inventory_row(olt=olt, onu=onu)
        if inventory_row is None:
            return []

        cobjectid = int(inventory_row["unm_object_id"])
        cneid = int(olt.unm_mneid)
        db_alarm_cutoff = _as_db_datetime(alarm_cutoff)
        db_alarm_end = _as_db_datetime(alarm_end)

        raw_rows: List[Dict] = []
        query_errors: List[UNMServiceError] = []
        table_names = [_UNM_ALARM_CURRENT_TABLE, *self._discover_alarm_history_tables(olt)]
        for table_name in table_names:
            try:
                raw_rows.extend(
                    self._fetch_alarm_rows_for_object(
                        olt=olt,
                        table_name=table_name,
                        cneid=cneid,
                        cobjectid=cobjectid,
                        db_alarm_cutoff=db_alarm_cutoff,
                        db_alarm_end=db_alarm_end,
                        fallback_limit=self._resolve_alarm_window_fallback_limit(alarm_limit),
                    )
                )
            except UNMServiceError as exc:
                logger.warning(
                    "UNM alarm history table query failed for olt_id=%s onu_id=%s table=%s (%s).",
                    getattr(olt, "id", None),
                    getattr(onu, "id", None),
                    table_name,
                    exc,
                )
                query_errors.append(exc)

        if not raw_rows and query_errors:
            raise query_errors[0]

        rows_by_log_id: Dict[int, Dict] = {}
        for raw_row in raw_rows:
            try:
                log_id = int(raw_row.get("clogid"))
            except (TypeError, ValueError):
                continue
            rows_by_log_id.setdefault(log_id, raw_row)

        result_rows = []
        for row in sorted(
            rows_by_log_id.values(),
            key=lambda item: _as_aware_datetime(item.get("coccurutctime"), source_timezone)
            or timezone.make_aware(datetime(1970, 1, 1)),
            reverse=True,
        ):
            occurred_at = _as_aware_datetime(row.get("coccurutctime"), source_timezone)
            cleared_at = _as_aware_datetime(row.get("cclearutctime"), source_timezone)
            if occurred_at is None or occurred_at > alarm_end:
                continue
            if cleared_at is not None and cleared_at < alarm_cutoff:
                continue

            event_code = self._safe_int(row.get("calarmcode"))
            event_type = self._map_event_type(event_code)
            status = "active" if cleared_at is None else "resolved"
            event_label = self._resolve_alarm_label(event_code, row)
            duration_end = cleared_at or alarm_end
            duration_seconds = None
            if occurred_at and duration_end:
                duration_seconds = max(0, int((duration_end - occurred_at).total_seconds()))

            result_rows.append(
                {
                    "id": f"unm-{row.get('clogid')}",
                    "event_type": event_type,
                    "event_label": event_label,
                    "event_code": event_code,
                    "severity": self._safe_int(row.get("calarmlevel")),
                    "start_at": occurred_at.isoformat() if occurred_at else None,
                    "end_at": cleared_at.isoformat() if cleared_at else None,
                    "status": status,
                    "duration_seconds": duration_seconds,
                    "location": str(row.get("clocationinfo") or "").strip(),
                }
            )

        return result_rows[: int(alarm_limit or 1)]

    def localize_alarm_datetime(self, *, olt: OLT, value):
        if value in (None, ""):
            return None
        self._ensure_olt_configured(olt)
        return _as_aware_datetime(value, self._get_unm_alarm_timezone(olt))

    def fetch_current_alarm_state_map(self, *, olt: OLT, onus: Iterable[ONU]) -> Dict[int, Dict]:
        self._ensure_olt_configured(olt)

        target_onu_ids: Dict[Tuple[int, int, int], int] = {}
        for onu in onus or []:
            try:
                position_key = (
                    int(getattr(onu, "slot_id", 0) or 0),
                    int(getattr(onu, "pon_id", 0) or 0),
                    int(getattr(onu, "onu_id", 0) or 0),
                )
                onu_pk = int(getattr(onu, "id"))
            except (TypeError, ValueError):
                continue
            if min(position_key) <= 0 or onu_pk <= 0:
                continue
            target_onu_ids[position_key] = onu_pk

        if not target_onu_ids:
            return {}

        source_timezone = self._get_unm_alarm_timezone(olt)
        rows = self._query(
            olt,
            """
            SELECT
                inv.cslotno,
                inv.cponno,
                inv.cauthno,
                cur.clogid,
                cur.cobjectid,
                cur.cneid,
                cur.calarmcode,
                cur.calarmlevel,
                cur.coccurutctime,
                cur.cclearutctime,
                cur.clocationinfo,
                cur.clineport,
                cur.calarminfo,
                cur.calarmexinfo
            FROM alarmdb.t_alarmlogcur cur
            INNER JOIN integratecfgdb.t_ontdevice inv
                ON inv.cneid = cur.cneid
               AND inv.cobjectid = cur.cobjectid
            WHERE cur.cneid = %s
              AND cur.cclearutctime IS NULL
            """,
            [int(olt.unm_mneid)],
        )

        result: Dict[int, Dict] = {}
        selected_rank: Dict[int, Tuple[datetime, int]] = {}
        for row in rows:
            try:
                position_key = (
                    int(row.get("cslotno")),
                    int(row.get("cponno")),
                    int(row.get("cauthno")),
                )
            except (TypeError, ValueError):
                continue

            onu_id = target_onu_ids.get(position_key)
            if onu_id is None:
                continue

            occurred_at = _as_aware_datetime(row.get("coccurutctime"), source_timezone)
            if occurred_at is None:
                continue

            log_id = self._safe_int(row.get("clogid")) or 0
            rank = (occurred_at, log_id)
            current_rank = selected_rank.get(onu_id)
            if current_rank and rank <= current_rank:
                continue

            event_code = self._safe_int(row.get("calarmcode"))
            mapped_reason = self._map_event_type(event_code)
            if mapped_reason not in (ONULog.REASON_LINK_LOSS, ONULog.REASON_DYING_GASP):
                mapped_reason = ONULog.REASON_UNKNOWN

            selected_rank[onu_id] = rank
            result[onu_id] = {
                "disconnect_reason": mapped_reason,
                "occurred_at": occurred_at,
                "event_code": event_code,
                "event_label": self._resolve_alarm_label(event_code, row),
                "severity": self._safe_int(row.get("calarmlevel")),
                "location": str(row.get("clocationinfo") or "").strip(),
            }

        return result

    def _fetch_single_onu_inventory_row(self, *, olt: OLT, onu: ONU) -> Optional[Dict]:
        rows = self._query(
            olt,
            """
            SELECT
                cobjectid,
                cslotno,
                cponno,
                cauthno,
                cobjectname,
                caliasname,
                clogicalsn
            FROM integratecfgdb.t_ontdevice
            WHERE cneid = %s
              AND cslotno = %s
              AND cponno = %s
              AND cauthno = %s
            LIMIT 1
            """,
            [int(olt.unm_mneid), int(onu.slot_id), int(onu.pon_id), int(onu.onu_id)],
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["unm_object_id"] = int(row.get("cobjectid"))
        row["name"] = _normalize_inventory_name(row)
        row["serial"] = _normalize_inventory_serial(row)
        return row

    def _fetch_alarm_rows_for_object(
        self,
        *,
        olt: OLT,
        table_name: str,
        cneid: int,
        cobjectid: int,
        db_alarm_cutoff,
        db_alarm_end,
        fallback_limit: int,
    ) -> List[Dict]:
        resolved_table_name = self._normalize_alarm_table_name(table_name)
        try:
            return self._query(
                olt,
                f"""
                SELECT
                    clogid,
                    cobjectid,
                    cneid,
                    calarmcode,
                    calarmlevel,
                    coccurutctime,
                    cclearutctime,
                    clocationinfo,
                    clineport,
                    calarminfo,
                    calarmexinfo
                FROM alarmdb.{resolved_table_name}
                WHERE cneid = %s
                  AND cobjectid = %s
                  AND coccurutctime <= %s
                  AND (cclearutctime IS NULL OR cclearutctime >= %s)
                """,
                [cneid, cobjectid, db_alarm_end, db_alarm_cutoff],
            )
        except UNMServiceError as exc:
            fallback_rows, fallback_succeeded = self._fetch_alarm_rows_for_object_via_window(
                olt=olt,
                resolved_table_name=resolved_table_name,
                cneid=cneid,
                cobjectid=cobjectid,
                db_alarm_cutoff=db_alarm_cutoff,
                db_alarm_end=db_alarm_end,
                fallback_limit=fallback_limit,
            )
            if fallback_succeeded:
                logger.info(
                    "UNM alarm history fallback used for olt_id=%s table=%s cobjectid=%s rows=%s.",
                    getattr(olt, "id", None),
                    resolved_table_name,
                    cobjectid,
                    len(fallback_rows),
                )
                return fallback_rows
            raise exc

    def _fetch_alarm_rows_for_object_via_window(
        self,
        *,
        olt: OLT,
        resolved_table_name: str,
        cneid: int,
        cobjectid: int,
        db_alarm_cutoff,
        db_alarm_end,
        fallback_limit: int,
    ) -> Tuple[List[Dict], bool]:
        fallback_succeeded = False
        try:
            rows = self._fetch_alarm_rows_by_window(
                olt=olt,
                resolved_table_name=resolved_table_name,
                db_alarm_cutoff=db_alarm_cutoff,
                db_alarm_end=db_alarm_end,
                limit=fallback_limit,
            )
            fallback_succeeded = True
        except UNMServiceError:
            rows = []

        if resolved_table_name == _UNM_ALARM_CURRENT_TABLE:
            try:
                rows.extend(
                    self._fetch_active_alarm_rows(
                        olt=olt,
                        resolved_table_name=resolved_table_name,
                        limit=fallback_limit,
                    )
                )
                fallback_succeeded = True
            except UNMServiceError:
                pass

        filtered_rows: List[Dict] = []
        for row in rows:
            if self._safe_int((row or {}).get("cneid")) != cneid:
                continue
            if self._safe_int((row or {}).get("cobjectid")) != cobjectid:
                continue
            filtered_rows.append(dict(row))
        return filtered_rows, fallback_succeeded

    def _fetch_alarm_rows_by_window(
        self,
        *,
        olt: OLT,
        resolved_table_name: str,
        db_alarm_cutoff,
        db_alarm_end,
        limit: int,
    ) -> List[Dict]:
        bounded_limit = max(int(limit or 1), 1)
        return self._query(
            olt,
            f"""
            SELECT
                clogid,
                cobjectid,
                cneid,
                calarmcode,
                calarmlevel,
                coccurutctime,
                cclearutctime,
                clocationinfo,
                clineport,
                calarminfo,
                calarmexinfo
            FROM alarmdb.{resolved_table_name}
            WHERE coccurutctime >= %s
              AND coccurutctime <= %s
            LIMIT {bounded_limit}
            """,
            [db_alarm_cutoff, db_alarm_end],
        )

    def _fetch_active_alarm_rows(
        self,
        *,
        olt: OLT,
        resolved_table_name: str,
        limit: int,
    ) -> List[Dict]:
        bounded_limit = max(int(limit or 1), 1)
        return self._query(
            olt,
            f"""
            SELECT
                clogid,
                cobjectid,
                cneid,
                calarmcode,
                calarmlevel,
                coccurutctime,
                cclearutctime,
                clocationinfo,
                clineport,
                calarminfo,
                calarmexinfo
            FROM alarmdb.{resolved_table_name}
            WHERE cclearutctime IS NULL
            LIMIT {bounded_limit}
            """,
            [],
        )

    def _discover_alarm_history_tables(self, olt: OLT) -> List[str]:
        try:
            rows = self._query(olt, "SHOW TABLES FROM alarmdb", [])
        except UNMServiceError as exc:
            logger.warning(
                "UNM alarm history table discovery failed for olt_id=%s (%s); using current alarm table only.",
                getattr(olt, "id", None),
                exc,
            )
            return []

        available_tables = []
        for row in rows:
            values = list((row or {}).values())
            if not values:
                continue
            table_name = str(values[0] or "").strip()
            if not table_name:
                continue
            if table_name == _UNM_ALARM_HISTORY_MERGE_TABLE:
                available_tables.append(table_name)
                continue
            if _UNM_ALARM_HISTORY_TABLE_PATTERN.match(table_name):
                available_tables.append(table_name)

        available_tables = sorted(set(available_tables))
        if _UNM_ALARM_HISTORY_MERGE_TABLE in available_tables:
            return [_UNM_ALARM_HISTORY_MERGE_TABLE]
        return available_tables

    @staticmethod
    def _normalize_alarm_table_name(table_name: str) -> str:
        candidate = str(table_name or "").strip()
        if not _UNM_ALARM_TABLE_NAME_PATTERN.fullmatch(candidate):
            raise UNMServiceError("UNM query failed.")
        if candidate != _UNM_ALARM_CURRENT_TABLE and candidate != _UNM_ALARM_HISTORY_MERGE_TABLE:
            if not _UNM_ALARM_HISTORY_TABLE_PATTERN.match(candidate):
                raise UNMServiceError("UNM query failed.")
        return candidate

    def _get_unm_alarm_timezone(self, olt: OLT):
        cache_key = (
            str(getattr(olt, "unm_host", "") or "").strip(),
            int(getattr(olt, "unm_port", 3306) or 3306),
            str(getattr(olt, "unm_username", "") or "").strip(),
            int(getattr(olt, "unm_mneid", 0) or 0),
        )
        now_monotonic = time.monotonic()
        cached = _UNM_TIMEZONE_CACHE.get(cache_key)
        if cached and cached[0] > now_monotonic:
            return cached[1]

        try:
            rows = self._query(
                olt,
                "SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS utc_offset_seconds",
                [],
            )
            offset_seconds = int((rows[0] or {}).get("utc_offset_seconds") or 0) if rows else 0
        except Exception as exc:
            logger.warning(
                "Failed to resolve UNM timezone offset for olt_id=%s; falling back to Varuna timezone (%s).",
                getattr(olt, "id", None),
                exc,
            )
            return timezone.get_current_timezone()

        source_timezone = dt_timezone(timedelta(seconds=offset_seconds))
        _UNM_TIMEZONE_CACHE[cache_key] = (now_monotonic + _UNM_TIMEZONE_CACHE_SECONDS, source_timezone)
        return source_timezone

    @staticmethod
    def _resolve_alarm_window_fallback_limit(alarm_limit: int) -> int:
        requested_limit = max(int(alarm_limit or 1), 1)
        return max(_UNM_ALARM_WINDOW_FALLBACK_LIMIT, requested_limit * 10)

    def _ensure_olt_configured(self, olt: OLT) -> None:
        if not getattr(olt, "unm_enabled", False):
            raise UNMServiceError("UNM integration is disabled for this OLT.")
        if not str(getattr(olt, "unm_host", "") or "").strip():
            raise UNMServiceError("UNM host is not configured for this OLT.")
        if not str(getattr(olt, "unm_username", "") or "").strip():
            raise UNMServiceError("UNM username is not configured for this OLT.")
        if not str(getattr(olt, "unm_password", "") or "").strip():
            raise UNMServiceError("UNM password is not configured for this OLT.")
        if getattr(olt, "unm_mneid", None) in (None, ""):
            raise UNMServiceError("UNM MNEID is not configured for this OLT.")
        if pymysql is None or DictCursor is None:
            raise UNMServiceError("PyMySQL is not installed in the backend runtime.")

    def _query(self, olt: OLT, query: str, params: Iterable) -> List[Dict]:
        self._ensure_olt_configured(olt)
        timeout_seconds = max(int(getattr(settings, "UNM_DB_TIMEOUT_SECONDS", 10) or 10), 1)
        try:
            connection = pymysql.connect(
                host=str(olt.unm_host),
                port=int(olt.unm_port or 3306),
                user=str(olt.unm_username),
                password=str(olt.unm_password),
                charset="latin1",
                autocommit=True,
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                write_timeout=timeout_seconds,
                cursorclass=DictCursor,
            )
        except Exception as exc:
            raise UNMServiceError(f"Failed to connect to UNM at {olt.unm_host}:{olt.unm_port}.") from exc

        try:
            with connection.cursor() as cursor:
                cursor.execute(query, list(params))
                rows = cursor.fetchall() or []
                return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning(
                "UNM query failed for olt_id=%s at %s:%s (%s).",
                getattr(olt, "id", None),
                getattr(olt, "unm_host", None),
                getattr(olt, "unm_port", None),
                exc,
            )
            raise UNMServiceError("UNM query failed.") from exc
        finally:
            try:
                connection.close()
            except Exception:
                logger.debug("Failed to close UNM connection cleanly.", exc_info=True)

    @staticmethod
    def _safe_int(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_alarm_label(self, event_code: Optional[int], row: Dict) -> str:
        if event_code is not None:
            label = _load_alarm_catalog().get(int(event_code))
            if label:
                return label
        raw_alarm_info = _normalize_alarm_label(row.get("calarminfo"))
        if raw_alarm_info:
            return raw_alarm_info
        if event_code is None:
            return "UNM Alarm"
        return f"Alarm {event_code}"

    @staticmethod
    def _map_event_type(event_code: Optional[int]) -> str:
        if event_code == 2400:
            return "link_loss"
        if event_code == 2340:
            return "dying_gasp"
        return "unm"


unm_service = UNMService()
