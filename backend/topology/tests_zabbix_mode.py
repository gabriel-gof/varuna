from unittest.mock import patch
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from topology.api.views import OLTViewSet, ONUViewSet
from topology.models import OLT, ONU, ONULog, ONUPowerSample, VendorProfile
from topology.services.cache_service import cache_service
from topology.services.maintenance_runtime import collect_power_for_olt, has_usable_status_snapshot
from topology.services.power_service import power_service
from topology.services.zabbix_service import (
    DEFAULT_AVAILABILITY_ITEM_KEY,
    VARUNA_HOST_TAG_MODEL,
    VARUNA_HOST_TAG_SOURCE,
    VARUNA_HOST_TAG_SOURCE_VALUE,
    VARUNA_AVAILABILITY_INTERVAL_MACRO,
    VARUNA_HOST_TAG_VENDOR,
    VARUNA_DISCOVERY_INTERVAL_MACRO,
    VARUNA_HISTORY_DAYS_MACRO,
    VARUNA_POWER_INTERVAL_MACRO,
    VARUNA_SNMP_COMMUNITY_MACRO,
    VARUNA_SNMP_IP_MACRO,
    VARUNA_SNMP_PORT_MACRO,
    VARUNA_STATUS_INTERVAL_MACRO,
    ZabbixService,
)
from topology.management.commands.run_scheduler import Command as SchedulerCommand
from topology.management.commands.discover_onus import _normalize_serial


def _zabbix_vendor_templates():
    return {
        "zabbix": {
            "discovery_item_key": "onuDiscovery",
            "availability_item_key": DEFAULT_AVAILABILITY_ITEM_KEY,
            "status_item_key_pattern": "onuStatusValue[{index}]",
            "reason_item_key_pattern": "onuDisconnectReason[{index}]",
            "onu_rx_item_key_pattern": "onuRxPower[{index}]",
            "olt_rx_item_key_pattern": "oltRxPower[{index}]",
        },
        "status": {
            "status_map": {},
        },
        "indexing": {
            "parts": {
                "pon_numeric": 0,
                "onu_id": 1,
            }
        },
    }


