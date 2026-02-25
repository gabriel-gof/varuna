"""
SNMP health helpers for OLT runtime availability tracking.
"""
from django.utils import timezone

def mark_olt_reachable(olt, save: bool = True):
    olt.snmp_reachable = True
    olt.last_snmp_check_at = timezone.now()
    olt.last_snmp_error = ''
    olt.snmp_failure_count = 0
    if save:
        olt.save(update_fields=['snmp_reachable', 'last_snmp_check_at', 'last_snmp_error', 'snmp_failure_count'])


def mark_olt_unreachable(olt, error: str = '', save: bool = True):
    olt.snmp_reachable = False
    olt.last_snmp_check_at = timezone.now()
    olt.last_snmp_error = (error or '')[:2000]
    olt.snmp_failure_count = (olt.snmp_failure_count or 0) + 1
    if save:
        olt.save(update_fields=['snmp_reachable', 'last_snmp_check_at', 'last_snmp_error', 'snmp_failure_count'])
