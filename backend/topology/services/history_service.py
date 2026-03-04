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

    rows = []
    for onu in onus:
        payload = result_map.get(int(onu.id)) or {}
        onu_rx = normalize_power_value(payload.get('onu_rx_power'))
        olt_rx = normalize_power_value(payload.get('olt_rx_power'))
        if onu_rx is None and olt_rx is None:
            continue

        read_at = _to_aware_datetime(payload.get('power_read_at'))
        if read_at is None or read_at < effective_min_read_at:
            continue

        rows.append(
            ONUPowerSample(
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
        )

    if not rows:
        return 0

    # Avoid per-row writes on large OLTs; duplicates are ignored by unique constraint.
    ONUPowerSample.objects.bulk_create(rows, batch_size=1000, ignore_conflicts=True)
    return len(rows)
