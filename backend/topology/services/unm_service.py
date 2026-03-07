from __future__ import annotations

from datetime import datetime
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from topology.models import OLT, ONU

try:
    import pymysql
    from pymysql.cursors import DictCursor
except Exception:  # pragma: no cover - exercised in runtime environments
    pymysql = None
    DictCursor = None


logger = logging.getLogger(__name__)

_ALARM_CATALOG_PATH = Path(__file__).with_name("unm_alarm_codes.json")


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


def _as_aware_datetime(value):
    if value in (None, ""):
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _as_db_datetime(value):
    if value in (None, ""):
        return None
    if timezone.is_aware(value):
        return timezone.make_naive(value, timezone.get_current_timezone())
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
        inventory_row = self._fetch_single_onu_inventory_row(olt=olt, onu=onu)
        if inventory_row is None:
            return []

        cobjectid = int(inventory_row["unm_object_id"])
        cneid = int(olt.unm_mneid)
        query_limit = max(int(alarm_limit or 1) * 20, 500)
        db_alarm_cutoff = _as_db_datetime(alarm_cutoff)
        db_alarm_end = _as_db_datetime(alarm_end)

        current_rows = self._query(
            olt,
            """
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
            FROM alarmdb.t_alarmlogcur
            WHERE cneid = %s
              AND cobjectid = %s
              AND coccurutctime <= %s
              AND (cclearutctime IS NULL OR cclearutctime >= %s)
            ORDER BY coccurutctime DESC
            LIMIT %s
            """,
            [cneid, cobjectid, db_alarm_end, db_alarm_cutoff, query_limit],
        )
        history_rows = self._query(
            olt,
            """
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
            FROM alarmdb.t_alarmloghist_merge
            WHERE cneid = %s
              AND cobjectid = %s
              AND coccurutctime <= %s
              AND (cclearutctime IS NULL OR cclearutctime >= %s)
            ORDER BY coccurutctime DESC
            LIMIT %s
            """,
            [cneid, cobjectid, db_alarm_end, db_alarm_cutoff, query_limit],
        )

        rows_by_log_id: Dict[int, Dict] = {}
        for raw_row in list(current_rows) + list(history_rows):
            try:
                log_id = int(raw_row.get("clogid"))
            except (TypeError, ValueError):
                continue
            rows_by_log_id.setdefault(log_id, raw_row)

        result_rows = []
        for row in sorted(
            rows_by_log_id.values(),
            key=lambda item: _as_aware_datetime(item.get("coccurutctime")) or timezone.make_aware(datetime(1970, 1, 1)),
            reverse=True,
        ):
            occurred_at = _as_aware_datetime(row.get("coccurutctime"))
            cleared_at = _as_aware_datetime(row.get("cclearutctime"))
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
