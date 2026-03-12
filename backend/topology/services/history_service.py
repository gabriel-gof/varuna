from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

from django.utils import timezone

from topology.models import ONU, ONUPowerSample
from topology.services.power_values import normalize_power_value


def _to_aware_datetime(value) -> Optional[datetime]:
    if value in (None, ''):
        return None
    if hasattr(value, 'tzinfo'):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def persist_power_samples(
    onus: Iterable[ONU],
    result_map: Dict[int, Dict],
    *,
    source: str = ONUPowerSample.SOURCE_SCHEDULER,
    min_read_at: Optional[timezone.datetime] = None,
    max_age_minutes: int = 15,
) -> int:
    """
    Persist fresh power snapshots from a power refresh result map.

    Only rows with at least one power value and valid reading timestamp are saved.
    If min_read_at is provided, stale rows older than this timestamp are skipped.
    """

    if not result_map:
        return 0

    now = timezone.now()
    effective_min_read_at = min_read_at
    max_age_cutoff = now - timedelta(minutes=max(1, int(max_age_minutes or 1)))
    if effective_min_read_at is None or effective_min_read_at < max_age_cutoff:
        effective_min_read_at = max_age_cutoff

    rows_by_key = {}
    for onu in onus:
        payload = result_map.get(int(onu.id)) or {}
        onu_rx = normalize_power_value(payload.get('onu_rx_power'))
        olt_rx = normalize_power_value(payload.get('olt_rx_power'))
        if onu_rx is None and olt_rx is None:
            continue

        read_at = _to_aware_datetime(payload.get('power_read_at'))
        if read_at is None or read_at < effective_min_read_at:
            continue

        rows_by_key[(int(onu.id), read_at)] = ONUPowerSample(
            olt_id=onu.olt_id,
            onu_id=onu.id,
            slot_id=int(onu.slot_id),
            pon_id=int(onu.pon_id),
            onu_number=int(onu.onu_id),
            onu_rx_power=onu_rx,
            olt_rx_power=olt_rx,
            read_at=read_at,
            source=source,
        )

    rows = list(rows_by_key.values())
    if not rows:
        return 0

    existing_keys = set(
        ONUPowerSample.objects.filter(
            onu_id__in={int(row.onu_id) for row in rows},
            read_at__in={row.read_at for row in rows},
        ).values_list('onu_id', 'read_at')
    )
    rows_to_insert = [
        row for row in rows
        if (int(row.onu_id), row.read_at) not in existing_keys
    ]
    if not rows_to_insert:
        return 0

    # Avoid per-row writes on large OLTs; duplicate keys are filtered before insert.
    ONUPowerSample.objects.bulk_create(rows_to_insert, batch_size=1000, ignore_conflicts=True)
    return len(rows_to_insert)


def sync_latest_power_snapshots(
    onus: Iterable[ONU],
    result_map: Dict[int, Dict],
    *,
    preserve_existing_empty_online: bool = False,
) -> int:
    """
    Update the per-ONU latest power snapshot used by fast UI read paths.

    Rows with no valid power value clear the latest snapshot so stale readings do
    not survive after an offline/unknown sync.
    """

    normalized_onus = []
    seen = set()
    for onu in onus:
        if not onu:
            continue
        onu_id = int(onu.id)
        if onu_id in seen:
            continue
        seen.add(onu_id)
        normalized_onus.append(onu)

    if not normalized_onus:
        return 0

    rows_to_update = []
    for onu in normalized_onus:
        payload = result_map.get(int(onu.id)) or {}
        onu_rx_power = normalize_power_value(payload.get('onu_rx_power'))
        olt_rx_power = normalize_power_value(payload.get('olt_rx_power'))
        skipped_reason = str(payload.get('skipped_reason') or '').strip().lower()

        if (
            preserve_existing_empty_online
            and onu_rx_power is None
            and olt_rx_power is None
            and str(getattr(onu, 'status', '') or '').lower() == ONU.STATUS_ONLINE
            and skipped_reason not in {'offline', 'unknown', 'not_online'}
        ):
            continue

        read_at = None
        if onu_rx_power is not None or olt_rx_power is not None:
            read_at = _to_aware_datetime(payload.get('power_read_at'))
            if read_at is None:
                onu_rx_power = None
                olt_rx_power = None

        if (
            normalize_power_value(getattr(onu, 'latest_onu_rx_power', None)) == onu_rx_power
            and normalize_power_value(getattr(onu, 'latest_olt_rx_power', None)) == olt_rx_power
            and getattr(onu, 'latest_power_read_at', None) == read_at
        ):
            continue

        onu.latest_onu_rx_power = onu_rx_power
        onu.latest_olt_rx_power = olt_rx_power
        onu.latest_power_read_at = read_at
        rows_to_update.append(onu)

    if not rows_to_update:
        return 0

    ONU.objects.bulk_update(
        rows_to_update,
        ['latest_onu_rx_power', 'latest_olt_rx_power', 'latest_power_read_at'],
        batch_size=1000,
    )
    return len(rows_to_update)


def _normalize_onu_ids(onu_ids: Iterable[int]) -> list[int]:
    normalized = []
    seen = set()
    for raw in onu_ids:
        try:
            onu_id = int(raw)
        except (TypeError, ValueError):
            continue
        if onu_id in seen:
            continue
        seen.add(onu_id)
        normalized.append(onu_id)
    return normalized


def get_latest_power_snapshot_map(onu_ids: Iterable[int]) -> Dict[int, Dict]:
    """
    Return the latest synced power snapshot per ONU as a read-model payload.
    """

    normalized_ids = _normalize_onu_ids(onu_ids)
    if not normalized_ids:
        return {}

    latest_rows = ONU.objects.filter(id__in=normalized_ids).values(
        'id',
        'latest_onu_rx_power',
        'latest_olt_rx_power',
        'latest_power_read_at',
    )

    result: Dict[int, Dict] = {}
    for row in latest_rows:
        onu_id = int(row['id'])
        onu_rx_power = normalize_power_value(row.get('latest_onu_rx_power'))
        olt_rx_power = normalize_power_value(row.get('latest_olt_rx_power'))
        if onu_rx_power is None and olt_rx_power is None:
            continue

        read_at = row.get('latest_power_read_at')
        result[onu_id] = {
            'onu_rx_power': onu_rx_power,
            'olt_rx_power': olt_rx_power,
            'power_read_at': read_at.isoformat() if read_at else None,
        }
    return result
