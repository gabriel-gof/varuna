import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import connections

from topology.services.power_values import (
    SENTINEL_NEG40_EPSILON,
    SENTINEL_ZERO_EPSILON,
    VALID_POWER_MAX_DBM,
    VALID_POWER_MIN_DBM,
    normalize_power_value,
)
from topology.services.vendor_profile import parse_onu_index


logger = logging.getLogger(__name__)

HUAWEI_STATUS_ITEM_RE = re.compile(
    r"^ONU\s+(?P<chassi>\d+)\/(?P<slot>\d+)\/(?P<pon>\d+)\/(?P<onu>\d+)\s+(?P<name>.+):\s*Status$",
    re.IGNORECASE,
)
HUAWEI_STATUS_ITEM_PON_RE = re.compile(
    r"^ONU\s+(?:PON\s+)?(?P<slot>\d+)\/(?P<pon>\d+)\/(?P<onu>\d+)\s+(?P<name>.+):\s*Status$",
    re.IGNORECASE,
)
HUAWEI_STATUS_NAME_WITH_SERIAL_RE = re.compile(
    r"^(?P<name>.+?)\s+\[(?P<serial>[^\]]+)\]$"
)
HUAWEI_STATUS_NAME_WITH_TRAILING_SERIAL_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<serial>[A-Z]{4}[A-Z0-9]{4,20})$",
    re.IGNORECASE,
)
FIBERHOME_STATUS_ITEM_RE = re.compile(
    r"^ONU\s+(?:PON\s+)?(?P<slot>\d+)\/(?P<pon>\d+)\/(?P<onu>\d+)\s+(?P<serial>.+):\s*Status$",
    re.IGNORECASE,
)
FIBERHOME_STATUS_ITEM_SERIAL_ONLY_RE = re.compile(
    r"^ONU\s+\{#PON\}\s+(?P<serial>.+):\s*Status$",
    re.IGNORECASE,
)
GENERIC_ONU_STATUS_ITEM_RE = re.compile(
    r"^ONU\s+(?P<slot>\d+)\/(?P<pon>\d+)\/(?P<onu>\d+)\s+(?P<body>.+):\s*Status$",
    re.IGNORECASE,
)
GENERIC_SERIAL_TOKEN_RE = re.compile(r"^[A-Z]{4}[A-Z0-9-]{4,28}$")
GENERIC_DIGIT_SERIAL_TOKEN_RE = re.compile(r"^(?=(?:.*\d){4,})[A-Z0-9-]{8,32}$")
GENERIC_DISCOVERY_NAME_WITH_NUMERIC_SUFFIX_RE = re.compile(
    r"^(?P<base>[A-Z0-9._:-]+)\s+(?P<suffix>\d{1,3})$",
    re.IGNORECASE,
)
GENERIC_DISCOVERY_NAME_SENTINELS = frozenset({"N/A", "NA", "NONE", "NULL", "--", "-"})

VARUNA_DISCOVERY_INTERVAL_MACRO = "{$VARUNA.DISCOVERY_INTERVAL}"
VARUNA_STATUS_INTERVAL_MACRO = "{$VARUNA.STATUS_INTERVAL}"
VARUNA_POWER_INTERVAL_MACRO = "{$VARUNA.POWER_INTERVAL}"
VARUNA_AVAILABILITY_INTERVAL_MACRO = "{$VARUNA.AVAILABILITY_INTERVAL}"
VARUNA_HISTORY_DAYS_MACRO = "{$VARUNA.HISTORY_DAYS}"
DEFAULT_VARUNA_HOST_GROUP_NAME = "OLT"
DEFAULT_VARUNA_LEGACY_HOST_GROUP_NAMES = ("OLT", "OLTs")
DEFAULT_VARUNA_HOST_NAME_PREFIX = ""
VARUNA_HOST_TAG_SOURCE = "source"
VARUNA_HOST_TAG_VENDOR = "vendor"
VARUNA_HOST_TAG_MODEL = "model"
VARUNA_HOST_TAG_SOURCE_VALUE = "varuna"
VARUNA_SNMP_IP_MACRO = "{$VARUNA.SNMP_IP}"
VARUNA_SNMP_PORT_MACRO = "{$VARUNA.SNMP_PORT}"
VARUNA_SNMP_COMMUNITY_MACRO = "{$VARUNA.SNMP_COMMUNITY}"
DEFAULT_AVAILABILITY_ITEM_KEY = "varunaSnmpAvailability"
SHARED_TEMPLATE_NAME_CANDIDATES = (
    "Varuna SNMP Availability",
    "SNMP Avail",
    "Template Varuna SNMP Availability",
)
MODEL_TAG_ALIASES = {
    "unificado": "unified",
}


class ZabbixAPIError(RuntimeError):
    pass


def _is_generic_serial_token(value: str) -> bool:
    return bool(
        GENERIC_SERIAL_TOKEN_RE.fullmatch(value)
        or GENERIC_DIGIT_SERIAL_TOKEN_RE.fullmatch(value)
    )


def _looks_like_hex_serial_token(value: str) -> bool:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return False
    compact = normalized.replace(" ", "")
    if normalized.startswith("0X"):
        compact = normalized[2:].replace(" ", "")
    return bool(re.fullmatch(r"[0-9A-F]{10,64}", compact) and len(compact) % 2 == 0)


def _normalize_status_serial_token(raw: str) -> str:
    token = str(raw or "").strip().upper().strip("[](){}").strip(",;:")
    if not token:
        return ""
    if "," in token:
        parts = [part.strip().strip(",;:") for part in token.split(",") if part.strip()]
        for part in parts:
            if _is_generic_serial_token(part):
                return part.replace("-", "")
        token = parts[0] if parts else token
    if _is_generic_serial_token(token):
        return token.replace("-", "")
    if _looks_like_hex_serial_token(token):
        return token.replace(" ", "")
    return ""


def normalize_discovery_onu_name(raw: str, *, serial: str = "") -> str:
    name = str(raw or "").strip().strip("[](){}").strip(",;:")
    if not name:
        return ""
    if name.upper() in GENERIC_DISCOVERY_NAME_SENTINELS:
        return ""

    normalized_serial = _normalize_status_serial_token(serial)
    serial_like_name = _normalize_status_serial_token(name)
    if serial_like_name:
        if not normalized_serial or serial_like_name == normalized_serial:
            return ""

    if not normalized_serial:
        match = GENERIC_DISCOVERY_NAME_WITH_NUMERIC_SUFFIX_RE.match(name)
        if match:
            return str(match.group("base") or "").strip()

    return name


def _discovery_row_identity_key(row: Dict[str, Any]) -> str:
    slot = str(row.get("{#SLOT}") or "").strip()
    pon = str(row.get("{#PON}") or "").strip()
    onu = str(row.get("{#ONU_ID}") or "").strip()
    if slot and pon and onu:
        return f"{slot}/{pon}/{onu}"
    return str(row.get("{#SNMPINDEX}") or "").strip()


def _repair_discovery_identity_rows(
    parsed_rows: List[Dict[str, Any]],
    fallback_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    fallback_by_key = {
        key: row for row in fallback_rows
        if (key := _discovery_row_identity_key(row))
    }
    repaired_rows: List[Dict[str, Any]] = []

    for row in parsed_rows:
        repaired = dict(row)
        raw_name = str(repaired.get("{#ONU_NAME}") or "").strip()
        normalized_serial = _normalize_status_serial_token(
            repaired.get("{#SERIAL}") or repaired.get("{#ONU_SERIAL}") or ""
        )
        normalized_name = normalize_discovery_onu_name(raw_name, serial=normalized_serial)
        name_needs_repair = raw_name != normalized_name

        if normalized_serial:
            repaired["{#SERIAL}"] = normalized_serial
        if normalized_name or "{#ONU_NAME}" in repaired:
            repaired["{#ONU_NAME}"] = normalized_name

        fallback = fallback_by_key.get(_discovery_row_identity_key(repaired))
        if fallback:
            fallback_serial = _normalize_status_serial_token(
                fallback.get("{#SERIAL}") or fallback.get("{#ONU_SERIAL}") or ""
            )
            fallback_name = normalize_discovery_onu_name(
                fallback.get("{#ONU_NAME}") or "",
                serial=fallback_serial,
            )
            if fallback_serial and not normalized_serial:
                repaired["{#SERIAL}"] = fallback_serial
                normalized_serial = fallback_serial
            if fallback_name and (not normalized_name or name_needs_repair):
                repaired["{#ONU_NAME}"] = fallback_name
                normalized_name = fallback_name

        repaired_rows.append(repaired)

    return repaired_rows


def _to_float_or_none(value) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _from_epoch_to_iso(epoch: Optional[int]) -> Optional[str]:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=dt_timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_host_alias_map(raw_value) -> Dict[str, str]:
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return {str(k): str(v) for k, v in raw_value.items() if str(v).strip()}
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(v).strip()}


