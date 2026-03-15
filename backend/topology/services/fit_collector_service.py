from __future__ import annotations

import base64
import logging
import re
import time
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from topology.models import ONU, OLT
from topology.services.vendor_profile import (
    COLLECTOR_TRANSPORT_HTTP,
    COLLECTOR_TRANSPORT_TELNET,
    get_collector_transport,
)

try:
    import telnetlib
except ImportError:  # pragma: no cover - telnetlib exists in Python 3.11 runtime.
    # Python 3.13+ removed telnetlib from stdlib; keep a patchable sentinel
    # object so unit tests can mock telnet behavior and runtime emits a clear
    # transport error when no telnet backend is available.
    telnetlib = SimpleNamespace(Telnet=None)


logger = logging.getLogger(__name__)

_PRIV_PROMPT_RE = re.compile(rb"(?:^|[\r\n])[^\n\r]*EPON#\s*$", re.IGNORECASE | re.MULTILINE)
_EXEC_PROMPT_RE = re.compile(rb"(?:^|[\r\n])[^\n\r]*EPON>\s*$", re.IGNORECASE | re.MULTILINE)
_PROMPT_LINE_RE = re.compile(r"^[^\n\r]*EPON[>#]\s*$", re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PAGER_RE = re.compile(rb"-+\s*Enter Key To Continue\s*-+", re.IGNORECASE)
_STATUS_ROW_RE = re.compile(
    r"^(?P<interface>\d+/\d+):(?P<onu_id>\d+)\s+"
    r"(?P<mac>[0-9a-f:]{17})\s+"
    r"(?P<state>Up|Down)\s+"
    r"(?P<firmware>\S+)\s+"
    r"(?P<chipid>\S+)\s+"
    r"(?P<ge>\d+)\s+"
    r"(?P<fe>\d+)\s+"
    r"(?P<pots>\d+)\s+"
    r"(?P<ctc_status>\S+)\s+"
    r"(?P<ctc_ver>\S+)\s+"
    r"(?P<activate>\S+)"
    r"(?:\s+(?P<uptime>(?:\d+D\s+)?\d+H\s+\d+M\s+\d+S))?"
    r"(?:\s+(?P<name>.*))?$",
    re.IGNORECASE,
)
_POWER_VALUE_RE = re.compile(r"^\s*RxPower\s*:\s*(?P<value>-?\d+(?:\.\d+)?)\s*dBm\s*$", re.IGNORECASE)
_OFFLINE_POWER_RE = re.compile(r"!\s*Onu\s+\d+\s+is\s+offline!", re.IGNORECASE)
_HTTP_AUTH_DENIED_RE = re.compile(r"Access Denied!\s*Please login\.", re.IGNORECASE)
_HTTP_ASP_ERROR_RE = re.compile(r"ASP Error:\s*Undefined procedure\s+getAllPonOnuTable", re.IGNORECASE)
_HTTP_JS_ARRAY_RE = re.compile(
    r"var\s+(?P<name>[A-Za-z0-9_]+)\s*=\s*new\s+Array\((?P<body>.*?)\);\s*",
    re.IGNORECASE | re.DOTALL,
)
_HTTP_JS_STRING_RE = re.compile(r"'((?:\\.|[^'])*)'")
_HTTP_ONUTABLE_WIDTH_RE = re.compile(r"lineNum=\(onutable\.length\)/(?P<width>\d+)")
_HTTP_ONU_KEY_RE = re.compile(r"^(?P<interface>\d+/\d+):(?P<onu_id>\d+)$")
_NAME_SENTINELS = {"NA", "N/A", "--", "-"}
_ACTIVE_TRUE_VALUES = {"1", "yes", "y", "true", "active", "authorized", "auth", "enable", "enabled", "on"}
_ACTIVE_FALSE_VALUES = {"0", "no", "n", "false", "inactive", "unauthorized", "unauth", "nauth", "disable", "disabled", "off"}


class FITCollectorError(RuntimeError):
    pass


class _FITTelnetSession:
    def __init__(self, olt: OLT, *, host: str | None = None, port: int | None = None):
        telnet_ctor = getattr(telnetlib, "Telnet", None)
        if telnet_ctor is None:
            raise FITCollectorError("telnetlib is unavailable in this Python runtime.")
        self.olt = olt
        self.host = str(host or olt.ip_address or "").strip()
        self.port = int(port or 23)
        self.username = str(getattr(olt, "telnet_username", "") or "")
        self.password = str(getattr(olt, "telnet_password", "") or "")
        self.connect_timeout = float(getattr(settings, "FIT_TELNET_TIMEOUT_SECONDS", 12) or 12)
        self.login_timeout = float(getattr(settings, "FIT_TELNET_LOGIN_TIMEOUT_SECONDS", 15) or 15)
        self.command_timeout = float(getattr(settings, "FIT_TELNET_COMMAND_TIMEOUT_SECONDS", 20) or 20)
        self.read_step = float(getattr(settings, "FIT_TELNET_READ_STEP_SECONDS", 0.1) or 0.1)
        self.char_delay = float(getattr(settings, "FIT_TELNET_CHAR_DELAY_SECONDS", 0.03) or 0.03)
        self.telnet = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def connect(self) -> None:
        if not self.host:
            raise FITCollectorError("FIT Telnet host is not configured.")
        try:
            self.telnet = telnetlib.Telnet(self.host, self.port, self.connect_timeout)
        except Exception as exc:  # pragma: no cover - network/runtime dependent.
            raise FITCollectorError(f"Telnet connection failed: {exc}") from exc
        self._login()

    def close(self) -> None:
        if self.telnet is None:
            return
        try:
            self.telnet.close()
        except Exception:
            logger.debug("FIT Telnet session close failed for OLT %s.", self.olt.id, exc_info=True)
        finally:
            self.telnet = None

    def _write_slow(self, value: str) -> None:
        if self.telnet is None:
            raise FITCollectorError("Telnet session is not connected.")
        for char in str(value):
            self.telnet.write(char.encode("utf-8", errors="ignore"))
            if self.char_delay > 0:
                time.sleep(self.char_delay)

    def _login(self) -> None:
        deadline = time.monotonic() + self.login_timeout
        buffer = b""
        sent_username = False
        sent_password = False
        sent_enable = False
        nudged = False

        while time.monotonic() < deadline:
            chunk = self.telnet.read_very_eager()
            if chunk:
                buffer += chunk
                lower = buffer.lower()
                if _PRIV_PROMPT_RE.search(buffer):
                    return
                if sent_password and not sent_enable and _EXEC_PROMPT_RE.search(buffer):
                    self._write_slow("enable")
                    self.telnet.write(b"\r")
                    sent_enable = True
                    buffer = b""
                    continue
                if not sent_username and (b"username:" in lower or b"login:" in lower):
                    self._write_slow(self.username)
                    self.telnet.write(b"\r")
                    sent_username = True
                    buffer = b""
                    continue
                if sent_username and not sent_password and b"password:" in lower:
                    self._write_slow(self.password)
                    self.telnet.write(b"\r")
                    sent_password = True
                    buffer = b""
                    continue
            else:
                if not nudged:
                    self.telnet.write(b"\r")
                    nudged = True
                time.sleep(self.read_step)

        raise FITCollectorError("Timed out waiting for FIT Telnet login prompt.")

    @staticmethod
    def _clean_output(raw: bytes, command: str) -> str:
        text = raw.decode("utf-8", errors="ignore").replace("\r", "")
        text = _ANSI_ESCAPE_RE.sub("", text)
        cleaned_lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if stripped == command:
                continue
            if stripped.endswith(f"# {command}"):
                continue
            if _PROMPT_LINE_RE.fullmatch(stripped):
                continue
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    def run_command(self, command: str, *, timeout: Optional[float] = None) -> str:
        if self.telnet is None:
            raise FITCollectorError("Telnet session is not connected.")
        command_timeout = float(timeout if timeout is not None else self.command_timeout)
        self._write_slow(command)
        self.telnet.write(b"\r")
        deadline = time.monotonic() + command_timeout
        buffer = b""

        while time.monotonic() < deadline:
            chunk = self.telnet.read_very_eager()
            if chunk:
                buffer += chunk
                if _PAGER_RE.search(buffer):
                    buffer = _PAGER_RE.sub(b"", buffer)
                    self.telnet.write(b" ")
                    continue
                if _PRIV_PROMPT_RE.search(buffer):
                    return self._clean_output(buffer, command)
            else:
                time.sleep(self.read_step)

        raise FITCollectorError(f"Timed out waiting for FIT command output: {command}")


class _FITHTTPSession:
    def __init__(self, olt: OLT, *, host: str | None = None):
        self.olt = olt
        self.host = str(host or olt.ip_address or "").strip()
        self.username = str(getattr(olt, "telnet_username", "") or "")
        self.password = str(getattr(olt, "telnet_password", "") or "")
        self.timeout = float(getattr(settings, "FIT_HTTP_TIMEOUT_SECONDS", 12) or 12)

    def _url(self, path: str) -> str:
        normalized_path = str(path or "").lstrip("/")
        return f"http://{self.host}/{normalized_path}"

    def get_page(self, path: str) -> str:
        if not self.host:
            raise FITCollectorError("FIT HTTP host is not configured.")
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        request = Request(
            self._url(path),
            headers={
                "Authorization": f"Basic {token}",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "Varuna-FIT-Collector/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # nosec B310 - controlled device URL
                page = response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) == 401:
                raise FITCollectorError("HTTP authentication failed.") from exc
            raise FITCollectorError(f"HTTP request failed: {exc}") from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise FITCollectorError(f"HTTP request failed: {reason}") from exc
        except Exception as exc:  # pragma: no cover - network/runtime dependent.
            raise FITCollectorError(f"HTTP request failed: {exc}") from exc

        if _HTTP_AUTH_DENIED_RE.search(page):
            raise FITCollectorError("HTTP authentication failed.")
        return page


class FITCollectorService:
    DEFAULT_INTERFACES = ("0/1", "0/2", "0/3", "0/4")

    @staticmethod
    def _collector_cfg(olt: OLT) -> Dict:
        templates = olt.vendor_profile.oid_templates if isinstance(olt.vendor_profile.oid_templates, dict) else {}
        return templates.get("collector", {}) if isinstance(templates.get("collector", {}), dict) else {}

    def _interfaces(self, olt: OLT) -> List[str]:
        configured = self._collector_cfg(olt).get("interfaces")
        if isinstance(configured, list):
            interfaces = [str(value).strip() for value in configured if str(value).strip()]
            if interfaces:
                return interfaces
        return list(self.DEFAULT_INTERFACES)

    def interfaces_for_olt(self, olt: OLT) -> List[str]:
        return self._interfaces(olt)

    @staticmethod
    def _transport(olt: OLT) -> str:
        transport = str(get_collector_transport(olt) or "").strip().lower()
        if transport in {COLLECTOR_TRANSPORT_HTTP, COLLECTOR_TRANSPORT_TELNET}:
            return transport
        return COLLECTOR_TRANSPORT_TELNET

    @staticmethod
    def _configured_blades(olt: OLT) -> List[Dict[str, int | str]]:
        blades = olt.get_blades()
        if blades:
            return blades
        raise FITCollectorError(
            "FIT blade configuration is required. Configure at least one blade with explicit IP and Telnet port."
        )

    @staticmethod
    def _slot_error(slot_id: int, exc: Exception) -> FITCollectorError:
        message = str(exc or "").strip() or "Unknown FIT collector error."
        prefix = f"Slot {int(slot_id)}: "
        if message.startswith(prefix):
            return exc if isinstance(exc, FITCollectorError) else FITCollectorError(message)
        return FITCollectorError(f"{prefix}{message}")

    @staticmethod
    def _blade_error(blade_ip: str, exc: Exception) -> FITCollectorError:
        message = str(exc or "").strip() or "Unknown FIT collector error."
        prefix = f"Blade {blade_ip}: "
        if message.startswith(prefix):
            return exc if isinstance(exc, FITCollectorError) else FITCollectorError(message)
        return FITCollectorError(f"{prefix}{message}")

    @classmethod
    def _blade_for_slot(cls, blades: List[Dict[str, int | str]], slot_id: int) -> Dict[str, int | str]:
        blade_index = int(slot_id) - 1
        if blade_index < 0 or blade_index >= len(blades):
            raise cls._slot_error(
                slot_id,
                FITCollectorError("No configured FIT blade for this slot. Add a blade_ips entry for every populated slot."),
            )
        return blades[blade_index]

    @classmethod
    def _requested_slot_errors(
        cls,
        blades: List[Dict[str, int | str]],
        interfaces_by_slot: Optional[Dict[int, List[str]]],
    ) -> List[str]:
        if not interfaces_by_slot:
            return []

        errors: List[str] = []
        for raw_slot_id, raw_interfaces in interfaces_by_slot.items():
            slot_interfaces = [str(value).strip() for value in raw_interfaces or [] if str(value).strip()]
            if not slot_interfaces:
                continue
            try:
                slot_id = int(raw_slot_id)
            except (TypeError, ValueError):
                errors.append(str(cls._slot_error(0, FITCollectorError(f"Invalid FIT slot identifier {raw_slot_id!r}."))))
                continue
            if slot_id < 1 or slot_id > len(blades):
                errors.append(
                    str(
                        cls._slot_error(
                            slot_id,
                            FITCollectorError("No configured FIT blade for this slot. Add a blade_ips entry for every populated slot."),
                        )
                    )
                )
        return errors

    @staticmethod
    def _normalize_name(value: str) -> str:
        candidate = str(value or "").strip()
        if candidate.upper() in _NAME_SENTINELS:
            return ""
        return candidate

    @staticmethod
    def _normalize_mac(value: str) -> str:
        candidate = str(value or "").strip().replace("-", ":").upper()
        if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", candidate):
            return candidate
        return ""

    @staticmethod
    def _is_authorized_activate(value: str) -> bool:
        token = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
        if not token:
            return True
        if token in _ACTIVE_TRUE_VALUES:
            return True
        if token in _ACTIVE_FALSE_VALUES:
            return False
        if token.startswith(("unauth", "nauth", "disable", "inactive")) or token == "no":
            return False
        if token.startswith(("auth", "yes", "enable", "active")):
            return True
        logger.debug("FIT activate token %r not recognized; treating ONU as authorized.", value)
        return True

    @staticmethod
    def _is_http_authorized_activate(value: str) -> bool:
        token = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
        if not token:
            return True
        if token in {"2", "deactivate", "inactive", "unauth", "nauth", "disable", "disabled"}:
            return False
        return True

    @staticmethod
    def _parse_http_float(value: str) -> Optional[float]:
        token = str(value or "").strip().lower()
        if not token or token in {"--", "-inf", "nan", "na", "n/a"}:
            return None
        try:
            return float(token)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_http_js_array(cls, page: str, name: str) -> List[str]:
        for match in _HTTP_JS_ARRAY_RE.finditer(str(page or "")):
            if match.group("name") != name:
                continue
            body = match.group("body") or ""
            return [
                value.replace("\\'", "'").replace("\\\\", "\\")
                for value in _HTTP_JS_STRING_RE.findall(body)
            ]
        return []

    @staticmethod
    def _extract_http_row_width(page: str, value_count: int) -> int:
        match = _HTTP_ONUTABLE_WIDTH_RE.search(str(page or ""))
        if match:
            try:
                width = int(match.group("width"))
            except (TypeError, ValueError):
                width = 0
            if width > 0:
                return width
        if value_count and value_count % 16 == 0:
            return 16
        return 11

    @staticmethod
    def _http_status_to_onu_status(value: str) -> str:
        token = str(value or "").strip().lower()
        if token == "up":
            return ONU.STATUS_ONLINE
        if not token:
            return ONU.STATUS_UNKNOWN
        return ONU.STATUS_OFFLINE

    @staticmethod
    def _interface_to_pon_id(interface: str) -> int:
        try:
            return int(str(interface).split("/")[-1])
        except (TypeError, ValueError, AttributeError, IndexError):
            raise FITCollectorError(f"Invalid FIT interface label: {interface}")

    def check_reachability(self, olt: OLT) -> tuple[bool, str]:
        if self._transport(olt) == COLLECTOR_TRANSPORT_HTTP:
            return self._check_reachability_http(olt)
        return self._check_reachability_telnet(olt)

    def _check_reachability_telnet(self, olt: OLT) -> tuple[bool, str]:
        try:
            blades = self._configured_blades(olt)
        except FITCollectorError as exc:
            return False, str(exc)
        errors: List[str] = []
        for blade in blades:
            try:
                with _FITTelnetSession(olt, host=blade["ip"], port=blade["port"]):
                    pass
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        if errors:
            return False, "; ".join(errors)
        return True, "Telnet login succeeded."

    def _check_reachability_http(self, olt: OLT) -> tuple[bool, str]:
        try:
            blades = self._configured_blades(olt)
        except FITCollectorError as exc:
            return False, str(exc)
        interfaces = self._interfaces(olt)
        probe_path = self._http_overview_path(interfaces[0]) if interfaces else "top.asp"
        errors: List[str] = []
        for blade in blades:
            try:
                session = _FITHTTPSession(olt, host=blade["ip"])
                session.get_page(probe_path)
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        if errors:
            return False, "; ".join(errors)
        return True, "HTTP UI request succeeded."

    @classmethod
    def parse_status_output(cls, raw_output: str, *, slot_id: int = 1) -> List[Dict]:
        rows: List[Dict] = []
        for raw_line in str(raw_output or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("OnuId") or line.startswith("==="):
                continue
            match = _STATUS_ROW_RE.match(line)
            if not match:
                continue
            interface = str(match.group("interface")).strip()
            onu_id = int(match.group("onu_id"))
            pon_id = cls._interface_to_pon_id(interface)
            state = str(match.group("state") or "").strip().lower()
            activate = str(match.group("activate") or "").strip()
            # Keep discovery scoped to authorized ONUs only.
            if not cls._is_authorized_activate(activate):
                continue
            rows.append(
                {
                    "slot_id": slot_id,
                    "pon_id": pon_id,
                    "onu_id": onu_id,
                    "interface": interface,
                    "status": ONU.STATUS_ONLINE if state == "up" else ONU.STATUS_OFFLINE,
                    "name": cls._normalize_name(match.group("name")),
                    "mac": cls._normalize_mac(match.group("mac")),
                }
            )
        return rows

    @classmethod
    def parse_http_status_page(cls, page: str, *, slot_id: int = 1) -> List[Dict]:
        values = cls._extract_http_js_array(page, "onutable")
        if not values:
            return []

        row_width = cls._extract_http_row_width(page, len(values))
        rows: List[Dict] = []
        for index in range(0, len(values), row_width):
            row = values[index:index + row_width]
            if len(row) < row_width:
                continue
            match = _HTTP_ONU_KEY_RE.fullmatch(str(row[0]).strip())
            if not match:
                continue
            if not cls._is_http_authorized_activate(row[9] if row_width > 9 else ""):
                continue
            interface = str(match.group("interface")).strip()
            rows.append(
                {
                    "slot_id": slot_id,
                    "pon_id": cls._interface_to_pon_id(interface),
                    "onu_id": int(match.group("onu_id")),
                    "interface": interface,
                    "status": cls._http_status_to_onu_status(row[3] if row_width > 3 else ""),
                    "name": cls._normalize_name(row[1] if row_width > 1 else ""),
                    "mac": cls._normalize_mac(row[2] if row_width > 2 else ""),
                    "onu_rx_power": cls._parse_http_float(row[15] if row_width > 15 else ""),
                }
            )
        return rows

    @classmethod
    def parse_http_detail_page(cls, page: str) -> Dict:
        info = cls._extract_http_js_array(page, "onuinfo")
        if not info:
            raise FITCollectorError("FIT HTTP detail page did not expose onuinfo.")
        match = _HTTP_ONU_KEY_RE.fullmatch(str(info[0]).strip())
        if not match:
            raise FITCollectorError("FIT HTTP detail page did not expose a valid ONU identifier.")

        opm = cls._extract_http_js_array(page, "onuOpmInfo")
        interface = str(match.group("interface")).strip()
        return {
            "interface": interface,
            "pon_id": cls._interface_to_pon_id(interface),
            "onu_id": int(match.group("onu_id")),
            "name": cls._normalize_name(info[1] if len(info) > 1 else ""),
            "mac": cls._normalize_mac(info[2] if len(info) > 2 else ""),
            "status": cls._http_status_to_onu_status(info[3] if len(info) > 3 else ""),
            "first_up_time": str(info[7]).strip() if len(info) > 7 else "",
            "last_up_time": str(info[8]).strip() if len(info) > 8 else "",
            "last_off_time": str(info[9]).strip() if len(info) > 9 else "",
            "temperature_c": cls._parse_http_float(opm[1] if len(opm) > 1 else ""),
            "voltage_v": cls._parse_http_float(opm[2] if len(opm) > 2 else ""),
            "bias_current_ma": cls._parse_http_float(opm[3] if len(opm) > 3 else ""),
            "tx_power_dbm": cls._parse_http_float(opm[4] if len(opm) > 4 else ""),
            "onu_rx_power": cls._parse_http_float(opm[5] if len(opm) > 5 else ""),
        }

    @staticmethod
    def parse_power_output(raw_output: str) -> Optional[float]:
        output = str(raw_output or "")
        if _OFFLINE_POWER_RE.search(output):
            return None
        for raw_line in output.splitlines():
            match = _POWER_VALUE_RE.match(raw_line)
            if not match:
                continue
            try:
                return float(match.group("value"))
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _http_overview_path(interface: str) -> str:
        normalized_interface = str(interface or "").strip()
        return f"onuOverview.asp?oltponno={quote(normalized_interface, safe='/')}"

    @staticmethod
    def _http_all_onu_path() -> str:
        return "onuAllPonOnuList.asp"

    @staticmethod
    def _http_detail_path(interface: str, onu_id: int) -> str:
        normalized_interface = str(interface or "").strip()
        onu_key = f"{normalized_interface}:{int(onu_id)}"
        return (
            f"onuConfig.asp?onuno={quote(onu_key, safe='/:')}"
            f"&oltponno={quote(normalized_interface, safe='/')}"
        )

    @staticmethod
    def _build_power_payload(onu: ONU, *, onu_rx_power: Optional[float], power_read_at: Optional[str]) -> Dict:
        return {
            "onu_id": int(onu.id),
            "slot_id": int(onu.slot_id or 0),
            "pon_id": int(onu.pon_id or 0),
            "onu_number": int(onu.onu_id or 0),
            "onu_rx_power": onu_rx_power,
            "olt_rx_power": None,
            "power_read_at": power_read_at,
        }

    @classmethod
    def _parse_http_all_status_page(cls, page: str, *, slot_id: int = 1) -> Optional[List[Dict]]:
        raw_page = str(page or "")
        if _HTTP_ASP_ERROR_RE.search(raw_page):
            return None
        return cls.parse_http_status_page(raw_page, slot_id=slot_id)

    def fetch_status_inventory(self, olt: OLT) -> List[Dict]:
        return self.fetch_status_inventory_for_interfaces(olt)

    def fetch_status_inventory_for_interfaces(
        self,
        olt: OLT,
        *,
        interfaces_by_slot: Optional[Dict[int, List[str]]] = None,
    ) -> List[Dict]:
        if self._transport(olt) == COLLECTOR_TRANSPORT_HTTP:
            return self._fetch_status_inventory_for_interfaces_http(
                olt,
                interfaces_by_slot=interfaces_by_slot,
            )
        return self._fetch_status_inventory_for_interfaces_telnet(
            olt,
            interfaces_by_slot=interfaces_by_slot,
        )

    def _fetch_status_inventory_for_interfaces_telnet(
        self,
        olt: OLT,
        *,
        interfaces_by_slot: Optional[Dict[int, List[str]]] = None,
    ) -> List[Dict]:
        rows: List[Dict] = []
        blades = self._configured_blades(olt)
        default_interfaces = self._interfaces(olt)
        errors: List[str] = self._requested_slot_errors(blades, interfaces_by_slot)
        for blade_index, blade in enumerate(blades):
            slot_id = blade_index + 1
            if interfaces_by_slot is not None:
                slot_interfaces = interfaces_by_slot.get(slot_id) or []
                slot_interfaces = [str(value).strip() for value in slot_interfaces if str(value).strip()]
                if not slot_interfaces:
                    continue
            else:
                slot_interfaces = default_interfaces
            try:
                with _FITTelnetSession(olt, host=blade["ip"], port=blade["port"]) as session:
                    for interface in slot_interfaces:
                        output = session.run_command(f"show onu info epon {interface} all")
                        rows.extend(self.parse_status_output(output, slot_id=slot_id))
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        rows.sort(key=lambda row: (int(row["slot_id"]), int(row["pon_id"]), int(row["onu_id"])))
        if errors:
            raise FITCollectorError("; ".join(errors))
        return rows

    def _fetch_status_inventory_for_interfaces_http(
        self,
        olt: OLT,
        *,
        interfaces_by_slot: Optional[Dict[int, List[str]]] = None,
    ) -> List[Dict]:
        rows: List[Dict] = []
        blades = self._configured_blades(olt)
        default_interfaces = self._interfaces(olt)
        errors: List[str] = self._requested_slot_errors(blades, interfaces_by_slot)
        for blade_index, blade in enumerate(blades):
            slot_id = blade_index + 1
            if interfaces_by_slot is not None:
                slot_interfaces = interfaces_by_slot.get(slot_id) or []
                slot_interfaces = [str(value).strip() for value in slot_interfaces if str(value).strip()]
                if not slot_interfaces:
                    continue
            else:
                slot_interfaces = default_interfaces
            try:
                session = _FITHTTPSession(olt, host=blade["ip"])
                all_rows = self._parse_http_all_status_page(
                    session.get_page(self._http_all_onu_path()),
                    slot_id=slot_id,
                )
                if all_rows:
                    allowed_interfaces = set(slot_interfaces)
                    rows.extend(
                        row for row in all_rows
                        if row.get("interface") in allowed_interfaces
                    )
                    continue
                for interface in slot_interfaces:
                    page = session.get_page(self._http_overview_path(interface))
                    rows.extend(self.parse_http_status_page(page, slot_id=slot_id))
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        rows.sort(key=lambda row: (int(row["slot_id"]), int(row["pon_id"]), int(row["onu_id"])))
        if errors:
            raise FITCollectorError("; ".join(errors))
        return rows

    def fetch_power_for_onus(self, olt: OLT, onus: Iterable[ONU]) -> Dict[int, Dict]:
        if self._transport(olt) == COLLECTOR_TRANSPORT_HTTP:
            return self._fetch_power_for_onus_http(olt, onus)
        return self._fetch_power_for_onus_telnet(olt, onus)

    def _fetch_power_for_onus_telnet(self, olt: OLT, onus: Iterable[ONU]) -> Dict[int, Dict]:
        ordered_onus = sorted(
            [onu for onu in onus if onu and onu.is_active],
            key=lambda onu: (int(onu.slot_id or 0), int(onu.pon_id or 0), int(onu.onu_id or 0)),
        )
        if not ordered_onus:
            return {}

        blades = self._configured_blades(olt)
        onus_by_slot: Dict[int, List[ONU]] = {}
        for onu in ordered_onus:
            onus_by_slot.setdefault(int(onu.slot_id or 1), []).append(onu)

        read_at = timezone.now().isoformat()
        results: Dict[int, Dict] = {}
        errors: List[str] = []
        for slot_id, slot_onus in onus_by_slot.items():
            try:
                blade = self._blade_for_slot(blades, slot_id)
            except FITCollectorError as exc:
                errors.append(str(exc))
                continue
            try:
                with _FITTelnetSession(olt, host=blade["ip"], port=blade["port"]) as session:
                    for onu in slot_onus:
                        interface = f"0/{int(onu.pon_id)}"
                        output = session.run_command(
                            f"show onu optical-ddm epon {interface} {int(onu.onu_id)}",
                            timeout=max(float(getattr(settings, "FIT_TELNET_POWER_TIMEOUT_SECONDS", 10) or 10), 1.0),
                        )
                        onu_rx_power = self.parse_power_output(output)
                        results[int(onu.id)] = self._build_power_payload(
                            onu,
                            onu_rx_power=onu_rx_power,
                            power_read_at=read_at if onu_rx_power is not None else None,
                        )
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        if errors:
            logger.warning("FIT Telnet power collection partial failure for OLT %s: %s", olt.id, "; ".join(errors))
            if not results:
                raise FITCollectorError("; ".join(errors))
        return results

    def _fetch_power_for_onus_http(self, olt: OLT, onus: Iterable[ONU]) -> Dict[int, Dict]:
        ordered_onus = sorted(
            [onu for onu in onus if onu and onu.is_active],
            key=lambda onu: (int(onu.slot_id or 0), int(onu.pon_id or 0), int(onu.onu_id or 0)),
        )
        if not ordered_onus:
            return {}

        blades = self._configured_blades(olt)
        onus_by_slot: Dict[int, List[ONU]] = {}
        for onu in ordered_onus:
            onus_by_slot.setdefault(int(onu.slot_id or 1), []).append(onu)

        read_at = timezone.now().isoformat()
        results: Dict[int, Dict] = {}
        errors: List[str] = []
        for slot_id, slot_onus in onus_by_slot.items():
            try:
                blade = self._blade_for_slot(blades, slot_id)
            except FITCollectorError as exc:
                errors.append(str(exc))
                continue
            try:
                session = _FITHTTPSession(olt, host=blade["ip"])
                all_rows = self._parse_http_all_status_page(
                    session.get_page(self._http_all_onu_path()),
                    slot_id=slot_id,
                )
                all_rows_by_key = {}
                if all_rows:
                    all_rows_by_key = {
                        (int(row["pon_id"]), int(row["onu_id"])): row
                        for row in all_rows
                    }

                onus_by_interface: Dict[str, List[ONU]] = {}
                for onu in slot_onus:
                    interface = f"0/{int(onu.pon_id)}"
                    onus_by_interface.setdefault(interface, []).append(onu)

                for interface, interface_onus in onus_by_interface.items():
                    if all_rows_by_key:
                        pending_onus: List[ONU] = []
                        for onu in interface_onus:
                            overview_row = all_rows_by_key.get((int(onu.pon_id or 0), int(onu.onu_id or 0)))
                            if overview_row is None:
                                pending_onus.append(onu)
                                continue
                            if str(overview_row.get("status") or "").strip().lower() != ONU.STATUS_ONLINE:
                                results[int(onu.id)] = self._build_power_payload(
                                    onu,
                                    onu_rx_power=None,
                                    power_read_at=None,
                                )
                                continue
                            inline_power = overview_row.get("onu_rx_power")
                            if inline_power is not None:
                                results[int(onu.id)] = self._build_power_payload(
                                    onu,
                                    onu_rx_power=inline_power,
                                    power_read_at=read_at,
                                )
                                continue
                            pending_onus.append(onu)
                        if not pending_onus:
                            continue
                        interface_onus = pending_onus

                    overview_page = session.get_page(self._http_overview_path(interface))
                    overview_rows = self.parse_http_status_page(overview_page, slot_id=slot_id)
                    overview_by_onu = {
                        int(row["onu_id"]): row
                        for row in overview_rows
                        if int(row.get("pon_id") or 0) == int(interface_onus[0].pon_id or 0)
                    }

                    for onu in interface_onus:
                        overview_row = overview_by_onu.get(int(onu.onu_id or 0))
                        if overview_row and str(overview_row.get("status") or "").strip().lower() != ONU.STATUS_ONLINE:
                            results[int(onu.id)] = self._build_power_payload(
                                onu,
                                onu_rx_power=None,
                                power_read_at=None,
                            )
                            continue
                        inline_power = overview_row.get("onu_rx_power") if overview_row else None
                        if inline_power is not None:
                            results[int(onu.id)] = self._build_power_payload(
                                onu,
                                onu_rx_power=inline_power,
                                power_read_at=read_at,
                            )
                            continue

                        detail_page = session.get_page(self._http_detail_path(interface, int(onu.onu_id or 0)))
                        detail = self.parse_http_detail_page(detail_page)
                        detail_power = detail.get("onu_rx_power")
                        detail_status = str(detail.get("status") or "").strip().lower()
                        results[int(onu.id)] = self._build_power_payload(
                            onu,
                            onu_rx_power=detail_power if detail_status == ONU.STATUS_ONLINE else None,
                            power_read_at=read_at if detail_status == ONU.STATUS_ONLINE and detail_power is not None else None,
                        )
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade["ip"], exc)))
        if errors:
            logger.warning("FIT HTTP power collection partial failure for OLT %s: %s", olt.id, "; ".join(errors))
            if not results:
                raise FITCollectorError("; ".join(errors))
        return results


fit_collector_service = FITCollectorService()
