"""
Compatibility stub.

Direct backend SNMP collection was removed; Varuna uses Zabbix as the only
collection engine. This module is kept only to avoid import breakage in legacy
code/tests and should not be used by runtime paths.
"""

from __future__ import annotations


class SNMPService:
    def _disabled(self):
        raise RuntimeError(
            "Direct SNMP runtime is disabled. Use Zabbix collection APIs instead."
        )

    def get(self, *args, **kwargs):
        self._disabled()

    def walk(self, *args, **kwargs):
        self._disabled()


snmp_service = SNMPService()