def _canonical_model_tag_value(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return ""
    return MODEL_TAG_ALIASES.get(normalized, normalized)


class ZabbixService:
    def __init__(self):
        self.api_url = str(getattr(settings, "ZABBIX_API_URL", "") or "").strip()
        self.api_timeout_seconds = float(getattr(settings, "ZABBIX_API_TIMEOUT_SECONDS", 10) or 10)
        self.api_token = str(getattr(settings, "ZABBIX_API_TOKEN", "") or "").strip()
        self.username = str(getattr(settings, "ZABBIX_USERNAME", "") or "").strip()
        self.password = str(getattr(settings, "ZABBIX_PASSWORD", "") or "").strip()
        self.host_alias_map = _parse_host_alias_map(getattr(settings, "ZABBIX_HOST_NAME_BY_OLT_JSON", "{}"))
        self._auth_token: Optional[str] = None
        self._request_id = 1
        self._host_cache: Dict[str, Dict] = {}
        self.enabled = bool(self.api_url)

    def _next_id(self) -> int:
        current = self._request_id
        self._request_id += 1
        return current

    def _post_json(self, payload: Dict, *, include_bearer: bool = True) -> Dict:
        if not self.enabled:
            raise ZabbixAPIError("Zabbix API URL is not configured.")

        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json-rpc"}
        if include_bearer and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        request = Request(self.api_url, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.api_timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except URLError as exc:
            raise ZabbixAPIError(f"Zabbix API request failed: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ZabbixAPIError("Invalid JSON response from Zabbix API.") from exc

        if "error" in parsed:
            err = parsed["error"] or {}
            detail = err.get("data") or err.get("message") or str(err)
            raise ZabbixAPIError(f"Zabbix API error: {detail}")
        if "result" not in parsed:
            raise ZabbixAPIError("Zabbix API response did not contain result.")
        return parsed["result"]

    def _get_auth_token(self) -> Optional[str]:
        if self.api_token:
            return None
        if self._auth_token:
            return self._auth_token
        if not self.username or not self.password:
            raise ZabbixAPIError("Zabbix username/password are not configured.")

        for params in (
            {"username": self.username, "password": self.password},
            {"user": self.username, "password": self.password},
        ):
            payload = {"jsonrpc": "2.0", "method": "user.login", "params": params, "id": self._next_id()}
            try:
                token = self._post_json(payload, include_bearer=False)
            except ZabbixAPIError:
                continue
            if token:
                self._auth_token = str(token)
                return self._auth_token

        raise ZabbixAPIError("Unable to authenticate against Zabbix API.")

    def _call(self, method: str, params: Any) -> Dict:
        payload: Dict = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }
        if not self.api_token:
            auth = self._get_auth_token()
            if auth:
                payload["auth"] = auth
        try:
            return self._post_json(payload, include_bearer=True)
        except ZabbixAPIError as exc:
            msg = str(exc).lower()
            if "re-login" not in msg and "session terminated" not in msg:
                raise
            self._auth_token = None
            auth = self._get_auth_token()
            if auth:
                payload["auth"] = auth
                payload["id"] = self._next_id()
            return self._post_json(payload, include_bearer=True)

    @staticmethod
    def _db_latest_items_enabled() -> bool:
        return bool(getattr(settings, "ZABBIX_DB_ENABLED", False))

    @staticmethod
    def _db_latest_items_chunk_size() -> int:
        return max(int(getattr(settings, "ZABBIX_DB_LATEST_ITEMS_CHUNK_SIZE", 1000) or 1000), 100)

    @staticmethod
    def _history_table_name_for_value_type(value_type: Optional[str]) -> Optional[str]:
        mapping = {
            0: "history",
            1: "history_str",
            2: "history_log",
            3: "history_uint",
            4: "history_text",
        }
        return mapping.get(_to_int_or_none(value_type))

    def _get_latest_history_rows_from_db(
        self,
        cursor,
        *,
        itemids_by_value_type: Dict[int, List[int]],
    ) -> Optional[Dict[int, Dict[str, Any]]]:
        history_by_itemid: Dict[int, Dict[str, Any]] = {}

        for value_type, raw_itemids in (itemids_by_value_type or {}).items():
            itemids = [int(itemid) for itemid in raw_itemids if _to_int_or_none(itemid) is not None]
            if not itemids:
                continue

            table_name = self._history_table_name_for_value_type(str(value_type))
            if not table_name:
                return None

            cursor.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        itemid,
                        clock,
                        value::text AS value,
                        row_number() OVER (
                            PARTITION BY itemid
                            ORDER BY clock DESC, ns DESC
                        ) AS rn
                    FROM {table_name}
                    WHERE itemid = ANY(%s)
                )
                SELECT
                    itemid,
                    MAX(clock) FILTER (WHERE rn = 1) AS lastclock,
                    MAX(value) FILTER (WHERE rn = 1) AS lastvalue,
                    MAX(value) FILTER (WHERE rn = 2) AS prevvalue
                FROM ranked
                WHERE rn <= 2
                GROUP BY itemid
                """,
                [itemids],
            )

            for itemid, lastclock, lastvalue, prevvalue in cursor.fetchall():
                normalized_itemid = int(itemid)
                history_by_itemid[normalized_itemid] = {
                    "lastclock": lastclock,
                    "lastvalue": None if lastvalue is None else str(lastvalue),
                    "prevvalue": None if prevvalue is None else str(prevvalue),
                }

        return history_by_itemid

    def _get_latest_valid_power_history_samples_from_db(
        self,
        *,
        item_specs: Dict[str, Optional[str]],
        time_from: Optional[int] = None,
        limit_per_item: int = 10,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self._db_latest_items_enabled():
            return None

        try:
            zabbix_conn = connections["zabbix"]
        except Exception:
            return None

        normalized_specs: Dict[str, Optional[str]] = {}
        for raw_itemid, raw_value_type in (item_specs or {}).items():
            itemid = str(raw_itemid or "").strip()
            if not itemid:
                continue
            normalized_specs[itemid] = raw_value_type
        if not normalized_specs:
            return {}

        normalized_time_from = _to_int_or_none(time_from)
        candidate_tables_by_itemid: Dict[str, List[str]] = {}
        max_candidate_count = 0
        for itemid, value_type in normalized_specs.items():
            table_candidates: List[str] = []
            parsed_value_type = _to_int_or_none(value_type)
            history_types: List[int] = []
            if parsed_value_type in {0, 3}:
                history_types.append(parsed_value_type)
            for history_type in (0, 3):
                if history_type not in history_types:
                    history_types.append(history_type)
            for history_type in history_types:
                table_name = self._history_table_name_for_value_type(str(history_type))
                if not table_name or table_name in table_candidates:
                    continue
                table_candidates.append(table_name)
            if not table_candidates:
                continue
            candidate_tables_by_itemid[itemid] = table_candidates
            max_candidate_count = max(max_candidate_count, len(table_candidates))

        if not candidate_tables_by_itemid:
            return {}

        results: Dict[str, Dict[str, Any]] = {}
        unresolved = set(candidate_tables_by_itemid.keys())

        try:
            with zabbix_conn.cursor() as cursor:
                for candidate_index in range(max_candidate_count):
                    if not unresolved:
                        break

                    itemids_by_table: Dict[str, List[int]] = defaultdict(list)
                    for itemid in unresolved:
                        table_candidates = candidate_tables_by_itemid.get(itemid) or []
                        if candidate_index >= len(table_candidates):
                            continue
                        parsed_itemid = _to_int_or_none(itemid)
                        if parsed_itemid is None:
                            continue
                        itemids_by_table[table_candidates[candidate_index]].append(parsed_itemid)

                    for table_name, itemids in itemids_by_table.items():
                        if not itemids:
                            continue

                        sql = f"""
                            SELECT
                                requested.itemid,
                                history_rows.clock,
                                history_rows.value
                            FROM unnest(%s::bigint[]) AS requested(itemid)
                            JOIN LATERAL (
                                SELECT
                                    h.clock,
                                    h.value::text AS value
                                FROM {table_name} h
                                WHERE h.itemid = requested.itemid
                                  AND h.value > %s
                                  AND h.value < %s
                        """
                        params: List[Any] = [itemids]
                        params.extend(
                            [
                                VALID_POWER_MIN_DBM + SENTINEL_NEG40_EPSILON,
                                VALID_POWER_MAX_DBM - SENTINEL_ZERO_EPSILON,
                            ]
                        )
                        if normalized_time_from is not None and normalized_time_from >= 0:
                            sql += " AND clock >= %s"
                            params.append(normalized_time_from)
                        sql += """
                                ORDER BY h.clock DESC, h.ns DESC
                                LIMIT 1
                            ) AS history_rows ON TRUE
                            ORDER BY requested.itemid ASC
                        """

                        cursor.execute(sql, params)
                        rows = cursor.fetchall()
                        if not rows:
                            continue

                        seen_itemids = set()
                        for raw_itemid, raw_clock, raw_value in rows:
                            itemid = str(raw_itemid or "").strip()
                            if not itemid or itemid in seen_itemids or itemid not in unresolved:
                                continue
                            value = normalize_power_value(_to_float_or_none(raw_value))
                            clock_epoch = _to_int_or_none(raw_clock)
                            if value is None or clock_epoch is None or clock_epoch <= 0:
                                continue
                            seen_itemids.add(itemid)
                            results[itemid] = {
                                "value": value,
                                "clock": _from_epoch_to_iso(clock_epoch),
                                "clock_epoch": clock_epoch,
                            }
                        unresolved.difference_update(seen_itemids)
        except Exception:
            logger.exception("Zabbix DB power-history fallback read failed; falling back to API.")
            try:
                zabbix_conn.close_if_unusable_or_obsolete()
            except Exception:
                pass
            return None

        return results

    def _get_items_by_keys_from_db(self, hostid: str, keys: Iterable[str]) -> Optional[Dict[str, Dict]]:
        if not self._db_latest_items_enabled():
            return None

        normalized_hostid = _to_int_or_none(hostid)
        if normalized_hostid is None:
            return None

        deduped_keys: List[str] = []
        seen = set()
        for raw_key in keys:
            key = str(raw_key or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_keys.append(key)
        if not deduped_keys:
            return {}

        try:
            zabbix_conn = connections["zabbix"]
        except Exception:
            return None

        item_map: Dict[str, Dict] = {}
        chunk_size = self._db_latest_items_chunk_size()
        metadata_sql = """
            SELECT
                i.itemid,
                i.key_,
                i.status,
                i.value_type,
                COALESCE(r.state, 0) AS rt_state,
                COALESCE(r.error, '') AS rt_error
            FROM items
            i
            LEFT JOIN item_rtdata r ON r.itemid = i.itemid
            WHERE i.hostid = %s
              AND i.key_ = ANY(%s)
            ORDER BY i.itemid
        """

        try:
            with zabbix_conn.cursor() as cursor:
                for start in range(0, len(deduped_keys), chunk_size):
                    chunk = deduped_keys[start:start + chunk_size]
                    cursor.execute(metadata_sql, [normalized_hostid, chunk])
                    metadata_rows = cursor.fetchall()
                    itemids_by_value_type: Dict[int, List[int]] = defaultdict(list)
                    for row in metadata_rows:
                        value_type = _to_int_or_none(row[3])
                        itemid = _to_int_or_none(row[0])
                        if value_type is None or itemid is None:
                            continue
                        itemids_by_value_type[value_type].append(itemid)

                    history_by_itemid = self._get_latest_history_rows_from_db(
                        cursor,
                        itemids_by_value_type=itemids_by_value_type,
                    )
                    if history_by_itemid is None:
                        return None

                    for row in metadata_rows:
                        itemid = _to_int_or_none(row[0])
                        if itemid is None:
                            continue
                        history_row = history_by_itemid.get(itemid, {})
                        payload = {
                            "itemid": str(itemid),
                            "key_": str(row[1] or "").strip(),
                            "lastvalue": history_row.get("lastvalue"),
                            "prevvalue": history_row.get("prevvalue"),
                            "lastclock": history_row.get("lastclock"),
                            "state": str(row[4]) if row[4] is not None else "0",
                            "status": str(row[2]) if row[2] is not None else "0",
                            "error": row[5] or "",
                            "value_type": str(row[3]) if row[3] is not None else "",
                        }
                        key = payload["key_"]
                        if not key:
                            continue
                        current = item_map.get(key)
                        current_clock = _to_int_or_none((current or {}).get("lastclock"))
                        next_clock = _to_int_or_none(payload.get("lastclock"))
                        if current is None or (next_clock or 0) >= (current_clock or 0):
                            item_map[key] = payload
        except Exception:
            logger.exception(
                "Zabbix DB latest-item read failed for hostid=%s; falling back to API.",
                normalized_hostid,
            )
            try:
                zabbix_conn.close_if_unusable_or_obsolete()
            except Exception:
                pass
            return None

        return item_map

    def _resolve_host_candidate_names(self, olt) -> List[str]:
        candidates: List[str] = []
        for key in (str(getattr(olt, "id", "")), str(getattr(olt, "name", ""))):
            value = self.host_alias_map.get(key)
            if value:
                candidates.append(value)
        name = str(getattr(olt, "name", "") or "").strip()
        if name:
            candidates.append(name)
        return self._expand_host_name_candidates(candidates)

    @staticmethod
    def _host_name_prefix() -> str:
        return str(getattr(settings, "ZABBIX_HOST_NAME_PREFIX", DEFAULT_VARUNA_HOST_NAME_PREFIX) or "").strip()

    def _desired_host_name_for_olt(self, olt) -> str:
        base_name = str(getattr(olt, "name", "") or "").strip()
        if not base_name:
            return ""
        prefix = self._host_name_prefix()
        if not prefix:
            return base_name
        if base_name.startswith(prefix):
            return base_name
        return f"{prefix}{base_name}"

    def _expand_host_name_candidates(self, names: Iterable[str]) -> List[str]:
        prefix = self._host_name_prefix()
        expanded: List[str] = []
        for raw_name in self._dedupe_values(names):
            expanded.append(raw_name)
            if not prefix:
                continue
            if raw_name.startswith(prefix):
                unprefixed = raw_name[len(prefix):].strip()
                if unprefixed:
                    expanded.append(unprefixed)
            else:
                expanded.append(f"{prefix}{raw_name}")
        return self._dedupe_values(expanded)

    @staticmethod
    def _dedupe_values(values: Iterable[str]) -> List[str]:
        unique: List[str] = []
        for raw in values:
            value = str(raw or "").strip()
            if not value or value in unique:
                continue
            unique.append(value)
        return unique

    def _cache_key_for_olt(self, olt) -> str:
        return f"olt:{getattr(olt, 'id', '')}:{getattr(olt, 'name', '')}"

    def _resolve_host_by_id(self, hostid: str) -> Optional[Dict]:
        normalized_hostid = str(hostid or "").strip()
        if not normalized_hostid:
            return None
        rows = self._call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status"],
                "hostids": [normalized_hostid],
                "limit": 1,
            },
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def _resolve_host_by_names(self, candidates: Iterable[str]) -> Optional[Dict]:
        for candidate in self._dedupe_values(candidates):
            rows = self._call(
                "host.get",
                {
                    "output": ["hostid", "host", "name", "status"],
                    "filter": {"host": [candidate]},
                    "limit": 1,
                },
            )
            if rows:
                return rows[0]
        return None

    def _resolve_host_by_ips(self, ip_candidates: Iterable[str]) -> Optional[Dict]:
        for ip_address in self._dedupe_values(ip_candidates):
            interfaces = self._call(
                "hostinterface.get",
                {
                    "output": ["hostid", "ip", "available", "error"],
                    "filter": {"ip": [ip_address]},
                    "limit": 1,
                },
            )
            if interfaces:
                hostid = interfaces[0].get("hostid")
                rows = self._call(
                    "host.get",
                    {
                        "output": ["hostid", "host", "name", "status"],
                        "hostids": [hostid],
                        "limit": 1,
                    },
                )
                if rows:
                    return rows[0]
        return None

    def resolve_host(self, olt) -> Optional[Dict]:
        cache_key = self._cache_key_for_olt(olt)
        if cache_key in self._host_cache:
            cached_host = self._host_cache.get(cache_key)
            cached_hostid = str((cached_host or {}).get("hostid") or "").strip()
            if cached_hostid:
                resolved = self._resolve_host_by_id(cached_hostid)
                if resolved is not None:
                    self._host_cache[cache_key] = resolved
                    return resolved
            # stale or invalid cache entry, force fresh resolution by name/ip
            self._host_cache.pop(cache_key, None)

        host = self._resolve_host_by_names(self._resolve_host_candidate_names(olt))
        if host is None:
            host = self._resolve_host_by_ips([str(getattr(olt, "ip_address", "") or "").strip()])
        if host is not None:
            self._host_cache[cache_key] = host
        return host

    def get_hostid(self, olt) -> Optional[str]:
        host = self.resolve_host(olt)
        if not host:
            return None
        hostid = host.get("hostid")
        return str(hostid) if hostid is not None else None

    def _resolve_host_for_sync(self, olt, previous: Optional[Dict] = None) -> Optional[Dict]:
        host = self.resolve_host(olt)
        if host:
            return host

        previous = previous or {}
        name_candidates = self._resolve_host_candidate_names(olt)
        previous_name = str(previous.get("name") or "").strip()
        if previous_name:
            name_candidates.extend(self._expand_host_name_candidates([previous_name]))

        ip_candidates = [str(getattr(olt, "ip_address", "") or "").strip()]
        previous_ip = str(previous.get("ip_address") or "").strip()
        if previous_ip:
            ip_candidates.append(previous_ip)

        host = self._resolve_host_by_names(name_candidates)
        if host is None:
            host = self._resolve_host_by_ips(ip_candidates)
        if host is not None:
            self._host_cache[self._cache_key_for_olt(olt)] = host
        return host

    def _sync_host_macros(self, hostid: str, desired_values: Dict[str, str]) -> None:
        normalized_values = {
            str(macro or "").strip(): str(value or "").strip()
            for macro, value in (desired_values or {}).items()
            if str(macro or "").strip()
        }
        if not normalized_values:
            return

        macro_rows = self._call(
            "usermacro.get",
            {
                "output": ["hostmacroid", "macro", "value"],
                "hostids": [hostid],
                "filter": {"macro": list(normalized_values.keys())},
            },
        )

        existing_by_macro: Dict[str, Dict] = {}
        for row in macro_rows:
            macro = str((row or {}).get("macro") or "").strip()
            if macro and macro not in existing_by_macro:
                existing_by_macro[macro] = row

        for macro, value in normalized_values.items():
            existing = existing_by_macro.get(macro)
            if existing:
                current_value = str((existing or {}).get("value") or "").strip()
                if current_value == value:
                    continue
                hostmacroid = str((existing or {}).get("hostmacroid") or "").strip()
                if not hostmacroid:
                    self._call("usermacro.create", {"hostid": hostid, "macro": macro, "value": value})
                    continue
                self._call("usermacro.update", {"hostmacroid": hostmacroid, "value": value})
                continue
            self._call("usermacro.create", {"hostid": hostid, "macro": macro, "value": value})

    def _get_host_group_id(self, name: str) -> Optional[str]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return None
        rows = self._call(
            "hostgroup.get",
            {
                "output": ["groupid", "name"],
                "filter": {"name": [normalized_name]},
                "limit": 1,
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        groupid = (rows[0] or {}).get("groupid")
        return str(groupid).strip() if groupid is not None else None

    def _get_or_create_host_group_id(self, name: str) -> Optional[str]:
        groupid = self._get_host_group_id(name)
        if groupid:
            return groupid
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return None
        result = self._call("hostgroup.create", {"name": normalized_name})
        groupids = (result or {}).get("groupids") if isinstance(result, dict) else None
        if isinstance(groupids, list) and groupids:
            return str(groupids[0]).strip()
        return self._get_host_group_id(normalized_name)

    @staticmethod
    def _desired_host_group_name() -> str:
        configured = str(getattr(settings, "ZABBIX_HOST_GROUP_NAME", "") or "").strip()
        return configured or DEFAULT_VARUNA_HOST_GROUP_NAME

    @staticmethod
    def _legacy_host_group_names() -> Tuple[str, ...]:
        configured = getattr(settings, "ZABBIX_HOST_GROUP_LEGACY_NAMES", DEFAULT_VARUNA_LEGACY_HOST_GROUP_NAMES)
        if isinstance(configured, (list, tuple)):
            names = [str(name or "").strip() for name in configured]
        else:
            names = [str(name or "").strip() for name in str(configured or "").split(",")]

        merged = list(DEFAULT_VARUNA_LEGACY_HOST_GROUP_NAMES) + names
        deduped: List[str] = []
        for name in merged:
            if not name:
                continue
            if name not in deduped:
                deduped.append(name)
        return tuple(deduped)

    def _sync_host_group_membership(self, hostid: str) -> None:
        desired_group_name = self._desired_host_group_name()
        desired_group_id = self._get_or_create_host_group_id(desired_group_name)
        if not desired_group_id:
            return

        legacy_group_ids = set()
        for legacy_name in self._legacy_host_group_names():
            if legacy_name == desired_group_name:
                continue
            legacy_group_id = self._get_host_group_id(legacy_name)
            if legacy_group_id:
                legacy_group_ids.add(legacy_group_id)

        rows = self._call(
            "host.get",
            {
                "output": ["hostid"],
                "hostids": [hostid],
                "selectHostGroups": ["groupid", "name"],
                "limit": 1,
            },
        )
        if not isinstance(rows, list) or not rows:
            return

        host_row = rows[0] if isinstance(rows[0], dict) else {}
        host_groups = host_row.get("hostgroups") if isinstance(host_row, dict) else []
        if not isinstance(host_groups, list):
            host_groups = []

        current_ids: List[str] = []
        for group_row in host_groups:
            group_id = str((group_row or {}).get("groupid") or "").strip()
            if group_id:
                current_ids.append(group_id)

        target_ids: List[str] = []
        for group_id in current_ids:
            if group_id in legacy_group_ids:
                continue
            if group_id not in target_ids:
                target_ids.append(group_id)
        if desired_group_id not in target_ids:
            target_ids.append(desired_group_id)

        if target_ids == current_ids:
            return

        self._call(
            "host.update",
            {
                "hostid": hostid,
                "groups": [{"groupid": group_id} for group_id in target_ids],
            },
        )

    @staticmethod
    def _template_name_candidates_for_olt(olt) -> List[str]:
        templates_cfg = (
            (getattr(getattr(olt, "vendor_profile", None), "oid_templates", {}) or {})
            .get("zabbix", {})
        )
        candidates: List[str] = []

        explicit_names = []
        if isinstance(templates_cfg, dict):
            raw_names = templates_cfg.get("host_template_names")
            if isinstance(raw_names, list):
                explicit_names.extend([str(name).strip() for name in raw_names if str(name or "").strip()])
            raw_single = templates_cfg.get("host_template_name")
            if raw_single:
                explicit_names.append(str(raw_single).strip())
        explicit_names = ZabbixService._dedupe_values(explicit_names)
        candidates.extend(explicit_names)
        if explicit_names:
            return candidates

        vendor = str(getattr(getattr(olt, "vendor_profile", None), "vendor", "") or "").strip()
        vendor_lc = vendor.lower()
        if vendor_lc == "fiberhome":
            candidates.extend(["OLT Fiberhome Unified", "Template OLT Fiberhome"])
        elif vendor_lc == "huawei":
            candidates.extend(["OLT Huawei Unified", "Template OLT Huawei"])
        elif vendor_lc == "zte":
            candidates.extend(["OLT ZTE C300", "Template OLT ZTE"])
        elif vendor_lc == "vsol like":
            candidates.extend(["OLT VSOL GPON 8P", "Template OLT VSOL Like"])
        elif vendor:
            candidates.append(f"Template OLT {vendor}")

        return ZabbixService._dedupe_values(candidates)

    @staticmethod
    def _shared_template_name_candidates() -> List[str]:
        return list(SHARED_TEMPLATE_NAME_CANDIDATES)

    def _resolve_template_ids_for_olt(self, olt, *, include_shared: bool = True) -> List[str]:
        template_ids: List[str] = []
        template_names = list(self._template_name_candidates_for_olt(olt))
        if include_shared:
            template_names.extend(self._shared_template_name_candidates())
        for name in self._dedupe_values(template_names):
            rows = self._call(
                "template.get",
                {
                    "output": ["templateid", "host", "name"],
                    "filter": {"host": [name]},
                    "limit": 1,
                },
            )
            if not isinstance(rows, list) or not rows:
                continue
            templateid = str((rows[0] or {}).get("templateid") or "").strip()
            if templateid and templateid not in template_ids:
                template_ids.append(templateid)
        return template_ids

    def _create_host_for_olt(self, olt) -> Optional[Dict]:
        olt_name = self._desired_host_name_for_olt(olt)
        if not olt_name:
            return None

        group_id = self._get_or_create_host_group_id(self._desired_host_group_name())
        if not group_id:
            raise ZabbixAPIError("Failed to resolve/create required host group for Varuna host sync.")

        vendor_template_ids = self._resolve_template_ids_for_olt(olt, include_shared=False)
        template_ids = self._resolve_template_ids_for_olt(olt, include_shared=True)
        if not vendor_template_ids:
            logger.warning(
                "Creating Zabbix host for OLT %s without linked template (template not found).",
                getattr(olt, "id", "?"),
            )

        create_payload: Dict[str, Any] = {
            "host": olt_name,
            "name": olt_name,
            "groups": [{"groupid": group_id}],
            "interfaces": [
                {
                    "type": 2,
                    "main": 1,
                    "useip": 1,
                    "ip": VARUNA_SNMP_IP_MACRO,
                    "dns": "",
                    "port": VARUNA_SNMP_PORT_MACRO,
                    "details": self._build_snmp_details({}, community_ref=VARUNA_SNMP_COMMUNITY_MACRO),
                }
            ],
            "tags": self._desired_host_tags_for_olt(olt),
        }
        if template_ids:
            create_payload["templates"] = [{"templateid": templateid} for templateid in template_ids]

        result = self._call("host.create", create_payload)
        hostids = (result or {}).get("hostids") if isinstance(result, dict) else None
        hostid = ""
        if isinstance(hostids, list) and hostids:
            hostid = str(hostids[0] or "").strip()
        if not hostid:
            return None

        self._sync_host_macros(
            hostid,
            {
                **self._runtime_macro_values_for_olt(olt),
                **self._interval_macro_values_for_olt(olt),
            },
        )
        self._host_cache.clear()
        return self.resolve_host(olt)

    def delete_olt_host(self, olt, *, previous: Optional[Dict] = None) -> bool:
        host = self._resolve_host_for_sync(olt, previous=previous)
        if not host:
            return False

        hostid = str((host or {}).get("hostid") or "").strip()
        if not hostid:
            return False

        self._call("host.delete", [hostid])
        self._host_cache.clear()
        return True

    def _get_primary_snmp_interface(self, hostid: str) -> Optional[Dict]:
        rows = self._call(
            "hostinterface.get",
            {
                "output": ["interfaceid", "type", "main", "useip", "ip", "dns", "port", "details"],
                "hostids": [hostid],
            },
        )
        snmp_rows = [row for row in rows if str((row or {}).get("type")) == "2"]
        if not snmp_rows:
            return None
        main_rows = [row for row in snmp_rows if str((row or {}).get("main")) == "1"]
        return (main_rows or snmp_rows)[0]

    def _ensure_availability_item(
        self,
        *,
        hostid: str,
        interfaceid: str,
        item_key: str,
    ) -> None:
        normalized_key = str(item_key or "").strip()
        if not normalized_key:
            return
        if self.get_single_item(hostid, normalized_key):
            return
        if not str(interfaceid or "").strip():
            return

        self._call(
            "item.create",
            {
                "hostid": hostid,
                "name": "SNMP availability",
                "type": 20,  # SNMP agent
                "key_": normalized_key,
                "value_type": 4,  # text
                "delay": VARUNA_AVAILABILITY_INTERVAL_MACRO,
                "history": "1d",
                "trends": "0",
                "interfaceid": str(interfaceid),
                "snmp_oid": "1.3.6.1.2.1.1.5.0",
                "tags": [
                    {"tag": "collector", "value": "varuna"},
                    {"tag": "metric", "value": "snmp_availability"},
                ],
            },
        )

    @staticmethod
    def _build_snmp_details(existing_details: Dict[str, Any], *, community_ref: str) -> Dict[str, str]:
        details = dict(existing_details or {})
        # Varuna currently supports only SNMPv2c in settings.
        details["version"] = "2"
        details["community"] = community_ref
        if "bulk" not in details:
            details["bulk"] = "1"
        return {str(k): str(v) for k, v in details.items() if k not in (None, "")}

    @staticmethod
    def _interval_macro_values_for_olt(olt) -> Dict[str, str]:
        discovery_seconds = max(int(getattr(olt, "discovery_interval_minutes", 0) or 0) * 60, 1)
        status_seconds = max(int(getattr(olt, "polling_interval_seconds", 0) or 0), 1)
        power_seconds = max(int(getattr(olt, "power_interval_seconds", 0) or 0), 1)
        availability_seconds = max(
            int(getattr(settings, "ZABBIX_AVAILABILITY_INTERVAL_SECONDS", 30) or 30),
            10,
        )
        history_days = max(int(getattr(olt, "history_days", 7) or 7), 1)
        return {
            VARUNA_DISCOVERY_INTERVAL_MACRO: f"{discovery_seconds}s",
            VARUNA_STATUS_INTERVAL_MACRO: f"{status_seconds}s",
            VARUNA_POWER_INTERVAL_MACRO: f"{power_seconds}s",
            VARUNA_AVAILABILITY_INTERVAL_MACRO: f"{availability_seconds}s",
            VARUNA_HISTORY_DAYS_MACRO: f"{history_days}d",
        }

    @staticmethod
    def _runtime_macro_values_for_olt(olt) -> Dict[str, str]:
        return {
            VARUNA_SNMP_IP_MACRO: str(getattr(olt, "ip_address", "") or "").strip(),
            VARUNA_SNMP_PORT_MACRO: str(int(getattr(olt, "snmp_port", 161) or 161)),
            VARUNA_SNMP_COMMUNITY_MACRO: str(getattr(olt, "snmp_community", "") or "").strip(),
        }

    @staticmethod
    def _normalize_host_tags(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        seen = set()
        for row in rows or []:
            tag = str((row or {}).get("tag") or "").strip()
            if not tag:
                continue
            value = str((row or {}).get("value") or "").strip()
            key = (tag, value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"tag": tag, "value": value})
        return normalized

    def _desired_host_tags_for_olt(self, olt) -> List[Dict[str, str]]:
        vendor_raw = str(getattr(getattr(olt, "vendor_profile", None), "vendor", "") or "").strip()
        model_raw = str(getattr(getattr(olt, "vendor_profile", None), "model_name", "") or "").strip()

        vendor = vendor_raw.lower()
        model = _canonical_model_tag_value(model_raw)

        return [
            {"tag": VARUNA_HOST_TAG_SOURCE, "value": VARUNA_HOST_TAG_SOURCE_VALUE},
            {"tag": VARUNA_HOST_TAG_VENDOR, "value": vendor},
            {"tag": VARUNA_HOST_TAG_MODEL, "value": model},
        ]

    def _sync_host_tags(self, hostid: str, olt) -> None:
        rows = self._call(
            "host.get",
            {
                "output": ["hostid"],
                "hostids": [hostid],
                "selectTags": ["tag", "value"],
                "limit": 1,
            },
        )
        if not isinstance(rows, list) or not rows:
            return

        host_row = rows[0] if isinstance(rows[0], dict) else {}
        existing_tags = self._normalize_host_tags(host_row.get("tags") if isinstance(host_row, dict) else [])
        managed_keys = {
            VARUNA_HOST_TAG_SOURCE.lower(),
            VARUNA_HOST_TAG_VENDOR.lower(),
            VARUNA_HOST_TAG_MODEL.lower(),
        }

        retained_tags: List[Dict[str, str]] = []
        for row in existing_tags:
            tag = str((row or {}).get("tag") or "").strip()
            if tag.lower() in managed_keys:
                continue
            retained_tags.append({"tag": tag, "value": str((row or {}).get("value") or "").strip()})

        desired_tags = self._normalize_host_tags(
            retained_tags + self._desired_host_tags_for_olt(olt)
        )
        if existing_tags == desired_tags:
            return

        self._call("host.update", {"hostid": hostid, "tags": desired_tags})

    def _sync_host_template_links(self, hostid: str, olt) -> None:
        desired_template_ids = self._resolve_template_ids_for_olt(olt, include_shared=True)
        if not desired_template_ids:
            return
        vendor_template_ids = self._resolve_template_ids_for_olt(olt, include_shared=False)
        shared_template_ids = {
            templateid
            for templateid in desired_template_ids
            if templateid not in vendor_template_ids
        }

        rows = self._call(
            "host.get",
            {
                "output": ["hostid"],
                "hostids": [hostid],
                "selectParentTemplates": ["templateid", "host", "name"],
                "limit": 1,
            },
        )
        if not isinstance(rows, list) or not rows:
            return

        host_row = rows[0] if isinstance(rows[0], dict) else {}
        parent_templates = []
        if isinstance(host_row, dict):
            parent_templates = host_row.get("parentTemplates")
            if not isinstance(parent_templates, list):
                parent_templates = host_row.get("parenttemplates")
            if not isinstance(parent_templates, list):
                parent_templates = []

        current_template_ids: List[str] = []
        for template_row in parent_templates:
            template_id = str((template_row or {}).get("templateid") or "").strip()
            if template_id and template_id not in current_template_ids:
                current_template_ids.append(template_id)

        shared_already_linked = any(templateid in shared_template_ids for templateid in current_template_ids)
        if shared_template_ids and not shared_already_linked:
            # Transition-safe behavior:
            # if host already has sentinel key inherited from legacy vendor template,
            # defer shared-template link to avoid duplicate-key inheritance errors.
            existing_sentinel = self.get_single_item(hostid, DEFAULT_AVAILABILITY_ITEM_KEY)
            if existing_sentinel:
                desired_template_ids = [
                    templateid
                    for templateid in desired_template_ids
                    if templateid not in shared_template_ids
                ]

        target_template_ids = self._dedupe_values(current_template_ids + desired_template_ids)
        if target_template_ids == current_template_ids:
            return

        self._call(
            "host.update",
            {
                "hostid": hostid,
                "templates": [{"templateid": template_id} for template_id in target_template_ids],
            },
        )

    def sync_olt_interval_macros(self, olt) -> bool:
        hostid = self.get_hostid(olt)
        if not hostid:
            return False

        self._sync_host_macros(hostid, self._interval_macro_values_for_olt(olt))
        return True

    def sync_olt_host_runtime(self, olt, *, previous: Optional[Dict] = None) -> bool:
        host = self._resolve_host_for_sync(olt, previous=previous)
        if not host:
            try:
                host = self._create_host_for_olt(olt)
            except Exception:
                logger.exception(
                    "Failed to create missing Zabbix host for OLT id=%s name=%s",
                    getattr(olt, "id", "?"),
                    getattr(olt, "name", ""),
                )
                return False
            if not host:
                return False

        hostid = str((host or {}).get("hostid") or "").strip()
        if not hostid:
            return False

        olt_name = self._desired_host_name_for_olt(olt)
        if olt_name:
            current_host_name = str((host or {}).get("host") or "").strip()
            current_visible_name = str((host or {}).get("name") or "").strip()
            if current_host_name != olt_name or current_visible_name != olt_name:
                self._call(
                    "host.update",
                    {
                        "hostid": hostid,
                        "host": olt_name,
                        "name": olt_name,
                    },
                )

        try:
            self._sync_host_group_membership(hostid)
        except Exception:
            logger.exception("Failed to sync host group membership for hostid=%s", hostid)
        try:
            self._sync_host_tags(hostid, olt)
        except Exception:
            logger.exception("Failed to sync host tags for hostid=%s", hostid)
        try:
            self._sync_host_template_links(hostid, olt)
        except Exception:
            logger.exception("Failed to sync host template links for hostid=%s", hostid)

        interface = self._get_primary_snmp_interface(hostid)
        runtime_macros = self._runtime_macro_values_for_olt(olt)
        interface_ip_ref = VARUNA_SNMP_IP_MACRO
        interface_port_ref = VARUNA_SNMP_PORT_MACRO
        interface_community_ref = VARUNA_SNMP_COMMUNITY_MACRO
        if interface:
            details = interface.get("details") if isinstance(interface.get("details"), dict) else {}
            current_community_ref = str((details or {}).get("community") or "").strip()
            desired_details = self._build_snmp_details(details or {}, community_ref=interface_community_ref)
            desired_payload = {
                "interfaceid": str((interface or {}).get("interfaceid") or ""),
                "useip": 1,
                "ip": interface_ip_ref,
                "dns": "",
                "port": interface_port_ref,
                "details": desired_details,
            }

            current_ip = str((interface or {}).get("ip") or "").strip()
            current_port = str((interface or {}).get("port") or "").strip()
            current_useip = str((interface or {}).get("useip") or "").strip()
            current_dns = str((interface or {}).get("dns") or "").strip()
            current_details = self._build_snmp_details(details or {}, community_ref=current_community_ref)
            has_diff = (
                current_ip != desired_payload["ip"]
                or current_port != desired_payload["port"]
                or current_useip != str(desired_payload["useip"])
                or current_dns != desired_payload["dns"]
                or current_details != desired_payload["details"]
            )
            if has_diff and desired_payload["interfaceid"]:
                self._call("hostinterface.update", desired_payload)
        else:
            self._call(
                "hostinterface.create",
                {
                    "hostid": hostid,
                    "type": 2,
                    "main": 1,
                    "useip": 1,
                    "ip": interface_ip_ref,
                    "dns": "",
                    "port": interface_port_ref,
                    "details": self._build_snmp_details({}, community_ref=interface_community_ref),
                },
            )
            interface = self._get_primary_snmp_interface(hostid)

        self._sync_host_macros(
            hostid,
            {
                **runtime_macros,
                **self._interval_macro_values_for_olt(olt),
            },
        )
        try:
            availability_key = str(
                (
                    ((getattr(olt.vendor_profile, "oid_templates", {}) or {}).get("zabbix", {}) or {}
                ).get("availability_item_key")
                or DEFAULT_AVAILABILITY_ITEM_KEY
            )).strip()
            self._ensure_availability_item(
                hostid=hostid,
                interfaceid=str((interface or {}).get("interfaceid") or ""),
                item_key=availability_key,
            )
        except Exception:
            logger.exception("Failed to ensure SNMP availability item for hostid=%s", hostid)
        return True

    def get_items_by_keys(self, hostid: str, keys: Iterable[str]) -> Dict[str, Dict]:
        deduped_keys = []
        seen = set()
        for key in keys:
            normalized = str(key or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped_keys.append(normalized)
        if not deduped_keys:
            return {}

        db_item_map = self._get_items_by_keys_from_db(hostid, deduped_keys)
        if db_item_map is not None:
            return db_item_map

        if self._db_latest_items_enabled():
            logger.error(
                "get_items_by_keys: DB read returned None for hostid=%s (%d keys); no API fallback",
                hostid,
                len(deduped_keys),
            )
        else:
            logger.warning(
                "get_items_by_keys: DB reader disabled and no API fallback for hostid=%s (%d keys)",
                hostid,
                len(deduped_keys),
            )
        return {}

    def execute_items_now(self, itemids: Iterable[str]) -> int:
        executed = 0
        seen = set()
        for raw_itemid in itemids:
            itemid = str(raw_itemid or "").strip()
            if not itemid or itemid in seen:
                continue
            seen.add(itemid)
            try:
                self._call(
                    "task.create",
                    {
                        "type": 6,
                        "request": {
                            "itemid": itemid,
                        },
                    },
                )
            except Exception:
                logger.exception("Zabbix task.create(check now) failed for itemid=%s", itemid)
                continue
            executed += 1
        return executed

    def execute_items_now_by_keys(self, hostid: str, keys: Iterable[str]) -> int:
        item_map = self.get_items_by_keys(hostid, keys)
        itemids = [str((row or {}).get("itemid") or "").strip() for row in item_map.values()]
        return self.execute_items_now(itemids)

    def get_single_item(self, hostid: str, key: str) -> Optional[Dict]:
        item_map = self.get_items_by_keys(hostid, [key])
        return item_map.get(key)

    def get_items_by_key_prefix(self, hostid: str, key_prefix: str, *, limit: int = 50000) -> List[Dict]:
        normalized_prefix = str(key_prefix or "").strip()
        if not normalized_prefix:
            return []
        rows = self._call(
            "item.get",
            {
                "output": ["itemid", "key_", "name", "lastclock", "lastvalue", "state"],
                "hostids": [hostid],
                "search": {"key_": normalized_prefix},
                "startSearch": True,
                "searchByAny": True,
                "sortfield": "itemid",
                "limit": max(int(limit or 0), 1),
            },
        )
        filtered: List[Dict] = []
        for row in rows:
            key = str((row or {}).get("key_", "")).strip()
            if key.startswith(normalized_prefix):
                filtered.append(row)
        return filtered

    def get_latest_item_by_key_prefix(self, hostid: str, key_prefix: str) -> Optional[Dict]:
        normalized_prefix = str(key_prefix or "").strip()
        if not normalized_prefix:
            return None
        # Some Zabbix versions reject sort by "lastclock" in item.get.
        # Fetch a bounded prefix set and pick the newest clock client-side.
        rows = self._call(
            "item.get",
            {
                "output": ["itemid", "key_", "lastclock", "lastvalue"],
                "hostids": [hostid],
                "search": {"key_": normalized_prefix},
                "startSearch": True,
                "searchByAny": True,
                "sortfield": "itemid",
                "limit": 20000,
            },
        )
        latest_row = None
        latest_clock = -1
        for row in rows:
            key = str((row or {}).get("key_", "")).strip()
            if not key.startswith(normalized_prefix):
                continue
            row_clock = _to_int_or_none((row or {}).get("lastclock")) or 0
            if row_clock > latest_clock:
                latest_clock = row_clock
                latest_row = row
        return latest_row

    def get_discovery_rule(self, hostid: str, key: str) -> Optional[Dict]:
        rows = self._call(
            "discoveryrule.get",
            {
                "output": ["itemid", "key_", "state", "value_type"],
                "hostids": [hostid],
                "filter": {"key_": [key]},
                "limit": 1,
            },
        )
        if not rows:
            return None
        return rows[0]

    def execute_item_now_by_key(self, hostid: str, key: str) -> int:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return 0

        item = self.get_single_item(hostid, normalized_key)
        if item and item.get("itemid"):
            return self.execute_items_now([str(item.get("itemid"))])

        discovery_rule = self.get_discovery_rule(hostid, normalized_key)
        if discovery_rule and discovery_rule.get("itemid"):
            return self.execute_items_now([str(discovery_rule.get("itemid"))])
        return 0

    def get_latest_history_value(
        self,
        *,
        itemid: str,
        value_type: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        history_candidates: List[int] = []
        parsed_value_type = _to_int_or_none(value_type)
        if parsed_value_type is not None and 0 <= parsed_value_type <= 5:
            history_candidates.append(parsed_value_type)
        for fallback_type in (4, 1):
            if fallback_type not in history_candidates:
                history_candidates.append(fallback_type)

        for history_type in history_candidates:
            rows = self._call(
                "history.get",
                {
                    "output": ["clock", "value"],
                    "history": history_type,
                    "itemids": [str(itemid)],
                    "sortfield": "clock",
                    "sortorder": "DESC",
                    "limit": 1,
                },
            )
            if not rows:
                continue
            row = rows[0] if isinstance(rows[0], dict) else {}
            value = row.get("value")
            read_at = _from_epoch_to_iso(_to_int_or_none(row.get("clock")))
            if value not in (None, ""):
                return str(value), read_at
            if read_at:
                return None, read_at
        return None, None

    def get_latest_valid_power_history_sample(
        self,
        *,
        itemid: str,
        value_type: Optional[str] = None,
        time_from: Optional[int] = None,
        limit: int = 10,
    ) -> Tuple[Optional[float], Optional[str], Optional[int]]:
        rows = self.get_history_series(
            itemid=itemid,
            value_type=value_type,
            time_from=time_from,
            sortorder="DESC",
            limit=max(int(limit or 0), 1),
        )
        for row in rows:
            value = normalize_power_value(_to_float_or_none((row or {}).get("value")))
            clock_epoch = _to_int_or_none((row or {}).get("clock_epoch"))
            if value is None or clock_epoch is None or clock_epoch <= 0:
                continue
            return value, _from_epoch_to_iso(clock_epoch), clock_epoch
        return None, None, None

    def get_latest_valid_power_history_samples(
        self,
        *,
        item_specs: Dict[str, Optional[str]],
        time_from: Optional[int] = None,
        limit_per_item: int = 10,
    ) -> Dict[str, Dict[str, Any]]:
        normalized_specs: Dict[str, Optional[str]] = {}
        for raw_itemid, raw_value_type in (item_specs or {}).items():
            itemid = str(raw_itemid or "").strip()
            if not itemid:
                continue
            normalized_specs[itemid] = raw_value_type

        if not normalized_specs:
            return {}

        results: Dict[str, Dict[str, Any]] = {}
        db_results = self._get_latest_valid_power_history_samples_from_db(
            item_specs=normalized_specs,
            time_from=time_from,
            limit_per_item=limit_per_item,
        )
        if db_results is not None:
            results.update(db_results)
            normalized_specs = {
                itemid: value_type
                for itemid, value_type in normalized_specs.items()
                if itemid not in results
            }
            if not normalized_specs:
                return results

        normalized_time_from = _to_int_or_none(time_from)
        normalized_limit_per_item = max(int(limit_per_item or 0), 1)
        unresolved = set(normalized_specs.keys())
        grouped_itemids: Dict[int, List[str]] = defaultdict(list)

        for itemid, value_type in normalized_specs.items():
            history_types = self._history_type_candidates(value_type=value_type)
            primary_history_type = history_types[0] if history_types else 0
            grouped_itemids[primary_history_type].append(itemid)

        for history_type, group_itemids in grouped_itemids.items():
            pending_itemids = [itemid for itemid in group_itemids if itemid in unresolved]
            if not pending_itemids:
                continue

            limit = min(
                max(len(pending_itemids) * normalized_limit_per_item * 4, len(pending_itemids)),
                20000,
            )
            params: Dict[str, Any] = {
                "output": ["itemid", "clock", "value"],
                "history": history_type,
                "itemids": pending_itemids,
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": limit,
            }
            if normalized_time_from is not None and normalized_time_from >= 0:
                params["time_from"] = normalized_time_from

            rows = self._call("history.get", params)
            if not isinstance(rows, list) or not rows:
                continue

            seen_rows_by_itemid: Dict[str, int] = defaultdict(int)
            pending_set = set(pending_itemids)
            for row in rows:
                itemid = str((row or {}).get("itemid") or "").strip()
                if not itemid or itemid not in pending_set or itemid not in unresolved:
                    continue
                if seen_rows_by_itemid[itemid] >= normalized_limit_per_item:
                    continue
                seen_rows_by_itemid[itemid] += 1

                value = normalize_power_value(_to_float_or_none((row or {}).get("value")))
                clock_epoch = _to_int_or_none((row or {}).get("clock"))
                if value is None or clock_epoch is None or clock_epoch <= 0:
                    continue

                results[itemid] = {
                    "value": value,
                    "clock": _from_epoch_to_iso(clock_epoch),
                    "clock_epoch": clock_epoch,
                }
                unresolved.discard(itemid)

        for itemid in list(unresolved):
            value, read_at, clock_epoch = self.get_latest_valid_power_history_sample(
                itemid=itemid,
                value_type=normalized_specs.get(itemid),
                time_from=normalized_time_from,
                limit=normalized_limit_per_item,
            )
            if value is None or clock_epoch is None or clock_epoch <= 0:
                continue
            results[itemid] = {
                "value": value,
                "clock": read_at or _from_epoch_to_iso(clock_epoch),
                "clock_epoch": clock_epoch,
            }

        return results

    @staticmethod
    def _history_type_candidates(value_type: Optional[str] = None) -> List[int]:
        candidates: List[int] = []
        parsed = _to_int_or_none(value_type)
        if parsed is not None and 0 <= parsed <= 5:
            candidates.append(parsed)
        # Try common metric/text types first while keeping a full fallback chain.
        for history_type in (1, 0, 3, 4, 2, 5):
            if history_type not in candidates:
                candidates.append(history_type)
        return candidates

    def get_history_series(
        self,
        *,
        itemid: str,
        value_type: Optional[str] = None,
        time_from: Optional[int] = None,
        time_till: Optional[int] = None,
        sortorder: str = "ASC",
        limit: int = 20000,
    ) -> List[Dict[str, Optional[str]]]:
        normalized_itemid = str(itemid or "").strip()
        if not normalized_itemid:
            return []

        normalized_limit = max(int(limit or 0), 1)
        normalized_sort = str(sortorder or "ASC").upper()
        if normalized_sort not in {"ASC", "DESC"}:
            normalized_sort = "ASC"

        history_types = self._history_type_candidates(value_type=value_type)
        for history_type in history_types:
            params: Dict[str, Any] = {
                "output": ["clock", "value"],
                "history": history_type,
                "itemids": [normalized_itemid],
                "sortfield": "clock",
                "sortorder": normalized_sort,
                "limit": normalized_limit,
            }
            parsed_from = _to_int_or_none(time_from)
            parsed_till = _to_int_or_none(time_till)
            if parsed_from is not None and parsed_from >= 0:
                params["time_from"] = parsed_from
            if parsed_till is not None and parsed_till >= 0:
                params["time_till"] = parsed_till

            rows = self._call("history.get", params)
            if not isinstance(rows, list) or not rows:
                continue

            parsed_rows: List[Dict[str, Optional[str]]] = []
            for row in rows:
                clock_epoch = _to_int_or_none((row or {}).get("clock"))
                if clock_epoch is None:
                    continue
                parsed_rows.append(
                    {
                        "clock_epoch": clock_epoch,
                        "clock": _from_epoch_to_iso(clock_epoch),
                        "value": None if (row or {}).get("value") is None else str((row or {}).get("value")).strip(),
                    }
                )
            if parsed_rows:
                return parsed_rows
        return []

    def get_previous_history_sample(
        self,
        *,
        itemid: str,
        value_type: Optional[str] = None,
        before_epoch: Optional[int] = None,
    ) -> Optional[Dict[str, Optional[str]]]:
        parsed_before = _to_int_or_none(before_epoch)
        if parsed_before is None or parsed_before <= 0:
            return None
        rows = self.get_history_series(
            itemid=itemid,
            value_type=value_type,
            time_till=parsed_before - 1,
            sortorder="DESC",
            limit=1,
        )
        if not rows:
            return None
        return rows[0]

    def fetch_onu_item_timelines(
        self,
        olt,
        *,
        index: str,
        status_item_key_pattern: str,
        reason_item_key_pattern: str = "",
        onu_rx_item_key_pattern: str = "",
        olt_rx_item_key_pattern: str = "",
        status_time_from: Optional[int] = None,
        status_time_till: Optional[int] = None,
        power_time_from: Optional[int] = None,
        power_time_till: Optional[int] = None,
        status_limit: int = 20000,
        power_limit: int = 20000,
    ) -> Dict[str, Any]:
        normalized_index = str(index or "").strip(".")
        if not normalized_index:
            return {}

        hostid = self.get_hostid(olt)
        if not hostid:
            return {}

        key_patterns = {
            "status": str(status_item_key_pattern or "").strip(),
            "reason": str(reason_item_key_pattern or "").strip(),
            "onu_rx": str(onu_rx_item_key_pattern or "").strip(),
            "olt_rx": str(olt_rx_item_key_pattern or "").strip(),
        }
        keys: Dict[str, str] = {}
        for name, pattern in key_patterns.items():
            if not pattern:
                continue
            keys[name] = pattern.replace("{index}", normalized_index)

        if not keys:
            return {}

        item_map = self.get_items_by_keys(hostid, keys.values())

        def _extract_item_meta(kind: str) -> Optional[Dict[str, str]]:
            key = keys.get(kind)
            if not key:
                return None
            row = item_map.get(key) or {}
            itemid = str((row or {}).get("itemid") or "").strip()
            if not itemid:
                return None
            return {
                "key": key,
                "itemid": itemid,
                "value_type": str((row or {}).get("value_type") or "").strip(),
            }

        status_item = _extract_item_meta("status")
        reason_item = _extract_item_meta("reason")
        onu_rx_item = _extract_item_meta("onu_rx")
        olt_rx_item = _extract_item_meta("olt_rx")

        status_samples: List[Dict[str, Optional[str]]] = []
        status_previous: Optional[Dict[str, Optional[str]]] = None
        reason_samples: List[Dict[str, Optional[str]]] = []
        onu_rx_samples: List[Dict[str, Optional[str]]] = []
        olt_rx_samples: List[Dict[str, Optional[str]]] = []

        if status_item:
            status_samples = self.get_history_series(
                itemid=status_item["itemid"],
                value_type=status_item.get("value_type"),
                time_from=status_time_from,
                time_till=status_time_till,
                sortorder="ASC",
                limit=status_limit,
            )
            status_previous = self.get_previous_history_sample(
                itemid=status_item["itemid"],
                value_type=status_item.get("value_type"),
                before_epoch=status_time_from,
            )
        if reason_item:
            reason_samples = self.get_history_series(
                itemid=reason_item["itemid"],
                value_type=reason_item.get("value_type"),
                time_from=status_time_from,
                time_till=status_time_till,
                sortorder="ASC",
                limit=status_limit,
            )
        if onu_rx_item:
            onu_rx_samples = self.get_history_series(
                itemid=onu_rx_item["itemid"],
                value_type=onu_rx_item.get("value_type"),
                time_from=power_time_from,
                time_till=power_time_till,
                sortorder="ASC",
                limit=power_limit,
            )
        if olt_rx_item:
            olt_rx_samples = self.get_history_series(
                itemid=olt_rx_item["itemid"],
                value_type=olt_rx_item.get("value_type"),
                time_from=power_time_from,
                time_till=power_time_till,
                sortorder="ASC",
                limit=power_limit,
            )

        return {
            "status_item": status_item,
            "reason_item": reason_item,
            "onu_rx_item": onu_rx_item,
            "olt_rx_item": olt_rx_item,
            "status_samples": status_samples,
            "status_previous": status_previous,
            "reason_samples": reason_samples,
            "onu_rx_samples": onu_rx_samples,
            "olt_rx_samples": olt_rx_samples,
        }

    def check_olt_reachability(self, olt) -> Tuple[bool, str]:
        hostid = self.get_hostid(olt)
        if not hostid:
            return False, "Zabbix host not found."

        zabbix_cfg = (
            (getattr(olt.vendor_profile, "oid_templates", {}) or {}).get("zabbix", {})
            if isinstance((getattr(olt.vendor_profile, "oid_templates", {}) or {}).get("zabbix", {}), dict)
            else {}
        )
        availability_key = str(
            zabbix_cfg.get("availability_item_key") or DEFAULT_AVAILABILITY_ITEM_KEY
        ).strip()
        availability_freshness_seconds = max(
            int(getattr(settings, "ZABBIX_AVAILABILITY_STALE_SECONDS", 45) or 45),
            5,
        )
        if not availability_key:
            return False, "SNMP availability item key is not configured."

        def _classify_availability(item_row: Optional[Dict]) -> Tuple[bool, str]:
            item = item_row or {}
            if not item:
                return False, f'SNMP availability item "{availability_key}" was not found.'

            item_status = str(item.get("status", "0")).strip()
            if item_status == "1":
                return False, "SNMP availability item is disabled."

            item_state = str(item.get("state", "0")).strip()
            item_error = str(item.get("error") or "").strip()
            if item_state == "1":
                return False, (item_error or "SNMP availability item is not supported.")[:2000]

            lastclock = _to_int_or_none(item.get("lastclock"))
            if not lastclock:
                return False, "SNMP availability item has no samples yet."

            age_seconds = int(datetime.now(tz=dt_timezone.utc).timestamp()) - lastclock
            if age_seconds > availability_freshness_seconds:
                return False, f"Last SNMP availability sample is stale ({age_seconds}s old)."

            return True, ""

        availability_item = self.get_single_item(hostid, availability_key)
        reachable, detail = _classify_availability(availability_item)
        if reachable:
            return True, ""

        availability_itemid = str((availability_item or {}).get("itemid") or "").strip()
        if availability_itemid and "stale" in detail.lower():
            try:
                executed = self.execute_items_now([availability_itemid])
                if executed:
                    time.sleep(0.8)
                    refreshed_item = self.get_single_item(hostid, availability_key)
                    refreshed_reachable, refreshed_detail = _classify_availability(refreshed_item)
                    if refreshed_reachable:
                        return True, ""
                    detail = refreshed_detail or detail
            except Exception:
                logger.exception(
                    "Failed to force-refresh SNMP availability item for hostid=%s itemid=%s",
                    hostid,
                    availability_itemid,
                )

        return False, detail

    @staticmethod
    def _extract_index_from_item_key(key: str, key_prefix: str) -> str:
        key_value = str(key or "").strip()
        prefix_value = str(key_prefix or "").strip()
        if not key_value or not prefix_value or not key_value.startswith(prefix_value):
            return ""
        suffix = key_value[len(prefix_value):]
        if suffix.endswith("]"):
            suffix = suffix[:-1]
        return str(suffix or "").strip().strip(".")

    @staticmethod
    def _status_item_prefix(status_item_key_pattern: str) -> str:
        pattern = str(status_item_key_pattern or "").strip()
        if not pattern:
            return ""
        if "{index}" in pattern:
            return pattern.split("{index}", 1)[0]
        bracket_idx = pattern.find("[")
        if bracket_idx >= 0:
            return pattern[: bracket_idx + 1]
        return pattern

    @staticmethod
    def _decode_fiberhome_flat_index(index: str) -> Optional[Tuple[int, int, int]]:
        parsed = _to_int_or_none(index)
        if parsed is None or parsed < 0:
            return None
        slot_encoded = (parsed >> 24) & 0xFF
        pon_encoded = (parsed >> 16) & 0xFF
        onu_id = (parsed >> 8) & 0xFF

        # Fiberhome index layout observed in production:
        # [slot*2, pon*8, onu_id, 0]
        slot_id = slot_encoded // 2
        pon_id = pon_encoded // 8
        if slot_id <= 0 or pon_id <= 0 or onu_id <= 0:
            return None
        return slot_id, pon_id, onu_id

    @staticmethod
    def _normalize_status_serial_token(raw: str) -> str:
        return _normalize_status_serial_token(raw)

    @staticmethod
    def _split_status_item_body_name_serial(body: str) -> Tuple[str, str]:
        normalized_body = str(body or "").strip()
        if not normalized_body:
            return "", ""

        bracket_match = HUAWEI_STATUS_NAME_WITH_SERIAL_RE.match(normalized_body)
        if bracket_match:
            return (
                str(bracket_match.group("name") or "").strip(),
                ZabbixService._normalize_status_serial_token(bracket_match.group("serial")),
            )

        parts = normalized_body.split()
        if not parts:
            return normalized_body, ""

        trailing = ZabbixService._normalize_status_serial_token(parts[-1])
        if re.fullmatch(r"[A-Z0-9]{8,32}", trailing):
            return normalize_discovery_onu_name(" ".join(parts[:-1]).strip(), serial=trailing), trailing
        return normalize_discovery_onu_name(normalized_body), ""

    def _build_discovery_row_from_status_item(self, olt, *, index: str, item_name: str) -> Optional[Dict[str, str]]:
        if not index:
            return None
        normalized_name = str(item_name or "").strip()
        vendor = str(getattr(olt.vendor_profile, "vendor", "") or "").strip().lower()
        row: Dict[str, str] = {"{#SNMPINDEX}": index}

        if "huawei" in vendor:
            match = HUAWEI_STATUS_ITEM_RE.match(normalized_name)
            parsed_chassi = ""
            if not match:
                match = HUAWEI_STATUS_ITEM_PON_RE.match(normalized_name)
            if match:
                display_name = match.group("name").strip()
                serial_value = ""
                parsed_chassi = str(match.groupdict().get("chassi") or "").strip()
                parsed_name = HUAWEI_STATUS_NAME_WITH_SERIAL_RE.match(display_name)
                if parsed_name:
                    display_name = parsed_name.group("name").strip()
                    serial_value = self._normalize_status_serial_token(parsed_name.group("serial"))
                    if serial_value and re.fullmatch(r"[0-9A-F ]+", serial_value):
                        compact_hex = serial_value.replace(" ", "")
                        if len(compact_hex) % 2 == 0:
                            serial_value = f"0X{compact_hex}"
                else:
                    # New Huawei template naming format uses plain trailing serial
                    # without brackets:
                    # "ONU <slot>/<pon>/<onu> <name> <serial>: Status"
                    parsed_name = HUAWEI_STATUS_NAME_WITH_TRAILING_SERIAL_RE.match(display_name)
                    if parsed_name:
                        display_name = parsed_name.group("name").strip()
                        serial_value = self._normalize_status_serial_token(parsed_name.group("serial"))
                row.update(
                    {
                        "{#SLOT}": match.group("slot"),
                        "{#PON}": match.group("pon"),
                        "{#ONU_ID}": match.group("onu"),
                        "{#ONU_NAME}": display_name,
                    }
                )
                if parsed_chassi:
                    row["{#CHASSI}"] = parsed_chassi
                if serial_value:
                    row["{#SERIAL}"] = serial_value
                if "." in index:
                    row["{#PON_ID}"] = index.split(".", 1)[0].strip()

        if "fiberhome" in vendor:
            match = FIBERHOME_STATUS_ITEM_RE.match(normalized_name)
            if match:
                row.update(
                    {
                        "{#SLOT}": match.group("slot"),
                        "{#PON}": match.group("pon"),
                        "{#ONU_ID}": match.group("onu"),
                        "{#SERIAL}": self._normalize_status_serial_token(match.group("serial")),
                    }
                )
            else:
                match_serial_only = FIBERHOME_STATUS_ITEM_SERIAL_ONLY_RE.match(normalized_name)
                if match_serial_only:
                    row["{#SERIAL}"] = self._normalize_status_serial_token(match_serial_only.group("serial"))
            if not all(row.get(macro) for macro in ("{#SLOT}", "{#PON}", "{#ONU_ID}")):
                decoded = self._decode_fiberhome_flat_index(index)
                if decoded:
                    slot_id, pon_id, onu_id = decoded
                    row.update(
                        {
                            "{#SLOT}": str(slot_id),
                            "{#PON}": str(pon_id),
                            "{#ONU_ID}": str(onu_id),
                        }
                    )

        has_primary_identity = all(row.get(macro) for macro in ("{#SLOT}", "{#PON}", "{#ONU_ID}"))
        if not has_primary_identity:
            oid_templates = getattr(olt.vendor_profile, "oid_templates", {}) or {}
            indexing_cfg = oid_templates.get("indexing", {}) if isinstance(oid_templates, dict) else {}
            parsed = parse_onu_index(index, indexing_cfg or {}) if isinstance(indexing_cfg, dict) else None
            if parsed:
                row.update(
                    {
                        "{#SLOT}": str(parsed.get("slot_id")),
                        "{#PON}": str(parsed.get("pon_id")),
                        "{#ONU_ID}": str(parsed.get("onu_id")),
                    }
                )
                if parsed.get("pon_numeric") is not None:
                    row["{#PON_ID}"] = str(parsed.get("pon_numeric"))

        # Generic fallback for vendors whose status item names follow:
        # "ONU <slot>/<pon>/<onu> <name> <serial>: Status" (for example VSOL/ZTE).
        generic_match = GENERIC_ONU_STATUS_ITEM_RE.match(normalized_name)
        if generic_match:
            row.setdefault("{#SLOT}", str(generic_match.group("slot") or "").strip())
            row.setdefault("{#PON}", str(generic_match.group("pon") or "").strip())
            row.setdefault("{#ONU_ID}", str(generic_match.group("onu") or "").strip())
            parsed_name, parsed_serial = self._split_status_item_body_name_serial(generic_match.group("body"))
            if parsed_name and not row.get("{#ONU_NAME}"):
                row["{#ONU_NAME}"] = parsed_name
            if parsed_serial and not row.get("{#SERIAL}"):
                row["{#SERIAL}"] = parsed_serial

        if not all(row.get(macro) for macro in ("{#SLOT}", "{#PON}", "{#ONU_ID}")):
            return None
        return row

    def _fallback_discovery_rows_from_status_items(self, olt, hostid: str) -> Tuple[List[Dict], Optional[str]]:
        templates = (getattr(olt.vendor_profile, "oid_templates", {}) or {})
        zabbix_cfg = templates.get("zabbix", {}) if isinstance(templates.get("zabbix", {}), dict) else {}
        status_pattern = str(zabbix_cfg.get("status_item_key_pattern") or "onuStatusValue[{index}]").strip()
        status_prefix = self._status_item_prefix(status_pattern)
        if not status_prefix:
            return [], None

        item_rows = self.get_items_by_key_prefix(hostid, status_prefix)
        if not item_rows:
            return [], None

        newest_clock = 0
        rows_by_index: Dict[str, Dict[str, str]] = {}
        for item in item_rows:
            key = str((item or {}).get("key_", "")).strip()
            index = self._extract_index_from_item_key(key, status_prefix)
            if not index:
                continue
            parsed_row = self._build_discovery_row_from_status_item(
                olt,
                index=index,
                item_name=str((item or {}).get("name") or ""),
            )
            if not parsed_row:
                continue
            rows_by_index[index] = parsed_row
            newest_clock = max(newest_clock, _to_int_or_none((item or {}).get("lastclock")) or 0)

        if not rows_by_index:
            return [], _from_epoch_to_iso(newest_clock)
        return list(rows_by_index.values()), _from_epoch_to_iso(newest_clock)

    def fetch_discovery_rows(self, olt, discovery_item_key: str) -> Tuple[List[Dict], Optional[str]]:
        hostid = self.get_hostid(olt)
        if not hostid:
            return [], None
        key = str(discovery_item_key or "").strip()
        if not key:
            return [], None
        item = self.get_single_item(hostid, key)
        raw_value = None
        read_at = None
        if item:
            raw_value = item.get("lastvalue")
            read_at = _from_epoch_to_iso(_to_int_or_none(item.get("lastclock")))
        else:
            # Zabbix LLD rules (discoveryrule.get) are not returned by item.get.
            # When the discovery key points to an LLD rule key, read its latest
            # history payload directly by discovery-rule itemid.
            discovery_rule = self.get_discovery_rule(hostid, key)
            if discovery_rule:
                raw_value, read_at = self.get_latest_history_value(
                    itemid=str(discovery_rule.get("itemid") or ""),
                    value_type=str(discovery_rule.get("value_type") or ""),
                )
        parsed_rows: List[Dict[str, Any]] = []
        if raw_value not in (None, ""):
            try:
                parsed = json.loads(str(raw_value))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = []
            if isinstance(parsed, list):
                parsed_rows = [row for row in parsed if isinstance(row, dict)]
        if parsed_rows:
            needs_identity_repair = any(
                (
                    not _normalize_status_serial_token(
                        row.get("{#SERIAL}") or row.get("{#ONU_SERIAL}") or ""
                    )
                )
                or normalize_discovery_onu_name(
                    row.get("{#ONU_NAME}") or "",
                    serial=row.get("{#SERIAL}") or row.get("{#ONU_SERIAL}") or "",
                ) != str(row.get("{#ONU_NAME}") or "").strip()
                for row in parsed_rows
            )
            fallback_rows: List[Dict[str, Any]] = []
            fallback_read_at: Optional[str] = None
            if needs_identity_repair:
                fallback_rows, fallback_read_at = self._fallback_discovery_rows_from_status_items(olt, hostid)
                if fallback_rows:
                    parsed_rows = _repair_discovery_identity_rows(parsed_rows, fallback_rows)
            return parsed_rows, fallback_read_at or read_at

        # Robust fallback for environments where LLD payload history is unavailable:
        # reconstruct discovery rows from created per-ONU status items.
        fallback_rows, fallback_read_at = self._fallback_discovery_rows_from_status_items(olt, hostid)
        if fallback_rows:
            return fallback_rows, fallback_read_at or read_at
        return [], read_at or fallback_read_at

    def fetch_status_by_index(
        self,
        olt,
        indexes: Iterable[str],
        *,
        status_item_key_pattern: str,
        reason_item_key_pattern: str = "",
        include_meta: bool = False,
    ) -> Tuple[Dict[str, Dict], Optional[str]]:
        hostid = self.get_hostid(olt)
        if not hostid:
            return {}, None

        keys: List[str] = []
        index_by_status_key: Dict[str, str] = {}
        index_by_reason_key: Dict[str, str] = {}
        for raw_index in indexes:
            index = str(raw_index or "").strip(".")
            if not index:
                continue
            status_key = status_item_key_pattern.replace("{index}", index)
            keys.append(status_key)
            index_by_status_key[status_key] = index
            if reason_item_key_pattern:
                reason_key = reason_item_key_pattern.replace("{index}", index)
                keys.append(reason_key)
                index_by_reason_key[reason_key] = index

        item_map = self.get_items_by_keys(hostid, keys)
        status_map: Dict[str, Dict] = {}
        newest_clock = 0
        for status_key, index in index_by_status_key.items():
            row = item_map.get(status_key)
            if not row:
                continue
            value = str(row.get("lastvalue") or "").strip().lower()
            if value not in {"online", "offline", "unknown", "link_loss", "dying_gasp"}:
                continue
            normalized_status = value
            reason = ""
            if value in {"link_loss", "dying_gasp"}:
                # Some vendors (Fiberhome) can encode offline reason directly in
                # the status item value, removing the need for a second reason item.
                normalized_status = "offline"
                reason = value
            reason_key = None
            for candidate, candidate_index in index_by_reason_key.items():
                if candidate_index == index:
                    reason_key = candidate
                    break
            if normalized_status == "online":
                reason = ""
            elif reason:
                pass
            elif reason_key:
                reason_row = item_map.get(reason_key)
                reason_value = str((reason_row or {}).get("lastvalue") or "").strip().lower()
                if reason_value in {"link_loss", "dying_gasp", "unknown"}:
                    reason = reason_value
                else:
                    reason = "unknown"
            else:
                reason = "unknown" if normalized_status != "online" else ""

            clock = _to_int_or_none(row.get("lastclock")) or 0
            newest_clock = max(newest_clock, clock)
            entry = {"status": normalized_status, "reason": reason}
            if include_meta:
                entry["status_clock_epoch"] = clock
                entry["status_clock"] = _from_epoch_to_iso(clock)
                entry["status_itemid"] = str((row or {}).get("itemid") or "").strip()
                entry["status_prevvalue"] = str((row or {}).get("prevvalue") or "").strip().lower()
            status_map[index] = entry
        return status_map, _from_epoch_to_iso(newest_clock)

    def fetch_previous_status_samples(
        self,
        *,
        item_clock_by_itemid: Dict[str, int],
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """
        For each status itemid, fetch the most recent history sample older than the
        provided current clock (epoch seconds).
        """
        results: Dict[str, Dict[str, Optional[str]]] = {}
        for raw_itemid, raw_current_clock in (item_clock_by_itemid or {}).items():
            itemid = str(raw_itemid or "").strip()
            current_clock = _to_int_or_none(raw_current_clock)
            if not itemid or current_clock is None or current_clock <= 0:
                continue

            rows = self._call(
                "history.get",
                {
                    "output": ["clock", "value"],
                    "history": 1,
                    "itemids": [itemid],
                    "sortfield": "clock",
                    "sortorder": "DESC",
                    "limit": 5,
                },
            )

            previous_value = None
            previous_clock = None
            for row in rows:
                sample_clock = _to_int_or_none((row or {}).get("clock"))
                if sample_clock is None:
                    continue
                if sample_clock >= current_clock:
                    continue
                previous_clock = sample_clock
                previous_value = str((row or {}).get("value") or "").strip().lower()
                break

            if previous_clock is None:
                continue

            results[itemid] = {
                "status": previous_value,
                "clock_epoch": previous_clock,
                "clock": _from_epoch_to_iso(previous_clock),
            }
        return results

    def fetch_power_by_index(
        self,
        olt,
        indexes: Iterable[str],
        *,
        onu_rx_item_key_pattern: str,
        olt_rx_item_key_pattern: str = "",
        history_fallback: bool = True,
    ) -> Tuple[Dict[str, Dict], Optional[str]]:
        hostid = self.get_hostid(olt)
        if not hostid:
            return {}, None

        keys: List[str] = []
        key_to_target: Dict[str, Tuple[str, str]] = {}
        for raw_index in indexes:
            index = str(raw_index or "").strip(".")
            if not index:
                continue
            onu_key = onu_rx_item_key_pattern.replace("{index}", index)
            keys.append(onu_key)
            key_to_target[onu_key] = (index, "onu_rx_power")
            if olt_rx_item_key_pattern:
                olt_key = olt_rx_item_key_pattern.replace("{index}", index)
                keys.append(olt_key)
                key_to_target[olt_key] = (index, "olt_rx_power")

        item_map = self.get_items_by_keys(hostid, keys)
        history_lookback_seconds = max(
            int(getattr(olt, "history_days", 7) or 7) * 86400,
            86400,
        )
        history_time_from = max(0, int(time.time()) - history_lookback_seconds)
        results: Dict[str, Dict] = {}
        newest_clock = 0
        history_fallback_specs: Dict[str, Optional[str]] = {}
        history_fallback_targets: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for key, row in item_map.items():
            target = key_to_target.get(key)
            if not target:
                continue
            index, field = target
            payload = results.setdefault(
                index,
                {"onu_rx_power": None, "olt_rx_power": None, "power_read_at": None, "power_clock_epoch": None},
            )
            value = normalize_power_value(_to_float_or_none(row.get("lastvalue")))
            clock = _to_int_or_none(row.get("lastclock")) or 0
            read_at = _from_epoch_to_iso(clock)
            itemid = str((row or {}).get("itemid") or "").strip()
            if (value is None or clock <= 0) and itemid:
                history_fallback_specs.setdefault(
                    itemid,
                    str((row or {}).get("value_type") or "").strip(),
                )
                history_fallback_targets[itemid].append((index, field))
                continue

            payload[field] = value
            newest_clock = max(newest_clock, clock)
            previous_clock = _to_int_or_none(payload.get("power_clock_epoch")) or 0
            if clock >= previous_clock:
                payload["power_clock_epoch"] = clock
                if read_at:
                    payload["power_read_at"] = read_at

        if history_fallback and history_fallback_specs:
            history_fallback_map = self.get_latest_valid_power_history_samples(
                item_specs=history_fallback_specs,
                time_from=history_time_from,
            )
            for itemid, fallback in history_fallback_map.items():
                fallback_value = normalize_power_value(_to_float_or_none((fallback or {}).get("value")))
                fallback_clock = _to_int_or_none((fallback or {}).get("clock_epoch"))
                if fallback_value is None or fallback_clock is None or fallback_clock <= 0:
                    continue
                fallback_read_at = str((fallback or {}).get("clock") or "").strip() or _from_epoch_to_iso(fallback_clock)
                for index, field in history_fallback_targets.get(itemid, []):
                    payload = results.setdefault(
                        index,
                        {"onu_rx_power": None, "olt_rx_power": None, "power_read_at": None, "power_clock_epoch": None},
                    )
                    payload[field] = fallback_value
                    previous_clock = _to_int_or_none(payload.get("power_clock_epoch")) or 0
                    if fallback_clock >= previous_clock:
                        payload["power_clock_epoch"] = fallback_clock
                        payload["power_read_at"] = fallback_read_at
                    newest_clock = max(newest_clock, fallback_clock)
        return results, _from_epoch_to_iso(newest_clock)


zabbix_service = ZabbixService()
