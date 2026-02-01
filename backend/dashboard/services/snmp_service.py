"""
Serviço SNMP para comunicação com OLTs
SNMP Service for OLT communication
"""
import logging
import asyncio
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
        self._engine = None
        self._pysnmp = None

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
                    ObjectType, ObjectIdentity, getCmd, nextCmd
                )
                self._pysnmp = {
                    'SnmpEngine': SnmpEngine,
                    'CommunityData': CommunityData,
                    'UdpTransportTarget': UdpTransportTarget,
                    'ContextData': ContextData,
                    'ObjectType': ObjectType,
                    'ObjectIdentity': ObjectIdentity,
                    'getCmd': getCmd,
                    'nextCmd': nextCmd
                }
            except ImportError:
                # Try alternate names for newer pysnmp versions
                try:
                    from pysnmp.hlapi.asyncio import (
                        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
                        ObjectType, ObjectIdentity, get_cmd as getCmd, next_cmd as nextCmd
                    )
                    self._pysnmp = {
                        'SnmpEngine': SnmpEngine,
                        'CommunityData': CommunityData,
                        'UdpTransportTarget': UdpTransportTarget,
                        'ContextData': ContextData,
                        'ObjectType': ObjectType,
                        'ObjectIdentity': ObjectIdentity,
                        'getCmd': getCmd,
                        'nextCmd': nextCmd
                    }
                except ImportError:
                    logger.error("Failed to import pysnmp.hlapi.asyncio. Please verify pysnmp version.")
                    raise
        return self._pysnmp

    @property
    def engine(self):
        if self._engine is None:
            self._engine = self.pysnmp_modules['SnmpEngine']()
        return self._engine

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
    
    def get(self, olt: Any, oids: List[str]) -> Optional[Dict[str, Any]]:
        """
        Executa SNMP GET para múltiplas OIDs
        Executes SNMP GET for multiple OIDs
        """
        if not oids:
            return None
        
        m = self.pysnmp_modules
        var_binds = [m['ObjectType'](m['ObjectIdentity'](oid)) for oid in oids]

        async def _get():
            try:
                errorIndication, errorStatus, errorIndex, varBinds = await m['getCmd'](
                    self.engine,
                    m['CommunityData'](olt.snmp_community, mpModel=1),
                    m['UdpTransportTarget'](
                        (olt.ip_address, olt.snmp_port),
                        timeout=self.timeout,
                        retries=self.retries
                    ),
                    m['ContextData'](),
                    *var_binds
                )

                if errorIndication or errorStatus:
                    logger.warning(
                        f"SNMP GET error em {olt.name}: "
                        f"{errorIndication or errorStatus.prettyPrint()}"
                    )
                    return None

                results = {}
                for varBind in varBinds:
                    oid_str = str(varBind[0])
                    val_obj = varBind[1]
                    results[oid_str] = self._parse_value(val_obj)

                return results
            except Exception as e:
                logger.error(f"SNMP GET exception em {olt.name}: {e}")
                return None

        return self._run(_get())
    
    def walk(self, olt: Any, oid: str) -> List[Dict[str, Any]]:
        """
        Executa SNMP WALK para uma OID
        Executes SNMP WALK for an OID
        """
        base_oid = oid.rstrip(".")
        results = []
        m = self.pysnmp_modules

        async def _walk():
            current_oid = base_oid
            while True:
                try:
                    errorIndication, errorStatus, errorIndex, varBinds = await m['nextCmd'](
                        self.engine,
                        m['CommunityData'](olt.snmp_community, mpModel=1),
                        m['UdpTransportTarget'](
                            (olt.ip_address, olt.snmp_port),
                            timeout=self.timeout,
                            retries=self.retries
                        ),
                        m['ContextData'](),
                        m['ObjectType'](m['ObjectIdentity'](current_oid)),
                        lexicographicMode=False
                    )
                except Exception as e:
                    logger.error(f"SNMP WALK exception em {olt.name}: {e}")
                    break

                if errorIndication:
                    logger.error(f"SNMP WALK error em {olt.name}: {errorIndication}")
                    break
                elif errorStatus:
                    logger.error(f"SNMP WALK error em {olt.name}: {errorStatus.prettyPrint()}")
                    break

                if not varBinds:
                    break

                advanced = False
                for row in varBinds:
                    for varBind in row:
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