class ZabbixModeTests(TestCase):
    def setUp(self):
        self.api_factory = APIRequestFactory()
        self.user = User.objects.create_user(username="zabbix-api", password="zabbix-api")
        self.vendor = VendorProfile.objects.create(
            vendor="Huawei",
            model_name="MA5680T-ZABBIX-TEST",
            description="Zabbix mode test vendor",
            oid_templates=_zabbix_vendor_templates(),
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=True,
            default_thresholds={},
            is_active=True,
        )
        self.olt = OLT.objects.create(
            name="OLT-ZABBIX-TEST",
            vendor_profile=self.vendor,
            protocol="snmp",
            ip_address="10.0.0.10",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )

    def test_has_usable_status_snapshot_requires_fresh_reachable_data(self):
        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index="11.1",
            serial="ABCD11111111",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        self.olt.last_poll_at = timezone.now() - timedelta(seconds=60)
        self.olt.snmp_reachable = True
        self.olt.save(update_fields=["last_poll_at", "snmp_reachable"])
        self.assertTrue(has_usable_status_snapshot(self.olt))

        self.olt.snmp_reachable = False
        self.olt.save(update_fields=["snmp_reachable"])
        self.assertFalse(has_usable_status_snapshot(self.olt))

        self.olt.snmp_reachable = True
        self.olt.last_poll_at = timezone.now() - timedelta(minutes=20)
        self.olt.save(update_fields=["snmp_reachable", "last_poll_at"])
        self.assertFalse(has_usable_status_snapshot(self.olt))

    def test_overlay_runtime_onu_payload_keeps_timestamp_when_cache_window_is_blank(self):
        payload = {
            "status": ONU.STATUS_OFFLINE,
            "disconnect_reason": ONULog.REASON_LINK_LOSS,
            "offline_since": "2026-03-02T18:00:00+00:00",
            "disconnect_window_start": "2026-03-02T18:00:00+00:00",
            "disconnect_window_end": "2026-03-02T18:00:00+00:00",
        }
        OLTViewSet._overlay_runtime_onu_payload(
            payload,
            status_payload={
                "status": ONU.STATUS_OFFLINE,
                "disconnect_reason": "",
                "offline_since": "",
                "disconnect_window_start": "",
                "disconnect_window_end": "",
            },
            power_payload={},
        )
        self.assertEqual(payload["disconnect_reason"], ONULog.REASON_LINK_LOSS)
        self.assertEqual(payload["offline_since"], "2026-03-02T18:00:00+00:00")
        self.assertEqual(payload["disconnect_window_start"], "2026-03-02T18:00:00+00:00")
        self.assertEqual(payload["disconnect_window_end"], "2026-03-02T18:00:00+00:00")

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_uses_zabbix_rows(self, fetch_discovery_rows_mock):
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "1",
                    "{#PON}": "2",
                    "{#ONU_ID}": "3",
                    "{#PON_ID}": "11",
                    "{#SNMPINDEX}": "11.3",
                    "{#SERIAL}": "ABCD12345678",
                    "{#ONU_NAME}": "client-1",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        onu = ONU.objects.get(olt=self.olt, slot_id=1, pon_id=2, onu_id=3)
        self.assertEqual(onu.snmp_index, "11.3")
        self.assertEqual(onu.serial, "ABCD12345678")
        self.assertEqual(onu.name, "client-1")
        self.olt.refresh_from_db()
        self.assertTrue(self.olt.snmp_reachable)

    def test_normalize_serial_prefers_serial_like_fragment_when_value_has_comma(self):
        self.assertEqual(_normalize_serial("TPLG-D22D7400,"), "TPLGD22D7400")
        self.assertEqual(_normalize_serial("thiago.sodre100, TPLG-D22D7400"), "TPLGD22D7400")
        self.assertEqual(_normalize_serial("TPLG-D22D7400, thiago.sodre100"), "TPLGD22D7400")

    @override_settings(
        ZABBIX_DISCOVERY_REFRESH_WAIT_SECONDS=2,
        ZABBIX_DISCOVERY_REFRESH_WAIT_STEP_SECONDS=1,
    )
    @patch("topology.management.commands.discover_onus.time.sleep")
    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_retries_after_refresh_when_rows_are_not_immediately_available(
        self,
        fetch_discovery_rows_mock,
        sleep_mock,
    ):
        fetch_discovery_rows_mock.side_effect = [
            ([], None),
            (
                [
                    {
                        "{#SLOT}": "1",
                        "{#PON}": "2",
                        "{#ONU_ID}": "3",
                        "{#PON_ID}": "11",
                        "{#SNMPINDEX}": "11.3",
                        "{#SERIAL}": "ABCD12345678",
                        "{#ONU_NAME}": "client-1",
                    }
                ],
                timezone.now().isoformat(),
            ),
        ]

        call_command("discover_onus", olt_id=self.olt.id, force=True, refresh_upstream=True)

        self.assertEqual(fetch_discovery_rows_mock.call_count, 2)
        sleep_mock.assert_called_once()
        self.assertTrue(
            ONU.objects.filter(olt=self.olt, slot_id=1, pon_id=2, onu_id=3, is_active=True).exists()
        )

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_schedules_immediate_poll_for_fresh_status(self, fetch_discovery_rows_mock):
        self.olt.next_poll_at = timezone.now() + timedelta(minutes=30)
        self.olt.save(update_fields=["next_poll_at"])
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "1",
                    "{#PON}": "2",
                    "{#ONU_ID}": "3",
                    "{#PON_ID}": "11",
                    "{#SNMPINDEX}": "11.3",
                    "{#SERIAL}": "ABCD12345678",
                    "{#ONU_NAME}": "client-1",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        self.olt.refresh_from_db()
        self.assertLessEqual(self.olt.next_poll_at, timezone.now())

    @patch("topology.management.commands.run_scheduler.call_command")
    def test_scheduler_tick_runs_discovery_before_polling(self, call_command_mock):
        scheduler = SchedulerCommand()
        scheduler.max_poll_olts_per_tick = None
        scheduler.max_discovery_olts_per_tick = None
        scheduler.max_power_olts_per_tick = None

        with patch("topology.management.commands.run_scheduler.OLT.objects.filter") as olt_filter_mock:
            olt_filter_mock.return_value.select_related.return_value = []
            scheduler._tick()

        command_names = [args[0] for args, _ in call_command_mock.call_args_list]
        self.assertGreaterEqual(len(command_names), 2)
        self.assertEqual(command_names[:2], ["discover_onus", "poll_onu_status"])

    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_uses_zabbix_status_items(self, fetch_status_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        self.olt.last_poll_at = timezone.now() - timedelta(minutes=1)
        self.olt.snmp_reachable = True
        self.olt.save(update_fields=["last_poll_at", "snmp_reachable"])

        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": "offline",
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "901",
                }
            },
            timezone.now().isoformat(),
        )

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        onu.refresh_from_db()
        self.assertEqual(onu.status, ONU.STATUS_OFFLINE)
        open_log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        self.assertEqual(open_log.disconnect_reason, ONULog.REASON_LINK_LOSS)
        self.olt.refresh_from_db()
        self.assertTrue(self.olt.snmp_reachable)

    @override_settings(ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS=1)
    @patch("topology.management.commands.poll_onu_status.zabbix_service.execute_items_now_by_keys")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.get_hostid")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_respects_upstream_cap_without_force(
        self,
        fetch_status_mock,
        get_hostid_mock,
        execute_now_mock,
    ):
        onu_a = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index="11.1",
            serial="ABCD11111111",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index="11.2",
            serial="ABCD22222222",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_status_mock.return_value = (
            {
                "11.1": {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "901",
                },
                "11.2": {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "902",
                },
            },
            timezone.now().isoformat(),
        )
        get_hostid_mock.return_value = "10090"

        call_command("poll_onu_status", olt_id=self.olt.id, force=True, refresh_upstream=True)

        execute_now_mock.assert_not_called()
        fetch_status_mock.assert_called_once()
        for onu in (onu_a, onu_b):
            onu.refresh_from_db()
            self.assertEqual(onu.status, ONU.STATUS_ONLINE)

    @override_settings(ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS=1)
    @patch("topology.management.commands.poll_onu_status.zabbix_service.execute_items_now_by_keys")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.get_hostid")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_force_upstream_bypasses_cap(
        self,
        fetch_status_mock,
        get_hostid_mock,
        execute_now_mock,
    ):
        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index="11.1",
            serial="ABCD11111111",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index="11.2",
            serial="ABCD22222222",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_status_mock.return_value = (
            {
                "11.1": {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "901",
                },
                "11.2": {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "902",
                },
            },
            timezone.now().isoformat(),
        )
        get_hostid_mock.return_value = "10090"
        execute_now_mock.return_value = 2

        call_command(
            "poll_onu_status",
            olt_id=self.olt.id,
            force=True,
            refresh_upstream=True,
            force_upstream=True,
        )

        execute_now_mock.assert_called_once()
        called_hostid = execute_now_mock.call_args.args[0]
        self.assertEqual(called_hostid, "10090")

    @override_settings(
        ZABBIX_DISCONNECT_HISTORY_MAX_ITEMS=128,
        ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS=90,
    )
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_previous_status_samples")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_uses_zabbix_transition_window(self, fetch_status_mock, fetch_prev_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        current_epoch = int(timezone.now().timestamp())
        previous_epoch = current_epoch - 300
        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": "offline",
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_itemid": "901",
                    "status_clock_epoch": current_epoch,
                }
            },
            timezone.now().isoformat(),
        )
        fetch_prev_mock.return_value = {
            "901": {"status": "online", "clock_epoch": previous_epoch}
        }

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        expected_start = datetime.fromtimestamp(previous_epoch, tz=dt_timezone.utc)
        expected_end = datetime.fromtimestamp(current_epoch, tz=dt_timezone.utc)
        self.assertEqual(log.disconnect_window_start, expected_start)
        self.assertEqual(log.disconnect_window_end, expected_end)
        self.assertEqual(log.offline_since, expected_end)

    @override_settings(
        ZABBIX_DISCONNECT_HISTORY_MAX_ITEMS=128,
        ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS=90,
    )
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_previous_status_samples")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_skips_transition_window_when_gap_is_untrusted(self, fetch_status_mock, fetch_prev_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        # polling_interval_seconds=300 => trusted gap max = 300*2 + 90 = 690s.
        current_epoch = int(timezone.now().timestamp())
        previous_epoch = current_epoch - 900
        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": "offline",
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_itemid": "901",
                    "status_clock_epoch": current_epoch,
                }
            },
            timezone.now().isoformat(),
        )
        fetch_prev_mock.return_value = {
            "901": {"status": "online", "clock_epoch": previous_epoch}
        }

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        expected_point = datetime.fromtimestamp(current_epoch, tz=dt_timezone.utc)
        self.assertEqual(log.disconnect_window_start, expected_point)
        self.assertEqual(log.disconnect_window_end, expected_point)

    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_preserves_state_when_only_stale_samples_exist(self, fetch_status_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        stale_epoch = int(timezone.now().timestamp()) - 5000
        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": ONU.STATUS_OFFLINE,
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_clock_epoch": stale_epoch,
                    "status_itemid": "901",
                }
            },
            timezone.now().isoformat(),
        )

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        onu.refresh_from_db()
        self.assertEqual(onu.status, ONU.STATUS_ONLINE)
        self.olt.refresh_from_db()
        self.assertFalse(self.olt.snmp_reachable)
        self.assertFalse(ONULog.objects.filter(onu=onu, offline_until__isnull=True).exists())

    @override_settings(ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS=0)
    @patch("topology.management.commands.poll_onu_status.cache_service.invalidate_topology_api_cache")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.check_olt_reachability")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.execute_items_now_by_keys")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.get_hostid")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_refresh_upstream_accepts_recent_pre_refresh_samples(
        self,
        fetch_status_mock,
        get_hostid_mock,
        execute_now_mock,
        check_reachability_mock,
        invalidate_topology_cache_mock,
    ):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now_epoch = int(timezone.now().timestamp())
        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": ONU.STATUS_ONLINE,
                    "reason": "",
                    "status_clock_epoch": now_epoch - 120,
                    "status_itemid": "901",
                }
            },
            timezone.now().isoformat(),
        )
        get_hostid_mock.return_value = "10090"
        execute_now_mock.return_value = 1
        check_reachability_mock.return_value = (True, "")

        call_command(
            "poll_onu_status",
            olt_id=self.olt.id,
            force=True,
            refresh_upstream=True,
            force_upstream=True,
        )

        onu.refresh_from_db()
        self.assertEqual(onu.status, ONU.STATUS_ONLINE)
        self.assertFalse(ONULog.objects.filter(onu=onu, offline_until__isnull=True).exists())
        self.olt.refresh_from_db()
        self.assertTrue(self.olt.snmp_reachable)
        self.assertEqual((self.olt.last_snmp_error or "").strip(), "")
        execute_now_mock.assert_called_once()
        check_reachability_mock.assert_called_once()
        invalidate_topology_cache_mock.assert_called_once_with(self.olt.id)

    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_power_service_uses_zabbix_items(self, fetch_power_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_power_mock.return_value = (
            {
                "11.3": {
                    "onu_rx_power": -19.5,
                    "olt_rx_power": -23.1,
                    "power_read_at": timezone.now().isoformat(),
                }
            },
            timezone.now().isoformat(),
        )

        result = power_service.refresh_for_onus([onu], force_refresh=True)
        row = result.get(onu.id) or {}

        self.assertEqual(row.get("onu_rx_power"), -19.5)
        self.assertEqual(row.get("olt_rx_power"), -23.1)
        self.assertTrue(row.get("power_read_at"))

    @override_settings(ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS=1)
    @patch("topology.services.power_service.zabbix_service.execute_items_now_by_keys")
    @patch("topology.services.power_service.zabbix_service.get_hostid")
    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_power_service_respects_upstream_cap_without_force(
        self,
        fetch_power_mock,
        get_hostid_mock,
        execute_now_mock,
    ):
        onu_a = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=10,
            snmp_index="11.10",
            serial="ABCD11111110",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=11,
            snmp_index="11.11",
            serial="ABCD11111111",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_power_mock.return_value = (
            {
                "11.10": {"onu_rx_power": -20.1, "olt_rx_power": -24.2, "power_read_at": timezone.now().isoformat()},
                "11.11": {"onu_rx_power": -20.3, "olt_rx_power": -24.4, "power_read_at": timezone.now().isoformat()},
            },
            timezone.now().isoformat(),
        )
        get_hostid_mock.return_value = "10090"

        result = power_service.refresh_for_onus(
            [onu_a, onu_b],
            force_refresh=True,
            refresh_upstream=True,
            force_upstream=False,
        )

        execute_now_mock.assert_not_called()
        self.assertIn(onu_a.id, result)
        self.assertIn(onu_b.id, result)

    @override_settings(ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS=1)
    @patch("topology.services.power_service.zabbix_service.execute_items_now_by_keys")
    @patch("topology.services.power_service.zabbix_service.get_hostid")
    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_power_service_force_upstream_bypasses_cap(
        self,
        fetch_power_mock,
        get_hostid_mock,
        execute_now_mock,
    ):
        onu_a = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=12,
            snmp_index="11.12",
            serial="ABCD11111112",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=13,
            snmp_index="11.13",
            serial="ABCD11111113",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_power_mock.return_value = (
            {
                "11.12": {"onu_rx_power": -20.1, "olt_rx_power": -24.2, "power_read_at": timezone.now().isoformat()},
                "11.13": {"onu_rx_power": -20.3, "olt_rx_power": -24.4, "power_read_at": timezone.now().isoformat()},
            },
            timezone.now().isoformat(),
        )
        get_hostid_mock.return_value = "10090"
        execute_now_mock.return_value = 4

        power_service.refresh_for_onus(
            [onu_a, onu_b],
            force_refresh=True,
            refresh_upstream=True,
            force_upstream=True,
        )

        execute_now_mock.assert_called_once()
        self.assertEqual(execute_now_mock.call_args.args[0], "10090")

    @override_settings(
        ZABBIX_REFRESH_CLOCK_GRACE_SECONDS=0,
        ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS=2,
        ZABBIX_REFRESH_UPSTREAM_WAIT_STEP_SECONDS=1,
    )
    @patch("topology.services.power_service.time.sleep")
    @patch("topology.services.power_service.zabbix_service.execute_items_now_by_keys")
    @patch("topology.services.power_service.zabbix_service.get_hostid")
    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_power_service_refresh_upstream_retries_until_fresh_clock(
        self,
        fetch_power_mock,
        get_hostid_mock,
        execute_now_mock,
        sleep_mock,
    ):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=21,
            snmp_index="11.21",
            serial="ABCD12340021",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now_epoch = int(timezone.now().timestamp())
        stale_epoch = now_epoch - 120
        fresh_epoch = now_epoch + 5
        fetch_power_mock.side_effect = [
            (
                {
                    "11.21": {
                        "onu_rx_power": -24.8,
                        "olt_rx_power": None,
                        "power_read_at": datetime.fromtimestamp(stale_epoch, tz=dt_timezone.utc).isoformat(),
                        "power_clock_epoch": stale_epoch,
                    }
                },
                timezone.now().isoformat(),
            ),
            (
                {
                    "11.21": {
                        "onu_rx_power": -24.6,
                        "olt_rx_power": None,
                        "power_read_at": datetime.fromtimestamp(fresh_epoch, tz=dt_timezone.utc).isoformat(),
                        "power_clock_epoch": fresh_epoch,
                    }
                },
                timezone.now().isoformat(),
            ),
        ]
        get_hostid_mock.return_value = "10090"
        execute_now_mock.return_value = 1

        result = power_service.refresh_for_onus(
            [onu],
            force_refresh=True,
            refresh_upstream=True,
            force_upstream=True,
        )

        row = result.get(onu.id) or {}
        self.assertEqual(row.get("onu_rx_power"), -24.6)
        self.assertTrue(row.get("power_read_at"))
        self.assertEqual(fetch_power_mock.call_count, 2)
        sleep_mock.assert_any_call(1)
        execute_now_mock.assert_called_once()

    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_power_service_refresh_upstream_does_not_fallback_to_cached_stale_values(self, fetch_power_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=30,
            snmp_index="11.30",
            serial="ABCD12340030",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        cache_service.set_many_onu_power(
            self.olt.id,
            {
                onu.id: {
                    "onu_id": onu.id,
                    "slot_id": onu.slot_id,
                    "pon_id": onu.pon_id,
                    "onu_number": onu.onu_id,
                    "onu_rx_power": -19.0,
                    "olt_rx_power": -24.0,
                    "power_read_at": timezone.now().isoformat(),
                }
            },
            ttl=3600,
        )
        stale_epoch = int(timezone.now().timestamp()) - 5000
        fetch_power_mock.return_value = (
            {
                "11.30": {
                    "onu_rx_power": -18.0,
                    "olt_rx_power": -23.0,
                    "power_read_at": datetime.fromtimestamp(stale_epoch, tz=dt_timezone.utc).isoformat(),
                    "power_clock_epoch": stale_epoch,
                }
            },
            timezone.now().isoformat(),
        )

        result = power_service.refresh_for_onus(
            [onu],
            force_refresh=True,
            refresh_upstream=True,
            force_upstream=True,
        )
        row = result.get(onu.id) or {}
        self.assertIsNone(row.get("onu_rx_power"))
        self.assertIsNone(row.get("olt_rx_power"))
        self.assertIsNone(row.get("power_read_at"))

    def test_discovery_rows_can_be_loaded_from_lld_history(self):
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value={"itemid": "50430", "value_type": "4"}),
            patch.object(
                service,
                "get_latest_history_value",
                return_value=(
                    '[{"{#SLOT}":"1","{#PON}":"2","{#ONU_ID}":"3","{#SERIAL}":"ABCD12345678"}]',
                    timezone.now().isoformat(),
                ),
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(self.olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("{#ONU_ID}"), "3")
        self.assertTrue(read_at)

    def test_discovery_rows_fallback_to_status_items_huawei(self):
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[4194394112.3]",
                        "name": "ONU 0/11/0/3 thiago.sodre100: Status",
                        "lastclock": "1710000000",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(self.olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "4194394112.3")
        self.assertEqual(row.get("{#SLOT}"), "11")
        self.assertEqual(row.get("{#PON}"), "0")
        self.assertEqual(row.get("{#ONU_ID}"), "3")
        self.assertEqual(row.get("{#PON_ID}"), "4194394112")
        self.assertEqual(row.get("{#ONU_NAME}"), "thiago.sodre100")
        self.assertTrue(read_at)

    def test_discovery_rows_fallback_to_status_items_huawei_name_with_serial(self):
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[4194394112.3]",
                        "name": "ONU 11/0/3 thiago.sodre100 [54 50 4C 47 D2 2D 74 00]: Status",
                        "lastclock": "1710000000",
                    }
                ],
            ),
        ):
            rows, _ = service.fetch_discovery_rows(self.olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#ONU_NAME}"), "thiago.sodre100")
        self.assertEqual(row.get("{#SERIAL}"), "0X54504C47D22D7400")

    def test_discovery_rows_fallback_to_status_items_fiberhome(self):
        vendor = VendorProfile.objects.create(
            vendor="Fiberhome",
            model_name="AN5516-ZABBIX-TEST",
            description="Zabbix mode test vendor fiberhome",
            oid_templates=_zabbix_vendor_templates(),
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=True,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-FIBERHOME-ZABBIX-TEST",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.20",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10088"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[436732672]",
                        "name": "ONU PON 13/1/3 TPLGD22D7400: Status",
                        "lastclock": "1710000001",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "436732672")
        self.assertEqual(row.get("{#SLOT}"), "13")
        self.assertEqual(row.get("{#PON}"), "1")
        self.assertEqual(row.get("{#ONU_ID}"), "3")
        self.assertEqual(row.get("{#SERIAL}"), "TPLGD22D7400")
        self.assertTrue(read_at)

    def test_discovery_rows_fallback_to_status_items_vsol_like(self):
        vsol_templates = _zabbix_vendor_templates()
        vsol_templates["indexing"] = {
            "regex": r"^(?P<pon_id>\d+)\.(?P<onu_id>\d+)$",
            "fixed": {"slot_id": 1},
        }
        vendor = VendorProfile.objects.create(
            vendor="vsol like",
            model_name="GPON 8P-ZABBIX-TEST",
            description="Zabbix mode test vendor vsol like",
            oid_templates=vsol_templates,
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=False,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-VSOL-ZABBIX-TEST",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.24",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10085"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[2.10]",
                        "name": "ONU 1/2/10 cassiano.freitas MONU0085F6D1: Status",
                        "lastclock": "1710000003",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "2.10")
        self.assertEqual(row.get("{#SLOT}"), "1")
        self.assertEqual(row.get("{#PON}"), "2")
        self.assertEqual(row.get("{#ONU_ID}"), "10")
        self.assertEqual(row.get("{#ONU_NAME}"), "cassiano.freitas")
        self.assertEqual(row.get("{#SERIAL}"), "MONU0085F6D1")
        self.assertTrue(read_at)

    def test_discovery_rows_fallback_to_status_items_vsol_like_serial_with_trailing_comma(self):
        vsol_templates = _zabbix_vendor_templates()
        vsol_templates["indexing"] = {
            "regex": r"^(?P<pon_id>\d+)\.(?P<onu_id>\d+)$",
            "fixed": {"slot_id": 1},
        }
        vendor = VendorProfile.objects.create(
            vendor="vsol like",
            model_name="GPON 8P-ZABBIX-TEST-COMMA",
            description="Zabbix mode test vendor vsol like comma serial",
            oid_templates=vsol_templates,
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=False,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-VSOL-ZABBIX-TEST-COMMA",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.25",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10086"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[2.10]",
                        "name": "ONU 1/2/10 cassiano.freitas MONU0085F6D1,: Status",
                        "lastclock": "1710000004",
                    }
                ],
            ),
        ):
            rows, _ = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SERIAL}"), "MONU0085F6D1")

    def test_discovery_rows_fallback_to_status_items_fiberhome_without_pon_prefix(self):
        vendor = VendorProfile.objects.create(
            vendor="Fiberhome",
            model_name="AN5516-ZABBIX-TEST-NO-PON-PREFIX",
            description="Zabbix mode test vendor fiberhome item name without PON prefix",
            oid_templates=_zabbix_vendor_templates(),
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=True,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-FIBERHOME-ZABBIX-TEST-NO-PON-PREFIX",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.23",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10086"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[436732672]",
                        "name": "ONU 13/1/3 TPLGD22D7400: Status",
                        "lastclock": "1710000001",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "436732672")
        self.assertEqual(row.get("{#SLOT}"), "13")
        self.assertEqual(row.get("{#PON}"), "1")
        self.assertEqual(row.get("{#ONU_ID}"), "3")
        self.assertEqual(row.get("{#SERIAL}"), "TPLGD22D7400")
        self.assertTrue(read_at)

    def test_discovery_rows_fallback_to_status_items_fiberhome_serial_only_name(self):
        vendor = VendorProfile.objects.create(
            vendor="Fiberhome",
            model_name="AN5516-ZABBIX-TEST-SERIAL-ONLY",
            description="Zabbix mode test vendor fiberhome serial-only item name",
            oid_templates=_zabbix_vendor_templates(),
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=True,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-FIBERHOME-ZABBIX-TEST-SERIAL-ONLY",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.21",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10089"),
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[436732672]",
                        "name": "ONU {#PON} TPLGD22D7400: Status",
                        "lastclock": "1710000002",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "436732672")
        # 436732672 == 0x1A080300 => slot=13, pon=1, onu=3 in Fiberhome flat index layout.
        self.assertEqual(row.get("{#SLOT}"), "13")
        self.assertEqual(row.get("{#PON}"), "1")
        self.assertEqual(row.get("{#ONU_ID}"), "3")
        self.assertEqual(row.get("{#SERIAL}"), "TPLGD22D7400")
        self.assertTrue(read_at)

    def test_fetch_status_by_index_accepts_embedded_offline_reason(self):
        vendor = VendorProfile.objects.create(
            vendor="Fiberhome",
            model_name="AN5516-ZABBIX-TEST-STATUS",
            description="Zabbix mode test vendor fiberhome status reason in status value",
            oid_templates=_zabbix_vendor_templates(),
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=False,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-FIBERHOME-ZABBIX-TEST-STATUS",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.22",
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=300,
            is_active=True,
        )
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10087"),
            patch.object(
                service,
                "get_items_by_keys",
                return_value={
                    "onuStatusValue[1]": {"lastvalue": "online", "lastclock": "1710000010"},
                    "onuStatusValue[2]": {"lastvalue": "link_loss", "lastclock": "1710000011"},
                    "onuStatusValue[3]": {"lastvalue": "dying_gasp", "lastclock": "1710000012"},
                    "onuStatusValue[4]": {"lastvalue": "offline", "lastclock": "1710000013"},
                    "onuDisconnectReason[4]": {"lastvalue": "link_loss", "lastclock": "1710000013"},
                },
            ),
        ):
            rows, read_at = service.fetch_status_by_index(
                olt,
                ["1", "2", "3", "4"],
                status_item_key_pattern="onuStatusValue[{index}]",
                reason_item_key_pattern="onuDisconnectReason[{index}]",
            )

        self.assertEqual(rows.get("1"), {"status": "online", "reason": ""})
        self.assertEqual(rows.get("2"), {"status": "offline", "reason": "link_loss"})
        self.assertEqual(rows.get("3"), {"status": "offline", "reason": "dying_gasp"})
        self.assertEqual(rows.get("4"), {"status": "offline", "reason": "link_loss"})
        self.assertTrue(read_at)

    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_collect_power_persists_recent_zabbix_readings(self, fetch_power_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        self.olt.last_poll_at = timezone.now()
        self.olt.save(update_fields=["last_poll_at"])

        read_at = timezone.now() - timedelta(minutes=10)
        fetch_power_mock.return_value = (
            {
                "11.3": {
                    "onu_rx_power": -19.5,
                    "olt_rx_power": -23.1,
                    "power_read_at": read_at.isoformat(),
                }
            },
            read_at.isoformat(),
        )

        payload = collect_power_for_olt(self.olt, force_refresh=True, include_results=False)
        self.assertEqual(payload.get("stored_count"), 1)

        sample = ONUPowerSample.objects.get(onu=onu)
        self.assertEqual(sample.onu_rx_power, -19.5)
        self.assertEqual(sample.olt_rx_power, -23.1)

    def test_sync_olt_interval_macros_creates_missing_macros(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_interval_macros(self.olt)

        self.assertTrue(synced)
        create_calls = [params for method, params in api_calls if method == "usermacro.create"]
        expected = {
            VARUNA_DISCOVERY_INTERVAL_MACRO: "3600s",
            VARUNA_STATUS_INTERVAL_MACRO: "300s",
            VARUNA_POWER_INTERVAL_MACRO: "300s",
            VARUNA_AVAILABILITY_INTERVAL_MACRO: "30s",
            VARUNA_HISTORY_DAYS_MACRO: "7d",
        }
        self.assertEqual(len(create_calls), 5)
        for call in create_calls:
            self.assertEqual(call.get("hostid"), "10090")
            macro = call.get("macro")
            self.assertEqual(call.get("value"), expected.get(macro))

    def test_sync_olt_interval_macros_updates_changed_values_only(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "usermacro.get":
                return [
                    {"hostmacroid": "11", "macro": VARUNA_DISCOVERY_INTERVAL_MACRO, "value": "1200s"},
                    {"hostmacroid": "12", "macro": VARUNA_STATUS_INTERVAL_MACRO, "value": "300s"},
                    {"hostmacroid": "13", "macro": VARUNA_POWER_INTERVAL_MACRO, "value": "1200s"},
                    {"hostmacroid": "14", "macro": VARUNA_AVAILABILITY_INTERVAL_MACRO, "value": "60s"},
                    {"hostmacroid": "15", "macro": VARUNA_HISTORY_DAYS_MACRO, "value": "30d"},
                ]
            return {}

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_interval_macros(self.olt)

        self.assertTrue(synced)
        updates = [params for method, params in api_calls if method == "usermacro.update"]
        self.assertEqual(
            updates,
            [
                {"hostmacroid": "11", "value": "3600s"},
                {"hostmacroid": "13", "value": "300s"},
                {"hostmacroid": "14", "value": "30s"},
                {"hostmacroid": "15", "value": "7d"},
            ],
        )

    @override_settings(ZABBIX_HOST_NAME_PREFIX="")
    def test_sync_olt_host_runtime_updates_host_interface_and_macros(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9001",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": "10.0.0.99",
                        "dns": "",
                        "port": "162",
                        "details": {
                            "version": "2",
                            "community": "{$SNMP_COMMUNITY}",
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return [
                    {"hostmacroid": "201", "macro": "{$SNMP_COMMUNITY}", "value": "old-community"},
                    {"hostmacroid": "202", "macro": VARUNA_DISCOVERY_INTERVAL_MACRO, "value": "1200s"},
                    {"hostmacroid": "203", "macro": VARUNA_STATUS_INTERVAL_MACRO, "value": "300s"},
                    {"hostmacroid": "204", "macro": VARUNA_POWER_INTERVAL_MACRO, "value": "1200s"},
                    {"hostmacroid": "205", "macro": VARUNA_AVAILABILITY_INTERVAL_MACRO, "value": "60s"},
                    {"hostmacroid": "206", "macro": VARUNA_HISTORY_DAYS_MACRO, "value": "30d"},
                    {"hostmacroid": "207", "macro": VARUNA_SNMP_IP_MACRO, "value": "10.0.0.99"},
                    {"hostmacroid": "208", "macro": VARUNA_SNMP_PORT_MACRO, "value": "162"},
                ]
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": "OLT-OLD", "name": "OLT-OLD"},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)

        host_update_calls = [params for method, params in api_calls if method == "host.update"]
        self.assertEqual(
            host_update_calls,
            [{"hostid": "10090", "host": self.olt.name, "name": self.olt.name}],
        )

        iface_update_calls = [params for method, params in api_calls if method == "hostinterface.update"]
        self.assertEqual(len(iface_update_calls), 1)
        iface_payload = iface_update_calls[0]
        self.assertEqual(iface_payload.get("interfaceid"), "9001")
        self.assertEqual(iface_payload.get("ip"), VARUNA_SNMP_IP_MACRO)
        self.assertEqual(iface_payload.get("port"), VARUNA_SNMP_PORT_MACRO)
        self.assertEqual((iface_payload.get("details") or {}).get("community"), VARUNA_SNMP_COMMUNITY_MACRO)

        macro_updates = [params for method, params in api_calls if method == "usermacro.update"]
        expected_updates = {
            "202": "3600s",
            "204": "300s",
            "205": "30s",
            "206": "7d",
            "207": self.olt.ip_address,
            "208": str(self.olt.snmp_port),
        }
        self.assertEqual(len(macro_updates), 6)
        for payload in macro_updates:
            self.assertEqual(payload.get("value"), expected_updates.get(payload.get("hostmacroid")))
        self.assertNotIn({"hostmacroid": "203", "value": "300s"}, macro_updates)

        macro_creates = [params for method, params in api_calls if method == "usermacro.create"]
        self.assertEqual(len(macro_creates), 1)
        self.assertEqual(
            macro_creates[0],
            {"hostid": "10090", "macro": VARUNA_SNMP_COMMUNITY_MACRO, "value": self.olt.snmp_community},
        )

    def test_sync_olt_host_runtime_rewrites_hardcoded_interface_community(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9003",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": self.olt.ip_address,
                        "dns": "",
                        "port": str(self.olt.snmp_port),
                        "details": {
                            "version": "2",
                            "community": "adsl",
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        iface_update_calls = [params for method, params in api_calls if method == "hostinterface.update"]
        self.assertEqual(len(iface_update_calls), 1)
        iface_payload = iface_update_calls[0]
        self.assertEqual(iface_payload.get("ip"), VARUNA_SNMP_IP_MACRO)
        self.assertEqual(iface_payload.get("port"), VARUNA_SNMP_PORT_MACRO)
        self.assertEqual((iface_payload.get("details") or {}).get("community"), VARUNA_SNMP_COMMUNITY_MACRO)

    def test_sync_olt_host_runtime_updates_varuna_host_tags(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                return [{"groupid": "301", "name": "OLT"}]
            if method == "host.get":
                if params.get("hostids"):
                    return [
                        {
                            "hostid": "10090",
                            "hostgroups": [{"groupid": "301", "name": "OLT"}],
                            "tags": [
                                {"tag": "source", "value": "legacy"},
                                {"tag": "vendor", "value": "OldVendor"},
                                {"tag": "model", "value": "OldModel"},
                                {"tag": "site", "value": "bsj"},
                            ],
                        }
                    ]
                return []
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9005",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": VARUNA_SNMP_IP_MACRO,
                        "dns": "",
                        "port": VARUNA_SNMP_PORT_MACRO,
                        "details": {
                            "version": "2",
                            "community": VARUNA_SNMP_COMMUNITY_MACRO,
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        tag_updates = [
            params
            for method, params in api_calls
            if method == "host.update" and isinstance(params, dict) and "tags" in params
        ]
        self.assertEqual(len(tag_updates), 1)
        tags = tag_updates[0].get("tags") or []
        self.assertIn({"tag": "site", "value": "bsj"}, tags)
        self.assertIn({"tag": VARUNA_HOST_TAG_SOURCE, "value": VARUNA_HOST_TAG_SOURCE_VALUE}, tags)
        self.assertIn({"tag": VARUNA_HOST_TAG_VENDOR, "value": "huawei"}, tags)
        self.assertIn({"tag": VARUNA_HOST_TAG_MODEL, "value": self.vendor.model_name.lower()}, tags)

    def test_sync_olt_host_runtime_maps_unificado_tag_to_unified(self):
        self.vendor.model_name = "UNIFICADO"
        self.olt.vendor_profile = self.vendor

        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                return [{"groupid": "301", "name": "OLT"}]
            if method == "host.get":
                if params.get("hostids"):
                    return [
                        {
                            "hostid": "10090",
                            "hostgroups": [{"groupid": "301", "name": "OLT"}],
                            "tags": [
                                {"tag": "source", "value": "varuna"},
                                {"tag": "vendor", "value": "huawei"},
                                {"tag": "model", "value": "unificado"},
                            ],
                        }
                    ]
                return []
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9005",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": VARUNA_SNMP_IP_MACRO,
                        "dns": "",
                        "port": VARUNA_SNMP_PORT_MACRO,
                        "details": {
                            "version": "2",
                            "community": VARUNA_SNMP_COMMUNITY_MACRO,
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        tag_updates = [
            params
            for method, params in api_calls
            if method == "host.update" and isinstance(params, dict) and "tags" in params
        ]
        self.assertEqual(len(tag_updates), 1)
        tags = tag_updates[0].get("tags") or []
        self.assertIn({"tag": VARUNA_HOST_TAG_MODEL, "value": "unified"}, tags)
        self.assertNotIn({"tag": VARUNA_HOST_TAG_MODEL, "value": "unificado"}, tags)

    @override_settings(
        ZABBIX_HOST_GROUP_NAME="OLT",
        ZABBIX_HOST_GROUP_LEGACY_NAMES=("OLT", "OLTs"),
        ZABBIX_HOST_NAME_PREFIX="",
    )
    def test_sync_olt_host_runtime_creates_host_when_missing(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                names = ((params or {}).get("filter") or {}).get("name") or []
                if "OLT" in names:
                    return [{"groupid": "301", "name": "OLT"}]
                return []
            if method == "template.get":
                hosts = ((params or {}).get("filter") or {}).get("host") or []
                if "Template OLT Huawei" in hosts:
                    return [{"templateid": "7001", "host": "Template OLT Huawei", "name": "Template OLT Huawei"}]
                if "Varuna SNMP Availability" in hosts:
                    return [{"templateid": "7999", "host": "Varuna SNMP Availability", "name": "Varuna SNMP Availability"}]
                if "SNMP Avail" in hosts:
                    return [{"templateid": "7999", "host": "SNMP Avail", "name": "SNMP Avail"}]
                return []
            if method == "host.create":
                return {"hostids": ["10095"]}
            if method == "usermacro.get":
                return []
            if method == "host.get":
                if params.get("filter"):
                    return []
                if params.get("hostids") == ["10095"]:
                    return [{"hostid": "10095", "host": self.olt.name, "name": self.olt.name}]
                return []
            if method == "hostinterface.get":
                if params.get("hostids") == ["10095"]:
                    return [
                        {
                            "interfaceid": "9010",
                            "type": "2",
                            "main": "1",
                            "useip": "1",
                            "ip": VARUNA_SNMP_IP_MACRO,
                            "dns": "",
                            "port": VARUNA_SNMP_PORT_MACRO,
                            "details": {
                                "version": "2",
                                "community": VARUNA_SNMP_COMMUNITY_MACRO,
                                "bulk": "1",
                            },
                        }
                    ]
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                side_effect=[None, {"hostid": "10095", "host": self.olt.name, "name": self.olt.name}],
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        host_create_calls = [params for method, params in api_calls if method == "host.create"]
        self.assertEqual(len(host_create_calls), 1)
        self.assertEqual(host_create_calls[0].get("host"), self.olt.name)
        self.assertEqual(host_create_calls[0].get("name"), self.olt.name)
        self.assertEqual(host_create_calls[0].get("groups"), [{"groupid": "301"}])
        self.assertEqual(
            (host_create_calls[0].get("interfaces") or [{}])[0].get("ip"),
            VARUNA_SNMP_IP_MACRO,
        )
        self.assertEqual(
            (host_create_calls[0].get("interfaces") or [{}])[0].get("port"),
            VARUNA_SNMP_PORT_MACRO,
        )
        template_ids = {
            str((row or {}).get("templateid") or "")
            for row in (host_create_calls[0].get("templates") or [])
        }
        self.assertSetEqual(template_ids, {"7001", "7999"})

    def test_delete_olt_host_uses_host_delete(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "host.get":
                if ((params or {}).get("filter") or {}).get("host"):
                    return [{"hostid": "10090", "host": self.olt.name, "name": self.olt.name}]
                return []
            return {}

        with patch.object(service, "_call", side_effect=_fake_call):
            deleted = service.delete_olt_host(self.olt)

        self.assertTrue(deleted)
        delete_calls = [params for method, params in api_calls if method == "host.delete"]
        self.assertEqual(delete_calls, [["10090"]])

    def test_resolve_host_recovers_from_stale_cached_hostid(self):
        service = ZabbixService()
        api_calls = []

        cache_key = service._cache_key_for_olt(self.olt)
        service._host_cache[cache_key] = {
            "hostid": "99999",
            "host": self.olt.name,
            "name": self.olt.name,
            "status": "0",
        }

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "host.get":
                if params.get("hostids") == ["99999"]:
                    return []
                host_filter = ((params or {}).get("filter") or {}).get("host") or []
                if self.olt.name in host_filter:
                    return [{"hostid": "10090", "host": self.olt.name, "name": self.olt.name, "status": "0"}]
                return []
            return {}

        with patch.object(service, "_call", side_effect=_fake_call):
            host = service.resolve_host(self.olt)

        self.assertIsNotNone(host)
        self.assertEqual(str((host or {}).get("hostid")), "10090")
        self.assertEqual(str((service._host_cache.get(cache_key) or {}).get("hostid")), "10090")
        # Ensure we first probed cached hostid, then recovered by host name.
        self.assertEqual(api_calls[0][0], "host.get")
        self.assertEqual((api_calls[0][1] or {}).get("hostids"), ["99999"])

    @override_settings(ZABBIX_HOST_NAME_PREFIX="GabSA-")
    def test_resolve_host_candidate_names_include_prefixed_and_plain(self):
        service = ZabbixService()
        names = service._resolve_host_candidate_names(self.olt)
        self.assertIn(self.olt.name, names)
        self.assertIn(f"GabSA-{self.olt.name}", names)

    @override_settings(
        ZABBIX_HOST_GROUP_NAME="OLT",
        ZABBIX_HOST_GROUP_LEGACY_NAMES=("OLT", "OLTs"),
        ZABBIX_HOST_NAME_PREFIX="GabSA-",
    )
    def test_sync_olt_host_runtime_applies_host_name_prefix(self):
        service = ZabbixService()
        api_calls = []
        expected_name = f"GabSA-{self.olt.name}"

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                names = ((params or {}).get("filter") or {}).get("name") or []
                if "OLT" in names:
                    return [{"groupid": "301", "name": "OLT"}]
                return []
            if method == "host.get":
                if params.get("hostids"):
                    return [
                        {
                            "hostid": "10090",
                            "hostgroups": [{"groupid": "301", "name": "OLT"}],
                            "tags": [],
                        }
                    ]
                return []
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9004",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": VARUNA_SNMP_IP_MACRO,
                        "dns": "",
                        "port": VARUNA_SNMP_PORT_MACRO,
                        "details": {
                            "version": "2",
                            "community": VARUNA_SNMP_COMMUNITY_MACRO,
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        host_name_updates = [
            params
            for method, params in api_calls
            if method == "host.update" and isinstance(params, dict) and "host" in params and "name" in params
        ]
        self.assertEqual(len(host_name_updates), 1)
        self.assertEqual(host_name_updates[0].get("host"), expected_name)
        self.assertEqual(host_name_updates[0].get("name"), expected_name)

    @override_settings(
        ZABBIX_HOST_GROUP_NAME="OLT",
        ZABBIX_HOST_GROUP_LEGACY_NAMES=("OLT", "OLTs"),
    )
    def test_sync_olt_host_runtime_migrates_legacy_host_group_olts_to_olt(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                names = ((params or {}).get("filter") or {}).get("name") or []
                if "OLT" in names:
                    return [{"groupid": "301", "name": "OLT"}]
                if "OLTs" in names:
                    return [{"groupid": "302", "name": "OLTs"}]
                return []
            if method == "host.get":
                if params.get("hostids"):
                    return [
                        {
                            "hostid": "10090",
                            "hostgroups": [
                                {"groupid": "302", "name": "OLTs"},
                                {"groupid": "88", "name": "Core"},
                            ],
                        }
                    ]
                return []
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9004",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": VARUNA_SNMP_IP_MACRO,
                        "dns": "",
                        "port": VARUNA_SNMP_PORT_MACRO,
                        "details": {
                            "version": "2",
                            "community": VARUNA_SNMP_COMMUNITY_MACRO,
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        group_updates = [
            params
            for method, params in api_calls
            if method == "host.update" and isinstance(params, dict) and "groups" in params
        ]
        self.assertEqual(len(group_updates), 1)
        self.assertEqual(
            group_updates[0].get("groups"),
            [{"groupid": "88"}, {"groupid": "301"}],
        )

    @override_settings(
        ZABBIX_HOST_GROUP_NAME="gabsat",
        ZABBIX_HOST_GROUP_LEGACY_NAMES=("OLT", "OLTs"),
    )
    def test_sync_olt_host_runtime_uses_configured_host_group_name(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "hostgroup.get":
                names = ((params or {}).get("filter") or {}).get("name") or []
                if "gabsat" in names:
                    return [{"groupid": "901", "name": "gabsat"}]
                if "OLT" in names:
                    return [{"groupid": "301", "name": "OLT"}]
                if "OLTs" in names:
                    return [{"groupid": "302", "name": "OLTs"}]
                return []
            if method == "host.get":
                if params.get("hostids"):
                    return [
                        {
                            "hostid": "10090",
                            "hostgroups": [
                                {"groupid": "302", "name": "OLTs"},
                                {"groupid": "88", "name": "Core"},
                            ],
                        }
                    ]
                return []
            if method == "hostinterface.get":
                return [
                    {
                        "interfaceid": "9004",
                        "type": "2",
                        "main": "1",
                        "useip": "1",
                        "ip": VARUNA_SNMP_IP_MACRO,
                        "dns": "",
                        "port": VARUNA_SNMP_PORT_MACRO,
                        "details": {
                            "version": "2",
                            "community": VARUNA_SNMP_COMMUNITY_MACRO,
                            "bulk": "1",
                        },
                    }
                ]
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(
                service,
                "resolve_host",
                return_value={"hostid": "10090", "host": self.olt.name, "name": self.olt.name},
            ),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(self.olt)

        self.assertTrue(synced)
        group_updates = [
            params
            for method, params in api_calls
            if method == "host.update" and isinstance(params, dict) and "groups" in params
        ]
        self.assertEqual(len(group_updates), 1)
        self.assertEqual(
            group_updates[0].get("groups"),
            [{"groupid": "88"}, {"groupid": "901"}],
        )

    @override_settings(ZABBIX_HOST_NAME_PREFIX="")
    def test_sync_olt_host_runtime_resolves_host_by_previous_name(self):
        service = ZabbixService()
        api_calls = []

        def _fake_call(method, params):
            api_calls.append((method, params))
            if method == "host.get":
                host_filter = ((params or {}).get("filter") or {}).get("host") or []
                if "OLT-OLD-NAME" in host_filter:
                    return [{"hostid": "10091", "host": "OLT-OLD-NAME", "name": "OLT-OLD-NAME"}]
                return []
            if method == "hostinterface.get":
                if params.get("hostids"):
                    return [
                        {
                            "interfaceid": "9002",
                            "type": "2",
                            "main": "1",
                            "useip": "1",
                            "ip": self.olt.ip_address,
                            "dns": "",
                            "port": str(self.olt.snmp_port),
                            "details": {
                                "version": "2",
                                "community": VARUNA_SNMP_COMMUNITY_MACRO,
                                "bulk": "1",
                            },
                        }
                    ]
                return []
            if method == "usermacro.get":
                return []
            return {}

        with (
            patch.object(service, "resolve_host", return_value=None),
            patch.object(service, "_call", side_effect=_fake_call),
        ):
            synced = service.sync_olt_host_runtime(
                self.olt,
                previous={"name": "OLT-OLD-NAME", "ip_address": "10.0.0.99"},
            )

        self.assertTrue(synced)
        host_updates = [params for method, params in api_calls if method == "host.update"]
        self.assertEqual(len(host_updates), 1)
        self.assertEqual(host_updates[0].get("host"), self.olt.name)

    @override_settings(ZABBIX_AVAILABILITY_STALE_SECONDS=45)
    def test_check_olt_reachability_uses_fresh_availability_item(self):
        service = ZabbixService()
        now_epoch = int(timezone.now().timestamp())

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(
                service,
                "get_single_item",
                side_effect=lambda hostid, key: (
                    {
                        "itemid": "8101",
                        "key_": key,
                        "status": "0",
                        "state": "0",
                        "error": "",
                        "lastclock": str(now_epoch - 10),
                        "lastvalue": "OLT-FH-CAS",
                    }
                    if key == DEFAULT_AVAILABILITY_ITEM_KEY else None
                ),
            ),
        ):
            reachable, detail = service.check_olt_reachability(self.olt)

        self.assertTrue(reachable)
        self.assertEqual(detail, "")

    def test_check_olt_reachability_marks_unreachable_when_availability_item_is_disabled(self):
        service = ZabbixService()

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(
                service,
                "get_single_item",
                return_value={
                    "itemid": "8101",
                    "key_": DEFAULT_AVAILABILITY_ITEM_KEY,
                    "status": "1",
                    "state": "0",
                    "error": "",
                    "lastclock": str(int(timezone.now().timestamp()) - 10),
                },
            ),
            patch.object(service, "execute_items_now") as execute_items_now_mock,
        ):
            reachable, detail = service.check_olt_reachability(self.olt)

        self.assertFalse(reachable)
        self.assertIn("disabled", detail.lower())
        execute_items_now_mock.assert_not_called()

    @override_settings(ZABBIX_AVAILABILITY_STALE_SECONDS=45)
    def test_check_olt_reachability_marks_unreachable_when_availability_item_stale(self):
        service = ZabbixService()
        now_epoch = int(timezone.now().timestamp())

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(
                service,
                "get_single_item",
                side_effect=lambda hostid, key: (
                    {
                        "itemid": "8101",
                        "key_": key,
                        "status": "0",
                        "state": "0",
                        "error": "",
                        "lastclock": str(now_epoch - 120),
                        "lastvalue": "OLT-FH-CAS",
                    }
                    if key == DEFAULT_AVAILABILITY_ITEM_KEY else None
                ),
            ),
            patch.object(service, "execute_items_now", return_value=0),
        ):
            reachable, detail = service.check_olt_reachability(self.olt)

        self.assertFalse(reachable)
        self.assertIn("availability", detail.lower())

    @override_settings(ZABBIX_AVAILABILITY_STALE_SECONDS=45)
    def test_check_olt_reachability_recovers_when_availability_refresh_becomes_fresh(self):
        service = ZabbixService()
        now_epoch = int(timezone.now().timestamp())
        sample_calls = {"count": 0}

        def _single_item(_hostid, key):
            if key != DEFAULT_AVAILABILITY_ITEM_KEY:
                return None
            sample_calls["count"] += 1
            if sample_calls["count"] == 1:
                return {
                    "itemid": "8101",
                    "key_": key,
                    "status": "0",
                    "state": "0",
                    "error": "",
                    "lastclock": str(now_epoch - 120),
                    "lastvalue": "OLT-FH-CAS",
                }
            return {
                "itemid": "8101",
                "key_": key,
                "status": "0",
                "state": "0",
                "error": "",
                "lastclock": str(now_epoch - 5),
                "lastvalue": "OLT-FH-CAS",
            }

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(service, "get_single_item", side_effect=_single_item),
            patch.object(service, "execute_items_now", return_value=1),
            patch("topology.services.zabbix_service.time.sleep", return_value=None),
        ):
            reachable, detail = service.check_olt_reachability(self.olt)

        self.assertTrue(reachable)
        self.assertEqual(detail, "")
        self.assertGreaterEqual(sample_calls["count"], 2)

    def test_check_olt_reachability_marks_unreachable_when_availability_has_no_samples(self):
        service = ZabbixService()

        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(
                service,
                "get_single_item",
                return_value={
                    "itemid": "8101",
                    "key_": DEFAULT_AVAILABILITY_ITEM_KEY,
                    "status": "0",
                    "state": "0",
                    "error": "",
                    "lastclock": "0",
                },
            ),
        ):
            reachable, detail = service.check_olt_reachability(self.olt)

        self.assertFalse(reachable)
        self.assertIn("no samples", detail.lower())

    @patch("topology.management.commands.run_scheduler.zabbix_service.check_olt_reachability")
    def test_scheduler_recovery_schedules_immediate_poll(self, check_reachability_mock):
        self.olt.snmp_reachable = False
        self.olt.snmp_failure_count = 3
        self.olt.last_snmp_check_at = None
        self.olt.next_poll_at = timezone.now() + timedelta(hours=1)
        self.olt.save(
            update_fields=["snmp_reachable", "snmp_failure_count", "last_snmp_check_at", "next_poll_at"]
        )

        check_reachability_mock.return_value = (True, "")
        command = SchedulerCommand()
        command._run_snmp_checks(base_interval_seconds=30, max_backoff_seconds=1800)

        self.olt.refresh_from_db()
        self.assertTrue(self.olt.snmp_reachable)
        self.assertEqual(self.olt.snmp_failure_count, 0)
        self.assertIsNotNone(self.olt.next_poll_at)
        self.assertLessEqual(
            abs((timezone.now() - self.olt.next_poll_at).total_seconds()),
            10,
        )

    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_uses_zabbix_timeline_source(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=101,
            snmp_index="11.101",
            serial="ABCD01010101",
            name="cliente-hist-zabbix",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        base_epoch = int(timezone.now().timestamp())
        fetch_timeline_mock.return_value = {
            "status_samples": [
                {"clock_epoch": base_epoch - 900, "clock": None, "value": "online"},
                {"clock_epoch": base_epoch - 600, "clock": None, "value": "link_loss"},
                {"clock_epoch": base_epoch - 300, "clock": None, "value": "online"},
                {"clock_epoch": base_epoch - 120, "clock": None, "value": "offline"},
            ],
            "status_previous": {"clock_epoch": base_epoch - 1200, "value": "online"},
            "reason_samples": [
                {"clock_epoch": base_epoch - 120, "clock": None, "value": "dying_gasp"},
            ],
            "onu_rx_samples": [
                {"clock_epoch": base_epoch - 300, "clock": None, "value": "-22.5"},
                {"clock_epoch": base_epoch - 120, "clock": None, "value": "-22.2"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": base_epoch - 300, "clock": None, "value": "-24.5"},
                {"clock_epoch": base_epoch - 120, "clock": None, "value": "-24.2"},
            ],
        }

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {
                "alarm_days": 7,
                "power_days": 7,
                "alarm_limit": 1000,
                "max_power_points": 744,
            },
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("source"), "zabbix")
        alarms = response.data.get("alarms") or []
        self.assertEqual(len(alarms), 2)
        # Desc order by start timestamp: active alarm first.
        self.assertEqual(alarms[0].get("status"), "active")
        self.assertEqual(alarms[0].get("event_type"), ONULog.REASON_DYING_GASP)
        self.assertEqual(alarms[1].get("status"), "resolved")
        self.assertEqual(alarms[1].get("event_type"), ONULog.REASON_LINK_LOSS)
        self.assertTrue((alarms[1].get("duration_seconds") or 0) > 0)

        stats = response.data.get("stats") or {}
        self.assertEqual(stats.get("total"), 2)
        self.assertEqual(stats.get("active"), 1)
        self.assertEqual(stats.get("resolved"), 1)
        self.assertEqual(stats.get("link_loss"), 1)
        self.assertEqual(stats.get("dying_gasp"), 1)

        power_history = response.data.get("power_history") or []
        self.assertEqual(len(power_history), 2)
        self.assertIsNotNone(power_history[0].get("onu_rx_power"))
        self.assertIsNotNone(power_history[0].get("olt_rx_power"))

    def test_alarm_clients_returns_hyphen_when_name_is_missing(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=106,
            snmp_index="11.106",
            serial="ABCD01020106",
            name="",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        request = self.api_factory.get(
            "/api/onu/alarm-clients/",
            {"search": "ABCD01020106", "limit": 7},
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_clients"})(request)

        self.assertEqual(response.status_code, 200)
        results = response.data.get("results") or []
        row = next((item for item in results if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("client_name"), "-")

    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_falls_back_to_local_source_when_zabbix_has_no_status(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=102,
            snmp_index="11.102",
            serial="ABCD01020102",
            name="cliente-hist-local",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now = timezone.now()
        log = ONULog.objects.create(
            onu=onu,
            offline_since=now - timedelta(minutes=10),
            offline_until=now - timedelta(minutes=5),
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=now - timedelta(minutes=11),
            disconnect_window_end=now - timedelta(minutes=10),
        )
        ONUPowerSample.objects.create(
            olt=onu.olt,
            onu=onu,
            slot_id=onu.slot_id,
            pon_id=onu.pon_id,
            onu_number=onu.onu_id,
            onu_rx_power=-21.3,
            olt_rx_power=-23.9,
            read_at=now - timedelta(minutes=6),
            source=ONUPowerSample.SOURCE_MANUAL,
        )
        fetch_timeline_mock.return_value = {}

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {
                "alarm_days": 7,
                "power_days": 7,
                "alarm_limit": 1000,
                "max_power_points": 744,
            },
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("source"), "varuna")
        alarms = response.data.get("alarms") or []
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].get("id"), log.id)
        self.assertEqual(alarms[0].get("event_type"), ONULog.REASON_LINK_LOSS)
        self.assertEqual(alarms[0].get("status"), "resolved")

        power_history = response.data.get("power_history") or []
        self.assertEqual(len(power_history), 1)
        self.assertEqual(power_history[0].get("onu_rx_power"), -21.3)

    def test_power_report_discards_sentinel_power_values(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=103,
            snmp_index="11.103",
            serial="ABCD01020103",
            name="cliente-power-sentinel",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        ONUPowerSample.objects.create(
            olt=onu.olt,
            onu=onu,
            slot_id=onu.slot_id,
            pon_id=onu.pon_id,
            onu_number=onu.onu_id,
            onu_rx_power=0.0,
            olt_rx_power=-40.0,
            read_at=timezone.now(),
            source=ONUPowerSample.SOURCE_MANUAL,
        )
        cache_service.delete(cache_service.get_onu_power_key(onu.olt_id, onu.id))

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row = next((item for item in rows if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertIsNone(row.get("onu_rx_power"))
        self.assertIsNone(row.get("olt_rx_power"))
        self.assertIsNone(row.get("power_read_at"))

    def test_power_report_discards_out_of_range_power_values(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=203,
            snmp_index="11.203",
            serial="ABCD01020203",
            name="cliente-power-range-guard",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        ONUPowerSample.objects.create(
            olt=onu.olt,
            onu=onu,
            slot_id=onu.slot_id,
            pon_id=onu.pon_id,
            onu_number=onu.onu_id,
            onu_rx_power=-80.0,
            olt_rx_power=1.0,
            read_at=timezone.now(),
            source=ONUPowerSample.SOURCE_MANUAL,
        )
        cache_service.delete(cache_service.get_onu_power_key(onu.olt_id, onu.id))

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row = next((item for item in rows if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertIsNone(row.get("onu_rx_power"))
        self.assertIsNone(row.get("olt_rx_power"))
        self.assertIsNone(row.get("power_read_at"))

    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_discards_sentinel_power_from_zabbix(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=104,
            snmp_index="11.104",
            serial="ABCD01020104",
            name="cliente-power-zabbix-sentinel",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now = timezone.now()
        epoch = int(now.timestamp())
        fetch_timeline_mock.return_value = {
            "status_samples": [
                {"clock_epoch": epoch - 120, "value": "1"},
                {"clock_epoch": epoch - 60, "value": "1"},
            ],
            "reason_samples": [],
            "status_previous": None,
            "onu_rx_samples": [
                {"clock_epoch": epoch - 60, "value": "0"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": epoch - 60, "value": "-40"},
            ],
        }

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {
                "alarm_days": 7,
                "power_days": 7,
                "alarm_limit": 1000,
                "max_power_points": 744,
            },
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("source"), "zabbix")
        self.assertEqual(response.data.get("power_history") or [], [])

    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_discards_out_of_range_power_from_zabbix(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=204,
            snmp_index="11.204",
            serial="ABCD01020204",
            name="cliente-power-zabbix-range-guard",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now = timezone.now()
        epoch = int(now.timestamp())
        fetch_timeline_mock.return_value = {
            "status_samples": [
                {"clock_epoch": epoch - 120, "value": "1"},
                {"clock_epoch": epoch - 60, "value": "1"},
            ],
            "reason_samples": [],
            "status_previous": None,
            "onu_rx_samples": [
                {"clock_epoch": epoch - 60, "value": "-80"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": epoch - 60, "value": "1"},
            ],
        }

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {
                "alarm_days": 7,
                "power_days": 7,
                "alarm_limit": 1000,
                "max_power_points": 744,
            },
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("source"), "zabbix")
        self.assertEqual(response.data.get("power_history") or [], [])

    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_merges_close_onu_and_olt_power_samples(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=105,
            snmp_index="11.105",
            serial="ABCD01020105",
            name="cliente-power-merge-window",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        now = timezone.now()
        epoch = int(now.timestamp())
        fetch_timeline_mock.return_value = {
            "status_samples": [
                {"clock_epoch": epoch - 180, "value": "online"},
                {"clock_epoch": epoch - 60, "value": "online"},
            ],
            "reason_samples": [],
            "status_previous": None,
            "onu_rx_samples": [
                {"clock_epoch": epoch - 60, "value": "-21.94"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": epoch - 54, "value": "-27.75"},
            ],
        }

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {
                "alarm_days": 7,
                "power_days": 7,
                "alarm_limit": 1000,
                "max_power_points": 744,
            },
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("source"), "zabbix")
        power_history = response.data.get("power_history") or []
        self.assertEqual(len(power_history), 1)
        self.assertEqual(power_history[0].get("onu_rx_power"), -21.94)
        self.assertEqual(power_history[0].get("olt_rx_power"), -27.75)
