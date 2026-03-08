from __future__ import annotations

from topology.services.fit_collector_service import fit_collector_service
from topology.services.vendor_profile import COLLECTOR_TYPE_FIT_TELNET, get_collector_type
from topology.services.zabbix_service import zabbix_service


def collector_name_for_olt(olt) -> str:
    collector_type = get_collector_type(olt)
    if collector_type == COLLECTOR_TYPE_FIT_TELNET:
        return "telnet"
    return "zabbix"


def check_olt_reachability(olt):
    collector_type = get_collector_type(olt)
    if collector_type == COLLECTOR_TYPE_FIT_TELNET:
        return fit_collector_service.check_reachability(olt)
    return zabbix_service.check_olt_reachability(olt)

