"""
Collector health helpers for OLT runtime availability tracking.
"""
from django.utils import timezone

def mark_olt_reachable(olt, save: bool = True):
    olt.collector_reachable = True
    olt.last_collector_check_at = timezone.now()
    olt.last_collector_error = ''
    olt.collector_failure_count = 0
    if save:
        olt.save(
            update_fields=[
                'collector_reachable',
                'last_collector_check_at',
                'last_collector_error',
                'collector_failure_count',
            ]
        )


def mark_olt_unreachable(olt, error: str = '', save: bool = True):
    olt.collector_reachable = False
    olt.last_collector_check_at = timezone.now()
    olt.last_collector_error = (error or '')[:2000]
    olt.collector_failure_count = (olt.collector_failure_count or 0) + 1
    if save:
        olt.save(
            update_fields=[
                'collector_reachable',
                'last_collector_check_at',
                'last_collector_error',
                'collector_failure_count',
            ]
        )
