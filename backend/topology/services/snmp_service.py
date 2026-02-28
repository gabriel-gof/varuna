"""
Servico SNMP para comunicacao com OLTs.
"""

import asyncio
import logging
import threading
import time
from functools import partial
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SNMPService:
    """
    Service for executing SNMP operations.
    """

    def __init__(self):
        self.timeout = 2.0
        self.retries = 1
        self.error_log_throttle_seconds = 30.0
        self._puresnmp = None
        self._error_log_lock = threading.Lock()
        self._last_error_log_at: Dict[str, float] = {}

    def _build_error_key(self, op: str, olt: Any, reason: str) -> str:
        olt_id = getattr(olt, "id", None)
        identifier = (
            f"id:{olt_id}"
            if olt_id is not None
            else f"name:{getattr(olt, 'name', '<unknown>')}"
        )
        normalized_reason = str(reason or "").strip()[:160]
        return f"{op}:{identifier}:{normalized_reason}"

    def _log_error_throttled(self, level: str, key: str, message: str, *args) -> None:
        throttle_seconds = max(float(self.error_log_throttle_seconds or 0.0), 0.0)
        should_log = True
        if throttle_seconds > 0:
            now = time.monotonic()
            with self._error_log_lock:
                last = self._last_error_log_at.get(key)
                if last is not None and (now - last) < throttle_seconds:
                    should_log = False
                else:
                    self._last_error_log_at[key] = now
                    # Bound the in-memory map so long-lived processes do not accumulate stale keys.
                    if len(self._last_error_log_at) > 4096:
                        cutoff = now - (throttle_seconds * 2)
                        self._last_error_log_at = {
                            error_key: ts
                            for error_key, ts in self._last_error_log_at.items()
                            if ts >= cutoff
                        }
        if should_log:
            getattr(logger, level)(message, *args)
        else:
            logger.debug(message, *args)

    @property
    def puresnmp_modules(self):
        """
        Lazy load puresnmp modules to avoid import-time side effects.
        """
        if self._puresnmp is None:
            try:
                from puresnmp import Client, ObjectIdentifier
                from puresnmp.credentials import V1, V2C
                from puresnmp.exc import SnmpError, Timeout
                from puresnmp.transport import send_udp

                self._puresnmp = {
                    "Client": Client,
                    "ObjectIdentifier": ObjectIdentifier,
                    "V1": V1,
                    "V2C": V2C,
                    "SnmpError": SnmpError,
                    "Timeout": Timeout,
                    "send_udp": send_udp,
                }
            except ImportError:
                logger.error(
                    "Failed to import puresnmp modules. Please verify puresnmp version."
                )
                raise
        return self._puresnmp

    def _run(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _build_credentials(self, olt: Any):
        snmp_version = str(getattr(olt, "snmp_version", "v2c")).lower()
        community = getattr(olt, "snmp_community", "")
        if snmp_version == "v2c":
            return self.puresnmp_modules["V2C"](community)
        if snmp_version == "v1":
            return self.puresnmp_modules["V1"](community)

        # SNMP v3 needs auth/priv fields that are not yet represented in OLT model.
        logger.error(
            "SNMP v3 requested for OLT %s but credentials are not configured in model fields.",
            getattr(olt, "name", "<unknown>"),
        )
        return None

    def _build_client(self, olt: Any, *, timeout: float, retries: int):
        credentials = self._build_credentials(olt)
        if credentials is None:
            return None

        # puresnmp send_udp uses a total-attempt counter; map retries=0 to one try.
        sender_retries = max(int(retries), 0) + 1
        sender = partial(
            self.puresnmp_modules["send_udp"],
            timeout=max(float(timeout), 0.1),
            retries=sender_retries,
        )
        return self.puresnmp_modules["Client"](
            str(getattr(olt, "ip_address", "")),
            credentials,
            port=int(getattr(olt, "snmp_port", 161) or 161),
            sender=sender,
        )

    @staticmethod
    def _operation_timeout_guard(
        timeout: float,
        retries: int,
        *,
        slack_seconds: float = 1.0,
        minimum_seconds: float = 1.0,
    ) -> float:
        attempts = max(int(retries), 0) + 1
        request_timeout = max(float(timeout), 0.1)
        return max(float(minimum_seconds), (request_timeout * attempts) + float(slack_seconds))

    def get(
        self,
        olt: Any,
        oids: List[str],
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute SNMP GET for multiple OIDs.
        """
        if not oids:
            return None

        timeout_value = self.timeout if timeout is None else float(timeout)
        retries_value = self.retries if retries is None else int(retries)

        async def _get():
            client = self._build_client(
                olt,
                timeout=timeout_value,
                retries=retries_value,
            )
            if client is None:
                return None

            m = self.puresnmp_modules
            try:
                parsed_oids = [m["ObjectIdentifier"](oid) for oid in oids]
                operation_timeout = self._operation_timeout_guard(
                    timeout_value,
                    retries_value,
                    slack_seconds=2.0,
                    minimum_seconds=1.0,
                )
                values = await asyncio.wait_for(
                    client.multiget(parsed_oids),
                    timeout=operation_timeout,
                )
                if len(values) != len(oids):
                    self._log_error_throttled(
                        "warning",
                        self._build_error_key(
                            "get_partial",
                            olt,
                            f"expected={len(oids)} got={len(values)}",
                        ),
                        "SNMP GET partial response em %s: expected=%s got=%s",
                        getattr(olt, "name", "<unknown>"),
                        len(oids),
                        len(values),
                    )

                results: Dict[str, Any] = {}
                for oid, value in zip(oids, values):
                    results[oid] = self._parse_value(value)
                return results
            except asyncio.TimeoutError:
                self._log_error_throttled(
                    "warning",
                    self._build_error_key("get_timeout", olt, "operation_guard_timeout"),
                    "SNMP GET timeout guard reached em %s.",
                    getattr(olt, "name", "<unknown>"),
                )
                return None
            except m["Timeout"] as exc:
                self._log_error_throttled(
                    "warning",
                    self._build_error_key("get_timeout", olt, str(exc)),
                    "SNMP GET timeout em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )
                return None
            except m["SnmpError"] as exc:
                self._log_error_throttled(
                    "warning",
                    self._build_error_key("get", olt, str(exc)),
                    "SNMP GET error em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )
                return None
            except Exception as exc:
                self._log_error_throttled(
                    "error",
                    self._build_error_key("get_exception", olt, str(exc)),
                    "SNMP GET exception em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )
                return None

        return self._run(_get())

    def walk(
        self,
        olt: Any,
        oid: str,
        *,
        max_walk_rows: int = 20000,
        timeout: float = 30.0,
        retries: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Execute SNMP WALK for an OID.
        """
        base_oid = oid.rstrip(".")
        results: List[Dict[str, Any]] = []

        async def _walk():
            client = self._build_client(
                olt,
                timeout=timeout,
                retries=retries,
            )
            if client is None:
                return results

            m = self.puresnmp_modules
            try:
                parsed_base_oid = m["ObjectIdentifier"](base_oid)
                snmp_version = str(getattr(olt, "snmp_version", "v2c")).lower()
                if snmp_version == "v1":
                    walker = client.walk(parsed_base_oid, errors="warn")
                else:
                    walker = client.bulkwalk([parsed_base_oid], bulk_size=25)

                operation_timeout = self._operation_timeout_guard(
                    timeout,
                    retries,
                    slack_seconds=5.0,
                    minimum_seconds=5.0,
                )
                walker_iter = walker.__aiter__()

                while len(results) < max_walk_rows:
                    try:
                        varbind = await asyncio.wait_for(
                            walker_iter.__anext__(),
                            timeout=operation_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        self._log_error_throttled(
                            "warning",
                            self._build_error_key("walk_timeout", olt, "operation_guard_timeout"),
                            "SNMP WALK timeout guard reached em %s.",
                            getattr(olt, "name", "<unknown>"),
                        )
                        break

                    bind_oid = getattr(varbind, "oid", None)
                    bind_value = getattr(varbind, "value", None)
                    if bind_oid is None and isinstance(varbind, (tuple, list)) and len(varbind) >= 2:
                        bind_oid = varbind[0]
                        bind_value = varbind[1]

                    oid_str = str(bind_oid)
                    if not oid_str.startswith(f"{base_oid}."):
                        break

                    results.append(
                        {
                            "oid": oid_str,
                            "value": self._parse_value(bind_value),
                        }
                    )
                    if len(results) >= max_walk_rows:
                        logger.warning(
                            "SNMP WALK on %s hit max_walk_rows cap (%s); stopping walk for OID %s.",
                            getattr(olt, "name", "<unknown>"),
                            max_walk_rows,
                            base_oid,
                        )
                        break
            except m["Timeout"] as exc:
                self._log_error_throttled(
                    "warning",
                    self._build_error_key("walk_timeout", olt, str(exc)),
                    "SNMP WALK timeout em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )
            except m["SnmpError"] as exc:
                self._log_error_throttled(
                    "warning",
                    self._build_error_key("walk", olt, str(exc)),
                    "SNMP WALK error em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )
            except Exception as exc:
                self._log_error_throttled(
                    "error",
                    self._build_error_key("walk_exception", olt, str(exc)),
                    "SNMP WALK exception em %s: %s",
                    getattr(olt, "name", "<unknown>"),
                    exc,
                )

            return results

        return self._run(_walk())

    def _parse_value(self, val_obj: Any) -> Optional[Any]:
        """
        Parse SNMP value to application-friendly format.
        """
        if val_obj is None:
            return None

        value = val_obj
        if hasattr(val_obj, "pythonize"):
            try:
                value = val_obj.pythonize()
            except Exception:
                value = val_obj

        if isinstance(value, (bytes, bytearray)):
            raw = bytes(value).replace(b"\x00", b"").rstrip(b"\x00")
            if not raw:
                return ""
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return f"0x{raw.hex()}"

        return str(value).replace("\x00", "")


snmp_service = SNMPService()
