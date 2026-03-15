from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from django.db.models import Count, F, Q
from django.utils import timezone

from topology.models import OLT, OLTPON, OLTSlot, ONU


@dataclass(frozen=True)
class OltCounterSnapshot:
    slot_count: int
    pon_count: int
    onu_count: int
    online_count: int
    offline_count: int


class TopologyCounterService:
    """
    Maintains denormalized topology counters for fast OLT/settings reads.
    """

    def clear_olt(self, olt_id: int) -> None:
        OLTSlot.objects.filter(olt_id=olt_id, is_active=True).update(
            cached_pon_count=None,
            cached_onu_count=None,
            cached_online_count=None,
            cached_offline_count=None,
        )
        OLTPON.objects.filter(olt_id=olt_id, is_active=True).update(
            cached_onu_count=None,
            cached_online_count=None,
            cached_offline_count=None,
        )
        OLT.objects.filter(id=olt_id).update(
            cached_slot_count=None,
            cached_pon_count=None,
            cached_onu_count=None,
            cached_online_count=None,
            cached_offline_count=None,
            cached_counts_at=None,
        )

    @staticmethod
    def _count_online(rows: Iterable[Dict]) -> Dict[int, Dict[str, int]]:
        result: Dict[int, Dict[str, int]] = {}
        for row in rows:
            key = int(row["key"])
            total = int(row.get("total") or 0)
            online = int(row.get("online") or 0)
            result[key] = {
                "total": total,
                "online": online,
                "offline": max(total - online, 0),
            }
        return result

    def refresh_olt(self, olt_id: int) -> OltCounterSnapshot:
        active_slots = list(
            OLTSlot.objects.filter(olt_id=olt_id, is_active=True).only(
                "id",
                "cached_pon_count",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
            )
        )
        active_pons = list(
            OLTPON.objects.filter(olt_id=olt_id, is_active=True).only(
                "id",
                "slot_id",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
            )
        )

        slot_map = {int(slot.id): slot for slot in active_slots}
        pon_map = {int(pon.id): pon for pon in active_pons}

        pon_count_by_slot: Dict[int, int] = {}
        for pon in active_pons:
            slot_id = int(pon.slot_id)
            pon_count_by_slot[slot_id] = pon_count_by_slot.get(slot_id, 0) + 1

        slot_onu_rows = ONU.objects.filter(
            olt_id=olt_id,
            is_active=True,
            slot_ref_id__in=slot_map.keys(),
        ).values(key=F("slot_ref_id")).annotate(
            total=Count("id"),
            online=Count("id", filter=Q(status=ONU.STATUS_ONLINE)),
        )
        slot_onu_map = self._count_online(slot_onu_rows)

        for slot_id, slot in slot_map.items():
            onu_stats = slot_onu_map.get(slot_id, {"total": 0, "online": 0, "offline": 0})
            slot.cached_pon_count = int(pon_count_by_slot.get(slot_id, 0))
            slot.cached_onu_count = int(onu_stats["total"])
            slot.cached_online_count = int(onu_stats["online"])
            slot.cached_offline_count = int(onu_stats["offline"])

        if active_slots:
            OLTSlot.objects.bulk_update(
                active_slots,
                [
                    "cached_pon_count",
                    "cached_onu_count",
                    "cached_online_count",
                    "cached_offline_count",
                ],
                batch_size=500,
            )

        pon_onu_rows = ONU.objects.filter(
            olt_id=olt_id,
            is_active=True,
            pon_ref_id__in=pon_map.keys(),
        ).values(key=F("pon_ref_id")).annotate(
            total=Count("id"),
            online=Count("id", filter=Q(status=ONU.STATUS_ONLINE)),
        )
        pon_onu_map = self._count_online(pon_onu_rows)

        for pon_id, pon in pon_map.items():
            onu_stats = pon_onu_map.get(pon_id, {"total": 0, "online": 0, "offline": 0})
            pon.cached_onu_count = int(onu_stats["total"])
            pon.cached_online_count = int(onu_stats["online"])
            pon.cached_offline_count = int(onu_stats["offline"])

        if active_pons:
            OLTPON.objects.bulk_update(
                active_pons,
                [
                    "cached_onu_count",
                    "cached_online_count",
                    "cached_offline_count",
                ],
                batch_size=500,
            )

        onu_totals = ONU.objects.filter(olt_id=olt_id, is_active=True).aggregate(
            total=Count("id"),
            online=Count("id", filter=Q(status=ONU.STATUS_ONLINE)),
        )
        onu_count = int(onu_totals.get("total") or 0)
        online_count = int(onu_totals.get("online") or 0)
        snapshot = OltCounterSnapshot(
            slot_count=len(active_slots),
            pon_count=len(active_pons),
            onu_count=onu_count,
            online_count=online_count,
            offline_count=max(onu_count - online_count, 0),
        )

        OLT.objects.filter(id=olt_id).update(
            cached_slot_count=snapshot.slot_count,
            cached_pon_count=snapshot.pon_count,
            cached_onu_count=snapshot.onu_count,
            cached_online_count=snapshot.online_count,
            cached_offline_count=snapshot.offline_count,
            cached_counts_at=timezone.now(),
        )
        return snapshot


topology_counter_service = TopologyCounterService()
