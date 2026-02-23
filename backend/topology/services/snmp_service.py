"""
Serviço SNMP para comunicação com OLTs
SNMP Service for OLT communication
"""
import logging
import asyncio
import threading
import time
from typing import List, Dict, Any, Optional

# Use explicit imports inside methods to avoid asyncio loop conflicts with mod_wsgi
# during module initialization

logger = logging.getLogger(__name__)

class SNMPService:
    """
    Serviço para executar operações SNMP
    Service for executing SNMP operations
    """
    
    def __init__(self):
        self.timeout = 2.0
        self.retries = 1
        self.error_log_throttle_seconds = 30.0
        self._pysnmp = None
        self._error_log_lock = threading.Lock()
        self._last_error_log_at: Dict[str, float] = {}

    def _build_error_key(self, op: str, olt: Any, reason: str) -> str:
        olt_id = getattr(olt, 'id', None)
        identifier = f"id:{olt_id}" if olt_id is not None else f"name:{getattr(olt, 'name', '<unknown>')}"
        normalized_reason = str(reason or '').strip()[:160]
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
    def pysnmp_modules(self):
        """
        Lazy load pysnmp modules to avoid import-time side effects.
        Returns a dictionary or object containing the necessary symbols.
        """
        if self._pysnmp is None:
            try:
                from pysnmp.hlapi.asyncio import (
                    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
                    ObjectType, ObjectIdentity, getCmd, nextCmd, bulkCmd
                )
                self._pysnmp = {
                    'SnmpEngine': SnmpEngine,
                    'CommunityData': CommunityData,
                    'UdpTransportTarget': UdpTransportTarget,
                    'ContextData': ContextData,
                    'ObjectType': ObjectType,
                    'ObjectIdentity': ObjectIdentity,
                    'getCmd': getCmd,
                    'nextCmd': nextCmd,
                    'bulkCmd': bulkCmd,
                }
            except ImportError:
                # Try alternate names for newer pysnmp versions
                try:
                    from pysnmp.hlapi.asyncio import (
                        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
                        ObjectType, ObjectIdentity, get_cmd as getCmd, next_cmd as nextCmd,
                        bulk_cmd as bulkCmd
                    )
                    self._pysnmp = {
                        'SnmpEngine': SnmpEngine,
                        'CommunityData': CommunityData,
                        'UdpTransportTarget': UdpTransportTarget,
                        'ContextData': ContextData,
                        'ObjectType': ObjectType,
                        'ObjectIdentity': ObjectIdentity,
                        'getCmd': getCmd,
                        'nextCmd': nextCmd,
                        'bulkCmd': bulkCmd,
                    }
                except ImportError:
                    logger.error("Failed to import pysnmp.hlapi.asyncio. Please verify pysnmp version.")
                    raise
        return self._pysnmp

    @property
    def engine(self):
        # pysnmp asyncio engine is event-loop bound; create per request
        # to avoid cross-loop deadlocks when sync wrappers spawn loops.
        return self.pysnmp_modules['SnmpEngine']()

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

    def _build_auth_data(self, olt: Any):
        snmp_version = str(getattr(olt, 'snmp_version', 'v2c')).lower()
        if snmp_version == 'v2c':
            return self.pysnmp_modules['CommunityData'](olt.snmp_community, mpModel=1)
        if snmp_version == 'v1':
            return self.pysnmp_modules['CommunityData'](olt.snmp_community, mpModel=0)

        # SNMP v3 needs auth/priv fields that are not yet represented in OLT model.
        logger.error(
            "SNMP v3 requested for OLT %s but credentials are not configured in model fields.",
            getattr(olt, 'name', '<unknown>'),
        )
        return None
    
    def get(
        self,
        olt: Any,
        oids: List[str],
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Executa SNMP GET para múltiplas OIDs
        Executes SNMP GET for multiple OIDs
        """
        if not oids:
            return None

        m = self.pysnmp_modules
        var_binds = [m['ObjectType'](m['ObjectIdentity'](oid)) for oid in oids]
        timeout_value = self.timeout if timeout is None else float(timeout)
        retries_value = self.retries if retries is None else int(retries)

        auth_data = self._build_auth_data(olt)
        if auth_data is None:
            return None

        async def _get():
            engine = self.engine
            try:
                # pysnmp 7.x requires using the create() factory method for UdpTransportTarget
                transport = await m['UdpTransportTarget'].create(
                    (olt.ip_address, olt.snmp_port),
                    timeout=timeout_value,
                    retries=retries_value
                )

                errorIndication, errorStatus, errorIndex, varBinds = await m['getCmd'](
                    engine,
                    auth_data,
                    transport,
                    m['ContextData'](),
                    *var_binds
                )

                if errorIndication or errorStatus:
                    reason = errorIndication or errorStatus.prettyPrint()
                    self._log_error_throttled(
                        "warning",
                        self._build_error_key("get", olt, reason),
                        "SNMP GET error em %s: %s",
                        getattr(olt, 'name', '<unknown>'),
                        reason,
                    )
                    return None

                results = {}
                for varBind in varBinds:
                    oid_str = str(varBind[0])
                    val_obj = varBind[1]
                    results[oid_str] = self._parse_value(val_obj)

                return results
            except Exception as e:
                self._log_error_throttled(
                    "error",
                    self._build_error_key("get_exception", olt, str(e)),
                    "SNMP GET exception em %s: %s",
                    getattr(olt, 'name', '<unknown>'),
                    e,
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
        Executa SNMP WALK para uma OID
        Executes SNMP WALK for an OID

        Walk uses a generous per-request timeout (default 30s, retries=0)
        instead of the short GET timeout, because walks involve many
        sequential bulk requests and slow OLTs may need several seconds
        per bulk batch.
        """
        base_oid = oid.rstrip(".")
        results = []
        m = self.pysnmp_modules
        auth_data = self._build_auth_data(olt)
        if auth_data is None:
            return results

        walk_timeout = float(timeout)
        walk_retries = int(retries)

        async def _walk():
            engine = self.engine
            # pysnmp 7.x requires using the create() factory method for UdpTransportTarget
            transport = await m['UdpTransportTarget'].create(
                (olt.ip_address, olt.snmp_port),
                timeout=walk_timeout,
                retries=walk_retries
            )

            current_oid = base_oid
            bulk_cmd = m.get('bulkCmd')
            max_repetitions = 25
            while True:
                try:
                    if bulk_cmd:
                        errorIndication, errorStatus, errorIndex, varBinds = await bulk_cmd(
                            engine,
                            auth_data,
                            transport,
                            m['ContextData'](),
                            0,
                            max_repetitions,
                            m['ObjectType'](m['ObjectIdentity'](current_oid)),
                            lexicographicMode=False
                        )
                    else:
                        errorIndication, errorStatus, errorIndex, varBinds = await m['nextCmd'](
                            engine,
                            auth_data,
                            transport,
                            m['ContextData'](),
                            m['ObjectType'](m['ObjectIdentity'](current_oid)),
                            lexicographicMode=False
                        )
                except Exception as e:
                    self._log_error_throttled(
                        "error",
                        self._build_error_key("walk_exception", olt, str(e)),
                        "SNMP WALK exception em %s: %s",
                        getattr(olt, 'name', '<unknown>'),
                        e,
                    )
                    break

                if errorIndication:
                    self._log_error_throttled(
                        "warning",
                        self._build_error_key("walk", olt, str(errorIndication)),
                        "SNMP WALK error em %s: %s",
                        getattr(olt, 'name', '<unknown>'),
                        errorIndication,
                    )
                    break
                elif errorStatus:
                    reason = errorStatus.prettyPrint()
                    self._log_error_throttled(
                        "warning",
                        self._build_error_key("walk", olt, reason),
                        "SNMP WALK error em %s: %s",
                        getattr(olt, 'name', '<unknown>'),
                        reason,
                    )
                    break

                if not varBinds:
                    break

                advanced = False
                for varBind in varBinds:
                    oid_str = str(varBind[0])
                    if not oid_str.startswith(f"{base_oid}."):
                        return results
                    val_obj = varBind[1]
                    results.append({
                        "oid": oid_str,
                        "value": self._parse_value(val_obj)
                    })
                    if oid_str != current_oid:
                        current_oid = oid_str
                        advanced = True

                if not advanced:
                    break

                if len(results) >= max_walk_rows:
                    logger.warning(
                        "SNMP WALK on %s hit max_walk_rows cap (%s); stopping walk for OID %s.",
                        getattr(olt, 'name', '<unknown>'),
                        max_walk_rows,
                        base_oid,
                    )
                    break

            return results

        return self._run(_walk())
    
    def _parse_value(self, val_obj: Any) -> Optional[Any]:
        """
        Parse valor SNMP
        Parse SNMP value
        """
        if val_obj is None:
            return None
        
        try:
            return val_obj.prettyPrint()
        except Exception:
            return str(val_obj)


snmp_service = SNMPService()
