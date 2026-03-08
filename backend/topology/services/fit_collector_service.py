from __future__ import annotations

import logging
import re
import time
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

from django.conf import settings
from django.utils import timezone

from topology.models import ONU, OLT

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
_NAME_SENTINELS = {"NA", "N/A", "--", "-"}
_ACTIVE_TRUE_VALUES = {"1", "yes", "y", "true", "active", "authorized", "auth", "enable", "enabled", "on"}
_ACTIVE_FALSE_VALUES = {"0", "no", "n", "false", "inactive", "unauthorized", "unauth", "nauth", "disable", "disabled", "off"}


class FITCollectorError(RuntimeError):
    pass


class _FITTelnetSession:
    def __init__(self, olt: OLT, *, host: str | None = None):
        telnet_ctor = getattr(telnetlib, "Telnet", None)
        if telnet_ctor is None:
            raise FITCollectorError("telnetlib is unavailable in this Python runtime.")
        self.olt = olt
        self.host = str(host or olt.ip_address or "").strip()
        self.port = int(getattr(olt, "telnet_port", 23) or 23)
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
    def _blade_error(blade_ip: str, exc: Exception) -> FITCollectorError:
        message = str(exc or "").strip() or "Unknown FIT collector error."
        prefix = f"Blade {blade_ip}: "
        if message.startswith(prefix):
            return exc if isinstance(exc, FITCollectorError) else FITCollectorError(message)
        return FITCollectorError(f"{prefix}{message}")

    @staticmethod
    def _normalize_name(value: str) -> str:
        candidate = str(value or "").strip()
        if candidate.upper() in _NAME_SENTINELS:
            return ""
        return candidate

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
    def _interface_to_pon_id(interface: str) -> int:
        try:
            return int(str(interface).split("/")[-1])
        except (TypeError, ValueError, AttributeError, IndexError):
            raise FITCollectorError(f"Invalid FIT interface label: {interface}")

    def check_reachability(self, olt: OLT) -> tuple[bool, str]:
        blade_ips = olt.get_blade_ips()
        errors: List[str] = []
        for blade_ip in blade_ips:
            try:
                with _FITTelnetSession(olt, host=blade_ip):
                    pass
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade_ip, exc)))
        if errors:
            return False, "; ".join(errors)
        return True, "Telnet login succeeded."

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
                }
            )
        return rows

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

    def fetch_status_inventory(self, olt: OLT) -> List[Dict]:
        return self.fetch_status_inventory_for_interfaces(olt)

    def fetch_status_inventory_for_interfaces(
        self,
        olt: OLT,
        *,
        interfaces_by_slot: Optional[Dict[int, List[str]]] = None,
    ) -> List[Dict]:
        rows: List[Dict] = []
        blade_ips = olt.get_blade_ips()
        default_interfaces = self._interfaces(olt)
        errors: List[str] = []
        for blade_index, blade_ip in enumerate(blade_ips):
            slot_id = blade_index + 1
            if interfaces_by_slot is not None:
                slot_interfaces = interfaces_by_slot.get(slot_id) or []
                slot_interfaces = [str(value).strip() for value in slot_interfaces if str(value).strip()]
                if not slot_interfaces:
                    continue
            else:
                slot_interfaces = default_interfaces
            try:
                with _FITTelnetSession(olt, host=blade_ip) as session:
                    for interface in slot_interfaces:
                        output = session.run_command(f"show onu info epon {interface} all")
                        rows.extend(self.parse_status_output(output, slot_id=slot_id))
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade_ip, exc)))
        rows.sort(key=lambda row: (int(row["slot_id"]), int(row["pon_id"]), int(row["onu_id"])))
        if errors:
            raise FITCollectorError("; ".join(errors))
        return rows

    def fetch_power_for_onus(self, olt: OLT, onus: Iterable[ONU]) -> Dict[int, Dict]:
        ordered_onus = sorted(
            [onu for onu in onus if onu and onu.is_active],
            key=lambda onu: (int(onu.slot_id or 0), int(onu.pon_id or 0), int(onu.onu_id or 0)),
        )
        if not ordered_onus:
            return {}

        blade_ips = olt.get_blade_ips()
        onus_by_slot: Dict[int, List[ONU]] = {}
        for onu in ordered_onus:
            onus_by_slot.setdefault(int(onu.slot_id or 1), []).append(onu)

        read_at = timezone.now().isoformat()
        results: Dict[int, Dict] = {}
        errors: List[str] = []
        for slot_id, slot_onus in onus_by_slot.items():
            blade_index = slot_id - 1
            blade_ip = blade_ips[blade_index] if blade_index < len(blade_ips) else blade_ips[0]
            try:
                with _FITTelnetSession(olt, host=blade_ip) as session:
                    for onu in slot_onus:
                        interface = f"0/{int(onu.pon_id)}"
                        output = session.run_command(
                            f"show onu optical-ddm epon {interface} {int(onu.onu_id)}",
                            timeout=max(float(getattr(settings, "FIT_TELNET_POWER_TIMEOUT_SECONDS", 10) or 10), 1.0),
                        )
                        onu_rx_power = self.parse_power_output(output)
                        results[int(onu.id)] = {
                            "onu_id": int(onu.id),
                            "slot_id": int(onu.slot_id or 0),
                            "pon_id": int(onu.pon_id or 0),
                            "onu_number": int(onu.onu_id or 0),
                            "onu_rx_power": onu_rx_power,
                            "olt_rx_power": None,
                            "power_read_at": read_at if onu_rx_power is not None else None,
                        }
            except FITCollectorError as exc:
                errors.append(str(self._blade_error(blade_ip, exc)))
        if errors:
            raise FITCollectorError("; ".join(errors))
        return results


fit_collector_service = FITCollectorService()
