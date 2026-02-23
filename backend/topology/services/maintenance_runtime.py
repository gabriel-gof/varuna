import logging
from datetime import timedelta
from io import StringIO
from typing import Callable, Dict, Optional

from django.core.management import call_command
from django.utils import timezone

from topology.models import OLT, ONU
from topology.services.power_service import power_service


logger = logging.getLogger(__name__)


def _emit_progress(callback: Optional[Callable[[int, str], None]], percent: int, detail: str) -> None:
    if not callable(callback):
        return
    callback(percent, detail)


def has_usable_status_snapshot(olt: OLT) -> bool:
    if not olt.last_poll_at:
        return False
    return ONU.objects.filter(
        olt=olt,
        is_active=True,
        status__in=[ONU.STATUS_ONLINE, ONU.STATUS_OFFLINE],
    ).exists()


def ensure_status_snapshot_for_power(
    olt: OLT,
    *,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> None:
    if has_usable_status_snapshot(olt):
        logger.warning(
            "Power refresh OLT %s: using existing status snapshot (last_poll_at=%s).",
            olt.id,
            olt.last_poll_at,
        )
        _emit_progress(progress_callback, 35, "Using existing status snapshot.")
        return

    status_templates = ((olt.vendor_profile.oid_templates or {}).get('status', {}))
    status_oid = status_templates.get('onu_status_oid')
    if not olt.vendor_profile.supports_onu_status or not status_oid:
        logger.warning(
            "Power refresh OLT %s: status snapshot missing and polling capability is unavailable. Proceeding with stored ONU statuses.",
            olt.id,
        )
        _emit_progress(progress_callback, 35, "Proceeding without fresh status snapshot.")
        return

    _emit_progress(progress_callback, 15, "Collecting fresh ONU status before power collection.")
    logger.warning(
        "Power refresh OLT %s: status snapshot missing; running poll_onu_status before power collection.",
        olt.id,
    )
    output = StringIO()
    call_command('poll_onu_status', olt_id=olt.id, force=True, stdout=output)
    olt.refresh_from_db(fields=['last_poll_at'])
    known_status_count = ONU.objects.filter(
        olt=olt,
        is_active=True,
        status__in=[ONU.STATUS_ONLINE, ONU.STATUS_OFFLINE],
    ).count()
    logger.warning(
        "Power refresh OLT %s: pre-power status run finished (known_status_onus=%s, output=%s).",
        olt.id,
        known_status_count,
        output.getvalue().strip(),
    )
    _emit_progress(progress_callback, 35, "Status snapshot updated.")


def mark_power_collection_schedule(olt: OLT, collected_at=None) -> None:
    now = collected_at or timezone.now()
    next_at = now + timedelta(seconds=olt.power_interval_seconds or 0)
    OLT.objects.filter(id=olt.id).update(last_power_at=now, next_power_at=next_at)
    olt.last_power_at = now
    olt.next_power_at = next_at


def collect_power_for_olt(
    olt: OLT,
    *,
    force_refresh: bool = True,
    include_results: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict:
    _emit_progress(progress_callback, 5, "Preparing power collection.")
    ensure_status_snapshot_for_power(olt, progress_callback=progress_callback)

    _emit_progress(progress_callback, 45, "Loading ONU list.")
    onus = list(
        ONU.objects.filter(olt=olt, is_active=True)
        .select_related('olt', 'olt__vendor_profile')
        .order_by('slot_id', 'pon_id', 'onu_id')
    )
    result_map = power_service.refresh_for_onus(onus, force_refresh=force_refresh)
    results = [result_map.get(onu.id, {'onu_id': onu.id}) for onu in onus]

    _emit_progress(progress_callback, 85, "Finalizing power collection.")
    collected_count = sum(
        1
        for row in results
        if row.get('onu_rx_power') is not None or row.get('olt_rx_power') is not None
    )
    skipped_offline_count = sum(
        1
        for row in results
        if str(row.get('skipped_reason') or '').lower() == 'offline'
    )
    skipped_unknown_count = sum(
        1
        for row in results
        if str(row.get('skipped_reason') or '').lower() == 'unknown'
    )
    skipped_not_online_count = sum(
        1
        for row in results
        if str(row.get('skipped_reason') or '').lower() in {'offline', 'unknown', 'not_online'}
    )
    attempted_count = max(0, len(results) - skipped_not_online_count)
    mark_power_collection_schedule(olt)

    payload = {
        'status': 'completed',
        'olt_id': olt.id,
        'count': len(results),
        'attempted_count': attempted_count,
        'skipped_not_online_count': skipped_not_online_count,
        'skipped_offline_count': skipped_offline_count,
        'skipped_unknown_count': skipped_unknown_count,
        'collected_count': collected_count,
        'last_power_at': olt.last_power_at,
        'next_power_at': olt.next_power_at,
    }
    logger.warning(
        "Power refresh OLT %s summary: total=%s attempted=%s collected=%s skipped_offline=%s skipped_unknown=%s.",
        olt.id,
        payload['count'],
        payload['attempted_count'],
        payload['collected_count'],
        payload['skipped_offline_count'],
        payload['skipped_unknown_count'],
    )
    if include_results:
        payload['results'] = results
    _emit_progress(progress_callback, 95, "Power collection completed.")
    return payload
