import json

from unittest.mock import patch
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from topology.api.auth_views import me_view
from topology.api.serializers import OLTSerializer, VendorProfileSerializer
from topology.api.views import OLTViewSet, OLTPONViewSet, ONUViewSet
from topology.models import MaintenanceJob, OLT, OLTPON, OLTSlot, ONU, ONULog, ONUPowerSample, UserProfile, VendorProfile
from topology.services.cache_service import cache_service
from topology.services.fit_collector_service import FITCollectorError, _FITTelnetSession, fit_collector_service
from topology.services.history_service import persist_power_samples, sync_latest_power_snapshots
from topology.services.maintenance_job_service import maintenance_job_service
from topology.services.maintenance_runtime import (
    collect_power_for_olt,
    get_power_sync_interval_seconds,
    has_usable_status_snapshot,
)
from topology.services.unm_service import UNMService, UNMServiceError, _UNM_TIMEZONE_CACHE
from topology.services.power_service import power_service
from topology.services.vendor_profile import get_collector_transport
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
from topology.management.commands.run_scheduler import Command as SchedulerCommand, _is_power_due
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


class _FakeFITTelnet:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.writes = []
        self.closed = False

    def read_very_eager(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def write(self, value):
        self.writes.append(value)

    def close(self):
        self.closed = True


class ZabbixModeTests(TestCase):
    def setUp(self):
        _UNM_TIMEZONE_CACHE.clear()
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
        self.fit_vendor = VendorProfile.objects.get(vendor__iexact="FIT", model_name__iexact="FNCS4000")

    def _create_topology_onu(
        self,
        *,
        slot_id: int = 1,
        pon_id: int = 1,
        onu_id: int = 1,
        snmp_index: str = "11.1",
        serial: str = "ABCD00000001",
        name: str = "cliente-topologia",
        status: str = ONU.STATUS_ONLINE,
    ):
        slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=slot_id,
            slot_key=str(slot_id),
            is_active=True,
        )
        pon = OLTPON.objects.create(
            olt=self.olt,
            slot=slot,
            pon_id=pon_id,
            pon_key=f"{slot_id}/{pon_id}",
            is_active=True,
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=slot_id,
            pon_id=pon_id,
            onu_id=onu_id,
            snmp_index=snmp_index,
            serial=serial,
            name=name,
            status=status,
            is_active=True,
        )
        return slot, pon, onu

    def _create_fit_olt(self, *, name="OLT-FIT-TEST", ip_address="192.168.100.4"):
        return OLT.objects.create(
            name=name,
            vendor_profile=self.fit_vendor,
            protocol="telnet",
            ip_address=ip_address,
            snmp_port=161,
            snmp_community="public",
            snmp_version="v2c",
            telnet_username="bifrost",
            telnet_password="acaidosdeuses%gabisat",
            blade_ips=[{"ip": ip_address, "port": 23}],
            discovery_enabled=True,
            polling_enabled=True,
            discovery_interval_minutes=60,
            polling_interval_seconds=300,
            power_interval_seconds=1800,
            is_active=True,
        )

    def _set_fit_transport(self, transport: str):
        templates = json.loads(json.dumps(self.fit_vendor.oid_templates or {}))
        collector_cfg = templates.get("collector") if isinstance(templates.get("collector"), dict) else {}
        collector_cfg["transport"] = str(transport).strip().lower()
        templates["collector"] = collector_cfg
        self.fit_vendor.oid_templates = templates
        self.fit_vendor.save(update_fields=["oid_templates"])

    def _create_fit_onu(
        self,
        fit_olt,
        *,
        pon_id=1,
        onu_id=1,
        status=ONU.STATUS_UNKNOWN,
        name="",
        serial="",
    ):
        slot, _ = OLTSlot.objects.get_or_create(
            olt=fit_olt,
            slot_id=1,
            slot_key="1",
            defaults={"is_active": True},
        )
        pon, _ = OLTPON.objects.get_or_create(
            olt=fit_olt,
            slot=slot,
            pon_id=pon_id,
            pon_key=f"1/{pon_id}",
            defaults={"is_active": True},
        )
        return ONU.objects.create(
            olt=fit_olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=1,
            pon_id=pon_id,
            onu_id=onu_id,
            snmp_index=f"0/{pon_id}:{onu_id}",
            serial=serial,
            name=name,
            status=status,
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
        self.olt.collector_reachable = True
        self.olt.save(update_fields=["last_poll_at", "collector_reachable"])
        self.assertTrue(has_usable_status_snapshot(self.olt))

        self.olt.collector_reachable = False
        self.olt.save(update_fields=["collector_reachable"])
        self.assertFalse(has_usable_status_snapshot(self.olt))

        self.olt.collector_reachable = True
        self.olt.last_poll_at = timezone.now() - timedelta(minutes=8)
        self.olt.save(update_fields=["collector_reachable", "last_poll_at"])
        self.assertTrue(has_usable_status_snapshot(self.olt))

        self.olt.collector_reachable = True
        self.olt.last_poll_at = timezone.now() - timedelta(minutes=20)
        self.olt.save(update_fields=["collector_reachable", "last_poll_at"])
        self.assertFalse(has_usable_status_snapshot(self.olt))

    def test_seeded_zte_c600_profile_has_expected_status_map(self):
        profile = VendorProfile.objects.get(vendor__iexact="zte", model_name__iexact="C600")
        zabbix_cfg = (profile.oid_templates or {}).get("zabbix", {})
        status_map = (profile.oid_templates or {}).get("status", {}).get("status_map", {})

        self.assertEqual(zabbix_cfg.get("host_template_name"), "OLT ZTE C600")
        self.assertEqual(status_map.get("1"), {"status": "offline", "reason": "link_loss"})
        self.assertEqual(status_map.get("2"), {"status": "offline", "reason": "link_loss"})
        self.assertEqual(status_map.get("3"), {"status": "online"})
        self.assertEqual(status_map.get("4"), {"status": "online"})
        self.assertEqual(status_map.get("5"), {"status": "offline", "reason": "dying_gasp"})
        self.assertEqual(status_map.get("6"), {"status": "offline", "reason": "unknown"})
        self.assertEqual(status_map.get("7"), {"status": "offline", "reason": "unknown"})

    def test_me_view_exposes_admin_only_settings_and_operator_topology_permissions(self):
        admin_user = User.objects.create_user(username="admin-role", password="admin-role")
        UserProfile.objects.create(user=admin_user, role=UserProfile.ROLE_ADMIN)
        operator_user = User.objects.create_user(username="operator-role", password="operator-role")
        UserProfile.objects.create(user=operator_user, role=UserProfile.ROLE_OPERATOR)

        admin_request = self.api_factory.get("/api/auth/me/")
        force_authenticate(admin_request, user=admin_user)
        admin_response = me_view(admin_request)

        operator_request = self.api_factory.get("/api/auth/me/")
        force_authenticate(operator_request, user=operator_user)
        operator_response = me_view(operator_request)

        self.assertEqual(admin_response.status_code, 200)
        self.assertTrue(admin_response.data.get("can_modify_settings"))
        self.assertTrue(admin_response.data.get("can_operate_topology"))

        self.assertEqual(operator_response.status_code, 200)
        self.assertEqual(operator_response.data.get("role"), UserProfile.ROLE_OPERATOR)
        self.assertFalse(operator_response.data.get("can_modify_settings"))
        self.assertTrue(operator_response.data.get("can_operate_topology"))

    def test_operator_can_update_pon_description(self):
        operator_user = User.objects.create_user(username="operator-pon", password="operator-pon")
        UserProfile.objects.create(user=operator_user, role=UserProfile.ROLE_OPERATOR)

        slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=1,
            slot_key="1",
            is_active=True,
        )
        pon = OLTPON.objects.create(
            olt=self.olt,
            slot=slot,
            pon_id=1,
            pon_key="1/1",
            description="before",
            is_active=True,
        )

        request = self.api_factory.patch(
            f"/api/pons/{pon.id}/",
            {"description": "after", "name": "renamed"},
            format="json",
        )
        force_authenticate(request, user=operator_user)
        response = OLTPONViewSet.as_view({"patch": "partial_update"})(request, pk=str(pon.id))

        self.assertEqual(response.status_code, 200)
        pon.refresh_from_db()
        self.assertEqual(pon.name, "")
        self.assertEqual(pon.description, "after")

    @patch("topology.api.views.persist_power_samples", return_value=1)
    @patch("topology.api.views.sync_latest_power_snapshots", return_value=1)
    @patch("topology.api.views.power_service.refresh_for_onus")
    @patch.object(ONUViewSet, "_has_usable_status_snapshot", return_value=True)
    @patch.object(ONUViewSet, "_run_scoped_status_refresh")
    def test_operator_can_refresh_scoped_status_and_power(
        self,
        run_scoped_status_refresh_mock,
        has_usable_status_snapshot_mock,
        refresh_for_onus_mock,
        sync_latest_power_snapshots_mock,
        persist_power_samples_mock,
    ):
        operator_user = User.objects.create_user(username="operator-refresh", password="operator-refresh")
        UserProfile.objects.create(user=operator_user, role=UserProfile.ROLE_OPERATOR)

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
        refresh_for_onus_mock.return_value = {
            onu.id: {
                "onu_id": onu.id,
                "slot_id": onu.slot_id,
                "pon_id": onu.pon_id,
                "onu_number": onu.onu_id,
                "onu_rx_power": -19.5,
                "olt_rx_power": -23.1,
                "power_read_at": timezone.now().isoformat(),
            }
        }

        status_request = self.api_factory.post(
            "/api/onu/batch-status/",
            {
                "olt_id": self.olt.id,
                "slot_id": onu.slot_id,
                "pon_id": onu.pon_id,
                "refresh": True,
            },
            format="json",
        )
        force_authenticate(status_request, user=operator_user)
        status_response = ONUViewSet.as_view({"post": "batch_status"})(status_request)

        power_request = self.api_factory.post(
            "/api/onu/batch-power/",
            {
                "olt_id": self.olt.id,
                "slot_id": onu.slot_id,
                "pon_id": onu.pon_id,
                "refresh": True,
            },
            format="json",
        )
        force_authenticate(power_request, user=operator_user)
        power_response = ONUViewSet.as_view({"post": "batch_power"})(power_request)

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(power_response.status_code, 200)
        run_scoped_status_refresh_mock.assert_called_once()
        has_usable_status_snapshot_mock.assert_called()
        refresh_for_onus_mock.assert_called_once()
        self.assertTrue(refresh_for_onus_mock.call_args.kwargs.get("refresh_upstream"))
        self.assertTrue(refresh_for_onus_mock.call_args.kwargs.get("force_upstream"))
        sync_latest_power_snapshots_mock.assert_called_once()
        persist_power_samples_mock.assert_called_once()

    def test_viewer_cannot_update_pon_description_or_refresh_topology_actions(self):
        viewer_user = User.objects.create_user(username="viewer-role", password="viewer-role")
        UserProfile.objects.create(user=viewer_user, role=UserProfile.ROLE_VIEWER)

        slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=1,
            slot_key="1",
            is_active=True,
        )
        pon = OLTPON.objects.create(
            olt=self.olt,
            slot=slot,
            pon_id=1,
            pon_key="1/1",
            description="before",
            is_active=True,
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index="11.1",
            serial="ABCD00000001",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        patch_request = self.api_factory.patch(
            f"/api/pons/{pon.id}/",
            {"description": "forbidden"},
            format="json",
        )
        force_authenticate(patch_request, user=viewer_user)
        patch_response = OLTPONViewSet.as_view({"patch": "partial_update"})(patch_request, pk=str(pon.id))

        refresh_request = self.api_factory.post(
            "/api/onu/batch-status/",
            {
                "olt_id": self.olt.id,
                "slot_id": 1,
                "pon_id": 1,
                "refresh": True,
            },
            format="json",
        )
        force_authenticate(refresh_request, user=viewer_user)
        refresh_response = ONUViewSet.as_view({"post": "batch_status"})(refresh_request)

        power_request = self.api_factory.get(f"/api/onu/{onu.id}/power/?refresh=true")
        force_authenticate(power_request, user=viewer_user)
        power_response = ONUViewSet.as_view({"get": "power"})(power_request, pk=str(onu.id))

        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(refresh_response.status_code, 403)
        self.assertEqual(power_response.status_code, 403)

    def test_olt_list_include_topology_uses_cached_structure_and_live_status(self):
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=19,
            snmp_index="28.19",
            serial="TPLG7E769D78",
            name="cliente-original",
            status=ONU.STATUS_ONLINE,
        )

        first_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(first_request, user=self.user)
        first_response = OLTViewSet.as_view({"get": "list"})(first_request)

        self.assertEqual(first_response.status_code, 200)
        cached_structure = cache_service.get_topology_structure(self.olt.id)
        self.assertIsNotNone(cached_structure)
        cached_structure["slots"]["2"]["pons"]["2/8"]["description"] = "descricao-cache"
        cached_structure["slots"]["2"]["pons"]["2/8"]["onus"][0]["name"] = "cliente-cache"
        cached_structure["slots"]["2"]["pons"]["2/8"]["onus"][0]["client_name"] = "cliente-cache"
        cache_service.set_topology_structure(self.olt.id, cached_structure, ttl=3600)

        offline_since = timezone.now() - timedelta(minutes=3)
        ONU.objects.filter(id=onu.id).update(status=ONU.STATUS_OFFLINE)
        ONULog.objects.create(
            onu=onu,
            offline_since=offline_since,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=offline_since,
            disconnect_window_end=offline_since,
        )

        second_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(second_request, user=self.user)
        second_response = OLTViewSet.as_view({"get": "list"})(second_request)

        self.assertEqual(second_response.status_code, 200)
        rows = second_response.data.get("results") if isinstance(second_response.data, dict) else second_response.data
        olt_row = next((row for row in (rows or []) if row.get("id") == self.olt.id), None)
        self.assertIsNotNone(olt_row)
        onu_row = olt_row["slots"][0]["pons"][0]["onus"][0]
        self.assertEqual(onu_row.get("name"), "cliente-cache")
        self.assertEqual(olt_row["slots"][0]["pons"][0].get("description"), "descricao-cache")
        self.assertEqual(onu_row.get("status"), ONU.STATUS_OFFLINE)
        self.assertEqual(onu_row.get("disconnect_reason"), ONULog.REASON_LINK_LOSS)
        self.assertIsNone(onu_row.get("onu_rx_power"))
        self.assertIsNone(onu_row.get("olt_rx_power"))
        self.assertIsNone(onu_row.get("power_read_at"))

    def test_olt_list_include_topology_ignores_runtime_power_cache(self):
        self.vendor.oid_templates = {
            **(self.vendor.oid_templates or {}),
            "power": {"olt_rx_oid": "1.2.3"},
        }
        self.vendor.save(update_fields=["oid_templates"])
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=22,
            snmp_index="28.22",
            serial="TPLG89441338",
        )
        cache_service.set_many_onu_power(
            self.olt.id,
            {
                onu.id: {
                    "onu_id": onu.id,
                    "slot_id": onu.slot_id,
                    "pon_id": onu.pon_id,
                    "onu_number": onu.onu_id,
                    "onu_rx_power": -19.4,
                    "olt_rx_power": -23.3,
                    "power_read_at": timezone.now().isoformat(),
                }
            },
            ttl=3600,
        )

        request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") if isinstance(response.data, dict) else response.data
        olt_row = next((row for row in (rows or []) if row.get("id") == self.olt.id), None)
        self.assertIsNotNone(olt_row)
        onu_row = olt_row["slots"][0]["pons"][0]["onus"][0]
        self.assertIsNone(onu_row.get("onu_rx_power"))
        self.assertIsNone(onu_row.get("olt_rx_power"))
        self.assertIsNone(onu_row.get("power_read_at"))

    def test_olt_list_include_topology_rolls_up_parent_status_from_child_states(self):
        slot_one, pon_one, _ = self._create_topology_onu(
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index="11.1",
            serial="TPLG00000001",
            status=ONU.STATUS_ONLINE,
        )
        offline_slot_1_pon_1 = ONU.objects.create(
            olt=self.olt,
            slot_ref=slot_one,
            pon_ref=pon_one,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index="11.2",
            serial="TPLG00000002",
            name="cliente-topologia",
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        pon_two = OLTPON.objects.create(
            olt=self.olt,
            slot=slot_one,
            pon_id=2,
            pon_key="1/2",
            is_active=True,
        )
        ONU.objects.create(
            olt=self.olt,
            slot_ref=slot_one,
            pon_ref=pon_two,
            slot_id=1,
            pon_id=2,
            onu_id=1,
            snmp_index="12.1",
            serial="TPLG00000003",
            name="cliente-topologia",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        slot_two = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=2,
            slot_key="2",
            is_active=True,
        )
        pon_three = OLTPON.objects.create(
            olt=self.olt,
            slot=slot_two,
            pon_id=1,
            pon_key="2/1",
            is_active=True,
        )
        offline_slot_2_pon_1 = ONU.objects.create(
            olt=self.olt,
            slot_ref=slot_two,
            pon_ref=pon_three,
            slot_id=2,
            pon_id=1,
            onu_id=1,
            snmp_index="21.1",
            serial="TPLG00000004",
            name="cliente-topologia",
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )

        offline_since = timezone.now() - timedelta(minutes=5)
        ONULog.objects.create(
            onu=offline_slot_1_pon_1,
            offline_since=offline_since,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=offline_since,
            disconnect_window_end=offline_since,
        )
        ONULog.objects.create(
            onu=offline_slot_2_pon_1,
            offline_since=offline_since,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=offline_since,
            disconnect_window_end=offline_since,
        )

        request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") if isinstance(response.data, dict) else response.data
        olt_row = next((row for row in (rows or []) if row.get("id") == self.olt.id), None)
        self.assertIsNotNone(olt_row)

        slot_one = next((slot for slot in olt_row["slots"] if int(slot["slot_number"]) == 1), None)
        slot_two = next((slot for slot in olt_row["slots"] if int(slot["slot_number"]) == 2), None)
        self.assertIsNotNone(slot_one)
        self.assertIsNotNone(slot_two)

        pon_one = next((pon for pon in slot_one["pons"] if int(pon["pon_number"]) == 1), None)
        pon_two = next((pon for pon in slot_one["pons"] if int(pon["pon_number"]) == 2), None)
        self.assertEqual(pon_one["status"], "partial")
        self.assertEqual(pon_two["status"], "online")
        self.assertEqual(slot_one["status"], "online")
        self.assertEqual(slot_two["status"], "offline")
        self.assertEqual(olt_row["status"], "partial")

    def test_olt_topology_detail_uses_cached_structure_and_live_status(self):
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=20,
            snmp_index="28.20",
            serial="TPLG2D2F2938",
            name="cliente-topologia",
            status=ONU.STATUS_ONLINE,
        )

        first_request = self.api_factory.get(f"/api/olts/{self.olt.id}/topology/")
        force_authenticate(first_request, user=self.user)
        first_response = OLTViewSet.as_view({"get": "topology"})(first_request, pk=str(self.olt.id))
        self.assertEqual(first_response.status_code, 200)

        cached_structure = cache_service.get_topology_structure(self.olt.id)
        self.assertIsNotNone(cached_structure)
        cached_structure["slots"]["2"]["pons"]["2/8"]["onus"][0]["serial"] = "SERIAL-CACHE"
        cached_structure["slots"]["2"]["pons"]["2/8"]["onus"][0]["serial_number"] = "SERIAL-CACHE"
        cache_service.set_topology_structure(self.olt.id, cached_structure, ttl=3600)

        offline_since = timezone.now() - timedelta(minutes=2)
        ONU.objects.filter(id=onu.id).update(status=ONU.STATUS_OFFLINE)
        ONULog.objects.create(
            onu=onu,
            offline_since=offline_since,
            disconnect_reason=ONULog.REASON_DYING_GASP,
            disconnect_window_start=offline_since,
            disconnect_window_end=offline_since,
        )

        second_request = self.api_factory.get(f"/api/olts/{self.olt.id}/topology/")
        force_authenticate(second_request, user=self.user)
        second_response = OLTViewSet.as_view({"get": "topology"})(second_request, pk=str(self.olt.id))

        self.assertEqual(second_response.status_code, 200)
        onu_row = second_response.data["slots"]["2"]["pons"]["2/8"]["onus"][0]
        self.assertEqual(onu_row.get("serial"), "SERIAL-CACHE")
        self.assertEqual(onu_row.get("status"), ONU.STATUS_OFFLINE)
        self.assertEqual(onu_row.get("disconnect_reason"), ONULog.REASON_DYING_GASP)
        self.assertIsNone(onu_row.get("onu_rx_power"))
        self.assertIsNone(onu_row.get("olt_rx_power"))
        self.assertIsNone(onu_row.get("power_read_at"))

    @patch("topology.services.topology_service.unm_service.localize_alarm_datetime")
    @patch("topology.services.topology_service.unm_service.is_enabled_for_olt", return_value=True)
    def test_olt_topology_detail_uses_unm_source_clock_for_disconnect_window(
        self,
        _is_enabled_mock,
        localize_alarm_datetime_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=24,
            snmp_index="28.24",
            serial="TPLGUNM00024",
            name="cliente-unm-topology",
            status=ONU.STATUS_OFFLINE,
        )

        raw_utc = datetime(2026, 3, 9, 14, 2, 36, tzinfo=dt_timezone.utc)
        source_clock = datetime(2026, 3, 9, 11, 2, 36, tzinfo=dt_timezone(timedelta(hours=-3)))
        localize_alarm_datetime_mock.side_effect = lambda *, olt, value: source_clock
        ONULog.objects.create(
            onu=onu,
            offline_since=raw_utc,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=raw_utc,
            disconnect_window_end=raw_utc,
        )

        request = self.api_factory.get(f"/api/olts/{self.olt.id}/topology/")
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "topology"})(request, pk=str(self.olt.id))

        self.assertEqual(response.status_code, 200)
        onu_row = response.data["slots"]["2"]["pons"]["2/8"]["onus"][0]
        self.assertEqual(onu_row.get("offline_since"), source_clock.isoformat())
        self.assertEqual(onu_row.get("disconnect_window_start"), source_clock.isoformat())
        self.assertEqual(onu_row.get("disconnect_window_end"), source_clock.isoformat())

    @patch("topology.api.views.unm_service.localize_alarm_datetime")
    @patch("topology.api.views.unm_service.is_enabled_for_olt", return_value=True)
    def test_batch_status_uses_unm_source_clock_for_disconnect_window(
        self,
        _is_enabled_mock,
        localize_alarm_datetime_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=31,
            snmp_index="11.31",
            serial="ABCD12340031",
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        raw_utc = datetime(2026, 3, 9, 14, 2, 36, tzinfo=dt_timezone.utc)
        source_clock = datetime(2026, 3, 9, 11, 2, 36, tzinfo=dt_timezone(timedelta(hours=-3)))
        localize_alarm_datetime_mock.side_effect = lambda *, olt, value: source_clock
        ONULog.objects.create(
            onu=onu,
            offline_since=raw_utc,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=raw_utc,
            disconnect_window_end=raw_utc,
        )

        request = self.api_factory.post(
            "/api/onu/batch-status/",
            {
                "olt_id": self.olt.id,
                "slot_id": onu.slot_id,
                "pon_id": onu.pon_id,
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"post": "batch_status"})(request)

        self.assertEqual(response.status_code, 200)
        row = next((item for item in (response.data.get("results") or []) if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("offline_since"), source_clock.isoformat())
        self.assertEqual(row.get("disconnect_window_start"), source_clock.isoformat())
        self.assertEqual(row.get("disconnect_window_end"), source_clock.isoformat())

    def test_olt_topology_detail_leaves_power_empty_until_scoped_snapshot_load(self):
        self.vendor.oid_templates = {
            **(self.vendor.oid_templates or {}),
            "power": {"olt_rx_oid": "1.2.3"},
        }
        self.vendor.save(update_fields=["oid_templates"])
        slot, pon, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=21,
            snmp_index="28.21",
            serial="FHTT6A0F1A28",
        )
        read_at = timezone.now() - timedelta(minutes=1)
        ONUPowerSample.objects.create(
            olt=self.olt,
            onu=onu,
            slot_id=slot.slot_id,
            pon_id=pon.pon_id,
            onu_number=onu.onu_id,
            onu_rx_power=-20.4,
            olt_rx_power=-23.8,
            read_at=read_at,
            source=ONUPowerSample.SOURCE_SCOPED,
        )

        request = self.api_factory.get(f"/api/olts/{self.olt.id}/topology/")
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "topology"})(request, pk=str(self.olt.id))

        self.assertEqual(response.status_code, 200)
        onu_row = response.data["slots"]["2"]["pons"]["2/8"]["onus"][0]
        self.assertIsNone(onu_row.get("onu_rx_power"))
        self.assertIsNone(onu_row.get("olt_rx_power"))
        self.assertIsNone(onu_row.get("power_read_at"))

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_invalidates_topology_structure_cache(self, fetch_discovery_rows_mock):
        self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=30,
            snmp_index="28.30",
            serial="TPLGDISC0030",
            name="cliente-antigo",
        )
        warm_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(warm_request, user=self.user)
        warm_response = OLTViewSet.as_view({"get": "list"})(warm_request)
        self.assertEqual(warm_response.status_code, 200)
        self.assertIsNotNone(cache_service.get_topology_structure(self.olt.id))

        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "2",
                    "{#PON}": "8",
                    "{#ONU_ID}": "30",
                    "{#PON_ID}": "28",
                    "{#SNMPINDEX}": "28.30",
                    "{#SERIAL}": "TPLGDISC0030",
                    "{#ONU_NAME}": "cliente-novo",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        self.assertIsNone(cache_service.get_topology_structure(self.olt.id))

        refresh_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(refresh_request, user=self.user)
        refresh_response = OLTViewSet.as_view({"get": "list"})(refresh_request)

        self.assertEqual(refresh_response.status_code, 200)
        rows = refresh_response.data.get("results") if isinstance(refresh_response.data, dict) else refresh_response.data
        olt_row = next((row for row in (rows or []) if row.get("id") == self.olt.id), None)
        self.assertEqual(olt_row["slots"][0]["pons"][0]["onus"][0].get("name"), "cliente-novo")
        self.assertIsNotNone(cache_service.get_topology_structure(self.olt.id))

    @patch("topology.management.commands.discover_onus.topology_counter_service.refresh_olt", side_effect=RuntimeError("boom"))
    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_clears_cached_counters_when_counter_refresh_fails(
        self,
        fetch_discovery_rows_mock,
        _refresh_counters_mock,
    ):
        slot, pon, _onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=30,
            snmp_index="28.30",
            serial="TPLGDISC0030",
            name="cliente-antigo",
        )
        self.olt.cached_slot_count = 9
        self.olt.cached_pon_count = 18
        self.olt.cached_onu_count = 90
        self.olt.cached_online_count = 80
        self.olt.cached_offline_count = 10
        self.olt.cached_counts_at = timezone.now()
        self.olt.save(
            update_fields=[
                "cached_slot_count",
                "cached_pon_count",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
                "cached_counts_at",
            ]
        )
        slot.cached_pon_count = 8
        slot.cached_onu_count = 64
        slot.cached_online_count = 60
        slot.cached_offline_count = 4
        slot.save(
            update_fields=[
                "cached_pon_count",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
            ]
        )
        pon.cached_onu_count = 64
        pon.cached_online_count = 60
        pon.cached_offline_count = 4
        pon.save(update_fields=["cached_onu_count", "cached_online_count", "cached_offline_count"])

        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "2",
                    "{#PON}": "8",
                    "{#ONU_ID}": "30",
                    "{#PON_ID}": "28",
                    "{#SNMPINDEX}": "28.30",
                    "{#SERIAL}": "TPLGDISC0030",
                    "{#ONU_NAME}": "cliente-novo",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        self.olt.refresh_from_db()
        slot.refresh_from_db()
        pon.refresh_from_db()
        self.assertIsNone(self.olt.cached_slot_count)
        self.assertIsNone(self.olt.cached_pon_count)
        self.assertIsNone(self.olt.cached_onu_count)
        self.assertIsNone(self.olt.cached_online_count)
        self.assertIsNone(self.olt.cached_offline_count)
        self.assertIsNone(self.olt.cached_counts_at)
        self.assertIsNone(slot.cached_pon_count)
        self.assertIsNone(slot.cached_onu_count)
        self.assertIsNone(slot.cached_online_count)
        self.assertIsNone(slot.cached_offline_count)
        self.assertIsNone(pon.cached_onu_count)
        self.assertIsNone(pon.cached_online_count)
        self.assertIsNone(pon.cached_offline_count)

    @patch("topology.api.views.zabbix_service.sync_olt_host_runtime", return_value=True)
    def test_olt_update_invalidates_topology_structure_cache(self, _sync_runtime_mock):
        self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=31,
            snmp_index="28.31",
            serial="TPLGUPDT0031",
        )
        warm_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(warm_request, user=self.user)
        warm_response = OLTViewSet.as_view({"get": "list"})(warm_request)
        self.assertEqual(warm_response.status_code, 200)
        self.assertIsNotNone(cache_service.get_topology_structure(self.olt.id))

        admin_user = User.objects.create_superuser(username="admin-cache", password="admin-cache", email="admin@example.com")
        update_request = self.api_factory.patch(
            f"/api/olts/{self.olt.id}/",
            {"name": "OLT-ZABBIX-TEST-UPDATED"},
            format="json",
        )
        force_authenticate(update_request, user=admin_user)
        update_response = OLTViewSet.as_view({"patch": "partial_update"})(update_request, pk=str(self.olt.id))

        self.assertEqual(update_response.status_code, 200)
        self.assertIsNone(cache_service.get_topology_structure(self.olt.id))

    @patch("topology.api.views.zabbix_service.sync_olt_host_runtime", return_value=True)
    def test_olt_create_requires_unm_mneid_when_unm_enabled(self, _sync_runtime_mock):
        admin_user = User.objects.create_superuser(
            username="admin-unm-create",
            password="admin-unm-create",
            email="admin-unm-create@example.com",
        )
        request = self.api_factory.post(
            "/api/olts/",
            {
                "name": "OLT-UNM-REQ",
                "vendor_profile": self.vendor.id,
                "protocol": "snmp",
                "ip_address": "10.0.0.40",
                "snmp_port": 161,
                "snmp_community": "public",
                "snmp_version": "v2c",
                "discovery_enabled": True,
                "polling_enabled": True,
                "discovery_interval_minutes": 60,
                "polling_interval_seconds": 300,
                "power_interval_seconds": 300,
                "history_days": 7,
                "unm_enabled": True,
                "unm_host": "192.168.30.101",
                "unm_port": 3306,
                "unm_username": "unm2000",
                "unm_password": "secret",
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("unm_mneid", response.data)

    @patch("topology.api.views.zabbix_service.sync_olt_host_runtime", return_value=True)
    def test_olt_update_preserves_existing_unm_password_when_not_resubmitted(self, _sync_runtime_mock):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "existing-secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )

        admin_user = User.objects.create_superuser(
            username="admin-unm-update",
            password="admin-unm-update",
            email="admin-unm-update@example.com",
        )
        request = self.api_factory.patch(
            f"/api/olts/{self.olt.id}/",
            {
                "unm_enabled": True,
                "unm_host": "192.168.30.102",
                "unm_username": "unm2000",
                "unm_password": "",
                "unm_mneid": 13172741,
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"patch": "partial_update"})(request, pk=str(self.olt.id))

        self.assertEqual(response.status_code, 200)
        self.olt.refresh_from_db()
        self.assertEqual(self.olt.unm_host, "192.168.30.102")
        self.assertEqual(self.olt.unm_mneid, 13172741)
        self.assertEqual(self.olt.unm_password, "existing-secret")

    def test_seeded_fit_profile_exposes_http_collector_defaults(self):
        serializer = VendorProfileSerializer(instance=self.fit_vendor)
        collector_cfg = (self.fit_vendor.oid_templates or {}).get("collector") or {}
        discovery_cfg = (self.fit_vendor.oid_templates or {}).get("discovery") or {}

        self.assertEqual(serializer.data["vendor"], "FIT")
        self.assertEqual(serializer.data["model_name"], "FNCS4000")
        self.assertEqual(serializer.data["default_protocol"], "telnet")
        self.assertFalse(serializer.data["supports_olt_rx_power"])
        self.assertEqual(collector_cfg.get("type"), "fit_telnet")
        self.assertEqual(collector_cfg.get("transport"), "http")
        self.assertTrue(discovery_cfg.get("deactivate_missing"))
        self.assertIn("disable_lost_after_minutes", discovery_cfg)
        self.assertEqual(int(discovery_cfg.get("disable_lost_after_minutes") or 0), 0)
        self.assertEqual(self.fit_vendor.default_thresholds.get("power_interval_seconds"), 1800)

    def test_zabbix_vendors_default_to_http_transport_contract(self):
        self.assertEqual(get_collector_transport(self.vendor), "http")

    def test_olt_model_history_days_enforces_range_on_full_clean(self):
        self.olt.history_days = 31

        with self.assertRaises(ValidationError) as ctx:
            self.olt.full_clean()

        self.assertIn("history_days", ctx.exception.message_dict)

    def test_olt_model_snmp_version_choices_match_serializer_contract(self):
        field = OLT._meta.get_field("snmp_version")

        self.assertEqual(list(field.choices), [("v2c", "v2c")])

    def test_olt_create_requires_telnet_credentials_for_fit_vendor(self):
        admin_user = User.objects.create_superuser(
            username="admin-fit-create",
            password="admin-fit-create",
            email="admin-fit-create@example.com",
        )
        request = self.api_factory.post(
            "/api/olts/",
            {
                "name": "OLT-FIT-REQ",
                "vendor_profile": self.fit_vendor.id,
                "protocol": "telnet",
                "ip_address": "192.168.100.10",
                "telnet_username": "",
                "telnet_password": "",
                "discovery_enabled": True,
                "polling_enabled": True,
                "discovery_interval_minutes": 60,
                "polling_interval_seconds": 300,
                "power_interval_seconds": 1800,
                "history_days": 7,
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("telnet_username", response.data)
        self.assertIn("telnet_password", response.data)

    def test_olt_update_preserves_existing_telnet_password_when_not_resubmitted(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-PASSWORD")
        admin_user = User.objects.create_superuser(
            username="admin-fit-update",
            password="admin-fit-update",
            email="admin-fit-update@example.com",
        )
        request = self.api_factory.patch(
            f"/api/olts/{fit_olt.id}/",
            {
                "telnet_username": "new-bifrost",
                "telnet_password": "",
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"patch": "partial_update"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 200)
        fit_olt.refresh_from_db()
        self.assertEqual(fit_olt.telnet_username, "new-bifrost")
        self.assertEqual(fit_olt.telnet_password, "acaidosdeuses%gabisat")

    @patch("topology.services.fit_collector_service._FITHTTPSession.get_page", autospec=True)
    def test_fit_http_status_inventory_error_lists_each_failed_blade(self, get_page_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-BLADES")
        fit_olt.blade_ips = [{"ip": "192.168.100.2", "port": 23}, {"ip": "192.168.100.4", "port": 23}]
        fit_olt.save(update_fields=["blade_ips"])
        get_page_mock.side_effect = FITCollectorError("HTTP request failed: timed out")

        with self.assertRaises(FITCollectorError) as ctx:
            fit_collector_service.fetch_status_inventory(fit_olt)

        self.assertIn("Blade 192.168.100.2:", str(ctx.exception))
        self.assertIn("Blade 192.168.100.4:", str(ctx.exception))

    def test_fit_reachability_requires_explicit_blade_configuration(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-NO-BLADES")
        fit_olt.blade_ips = None
        fit_olt.save(update_fields=["blade_ips"])

        reachable, detail = fit_collector_service.check_reachability(fit_olt)

        self.assertFalse(reachable)
        self.assertIn("explicit IP and Telnet port", detail)

    def test_fit_olt_serializer_rejects_missing_blade_port(self):
        serializer = OLTSerializer(
            data={
                "name": "OLT-FIT-SERIALIZER",
                "vendor_profile": self.fit_vendor.id,
                "protocol": "telnet",
                "ip_address": "192.168.100.40",
                "telnet_username": "bifrost",
                "telnet_password": "acaidosdeuses%gabisat",
                "blade_ips": [{"ip": "192.168.100.40"}],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("blade_ips", serializer.errors)
        self.assertIn("Port is required for blade 192.168.100.40.", str(serializer.errors["blade_ips"]))

    def test_fit_olt_serializer_rejects_duplicate_blade_entries(self):
        serializer = OLTSerializer(
            data={
                "name": "OLT-FIT-SERIALIZER-DUP",
                "vendor_profile": self.fit_vendor.id,
                "protocol": "telnet",
                "ip_address": "192.168.100.40",
                "telnet_username": "bifrost",
                "telnet_password": "acaidosdeuses%gabisat",
                "blade_ips": [
                    {"ip": "192.168.100.40", "port": 23},
                    {"ip": "192.168.100.40", "port": 23},
                ],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("blade_ips", serializer.errors)
        self.assertIn("Duplicate blade entry: 192.168.100.40:23.", str(serializer.errors["blade_ips"]))

    def test_fit_parse_status_output_accepts_rows_with_and_without_uptime(self):
        raw_output = """
OnuId    Mac               Status Firmware ChipId GE FE POTS CTCStatus CTCVer Active Uptime Name
==============================================================================================
0/1:1    70:b6:4f:a4:6a:68 Down   V5.0.8   0x741B 1  0  2    NA        V2.1   Yes
0/1:2    58:d2:37:ea:f4:00 Up     V5.0.8   0x741B 1  0  2    NA        V2.1   Yes client-a
0/2:1    58:d2:37:ea:da:00 Up     V5.0.8   0x741B 1  0  2    NA        V2.1   Yes 17H 22M 53S
0/2:2    58:d2:37:ea:db:00 Down   V5.0.8   0x741B 1  0  2    NA        V2.1   Yes 1D 03H 04M 05S client-b
"""

        rows = fit_collector_service.parse_status_output(raw_output, slot_id=3)

        self.assertEqual(
            rows,
            [
                {
                    "slot_id": 3,
                    "pon_id": 1,
                    "onu_id": 1,
                    "interface": "0/1",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "",
                    "mac": "70:B6:4F:A4:6A:68",
                },
                {
                    "slot_id": 3,
                    "pon_id": 1,
                    "onu_id": 2,
                    "interface": "0/1",
                    "status": ONU.STATUS_ONLINE,
                    "name": "client-a",
                    "mac": "58:D2:37:EA:F4:00",
                },
                {
                    "slot_id": 3,
                    "pon_id": 2,
                    "onu_id": 1,
                    "interface": "0/2",
                    "status": ONU.STATUS_ONLINE,
                    "name": "",
                    "mac": "58:D2:37:EA:DA:00",
                },
                {
                    "slot_id": 3,
                    "pon_id": 2,
                    "onu_id": 2,
                    "interface": "0/2",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "client-b",
                    "mac": "58:D2:37:EA:DB:00",
                },
            ],
        )

    def test_fit_parse_status_output_skips_unauthorized_onus(self):
        raw_output = """
OnuId    Mac               Status Firmware ChipId GE FE POTS CTCStatus CTCVer Active Uptime Name
==============================================================================================
0/1:1    70:b6:4f:a4:6a:68 Down   V5.0.8   0x741B 1  0  2    NA        V2.1   Yes
0/1:2    58:d2:37:ea:f4:00 Up     V5.0.8   0x741B 1  0  2    NA        V2.1   No
0/2:1    58:d2:37:ea:da:00 Up     V5.0.8   0x741B 1  0  2    NA        V2.1   Nauth
0/2:2    58:d2:37:ea:db:00 Down   V5.0.8   0x741B 1  0  2    NA        V2.1   Yes client-b
"""

        rows = fit_collector_service.parse_status_output(raw_output, slot_id=2)

        self.assertEqual(
            rows,
            [
                {
                    "slot_id": 2,
                    "pon_id": 1,
                    "onu_id": 1,
                    "interface": "0/1",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "",
                    "mac": "70:B6:4F:A4:6A:68",
                },
                {
                    "slot_id": 2,
                    "pon_id": 2,
                    "onu_id": 2,
                    "interface": "0/2",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "client-b",
                    "mac": "58:D2:37:EA:DB:00",
                },
            ],
        )

    def test_fit_parse_http_status_page_accepts_legacy_overview_layout(self):
        page = """
<script>
var onutable=new Array(
'0/1:1','NA','70:B6:4F:A4:6A:68','Up','3230','9125','2','CtcNegDone','21','Activate','7943',
'0/1:2','cliente-fit','58:D2:37:EA:F4:00','Down','c41f','6878','3','MpcpDiscovery','21','Activate','1',
'0/1:3','NA','58:D2:37:EA:F4:01','Up','c41f','6878','3','CtcNegDone','21','Deactivate','7592'
);
var lineNum=(onutable.length)/11;
</script>
"""

        rows = fit_collector_service.parse_http_status_page(page, slot_id=2)

        self.assertEqual(
            rows,
            [
                {
                    "slot_id": 2,
                    "pon_id": 1,
                    "onu_id": 1,
                    "interface": "0/1",
                    "status": ONU.STATUS_ONLINE,
                    "name": "",
                    "mac": "70:B6:4F:A4:6A:68",
                    "onu_rx_power": None,
                },
                {
                    "slot_id": 2,
                    "pon_id": 1,
                    "onu_id": 2,
                    "interface": "0/1",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "cliente-fit",
                    "mac": "58:D2:37:EA:F4:00",
                    "onu_rx_power": None,
                },
            ],
        )

    def test_fit_parse_http_status_page_accepts_inline_optics_layout(self):
        page = """
<script>
var onutable=new Array(
'0/2:1','NA','94:02:6B:65:E5:85','Up','312e','9602','2','CtcNegDone','21','Activate','7943','34.00','3.00','13.00','2.06','-26.20',
'0/2:2','NA','A0:94:6A:0E:31:CB','Down','312e','9601','2','MpcpDiscovery','21','Activate','1','--','--','--','--','--'
);
var lineNum=(onutable.length)/16;
</script>
"""

        rows = fit_collector_service.parse_http_status_page(page, slot_id=1)

        self.assertEqual(
            rows,
            [
                {
                    "slot_id": 1,
                    "pon_id": 2,
                    "onu_id": 1,
                    "interface": "0/2",
                    "status": ONU.STATUS_ONLINE,
                    "name": "",
                    "mac": "94:02:6B:65:E5:85",
                    "onu_rx_power": -26.2,
                },
                {
                    "slot_id": 1,
                    "pon_id": 2,
                    "onu_id": 2,
                    "interface": "0/2",
                    "status": ONU.STATUS_OFFLINE,
                    "name": "",
                    "mac": "A0:94:6A:0E:31:CB",
                    "onu_rx_power": None,
                },
            ],
        )

    def test_fit_parse_http_status_page_accepts_all_onu_layout(self):
        page = """
<script>
var onutable=new Array(
'0/2:1','NA','E0:E8:E6:A0:69:09','Up','0101','9125','2','5','21','0','1163','30.00','3.00','14.00','1.72','-28.86','2026-03-11 16:14:06','2026-03-11 16:13:30','1','166365','5','1',
'0/2:2','NA','94:02:6B:E4:BD:CB','Down','1002','1601','2','5','21','2','1389','38.00','3.00','22.00','1.78','-30.97','2026-03-11 16:12:52','2026-03-11 16:11:50','1','166439','6','1'
);
var lineNum=(onutable.length)/22;
</script>
"""

        rows = fit_collector_service.parse_http_status_page(page, slot_id=2)

        self.assertEqual(
            rows,
            [
                {
                    "slot_id": 2,
                    "pon_id": 2,
                    "onu_id": 1,
                    "interface": "0/2",
                    "status": ONU.STATUS_ONLINE,
                    "name": "",
                    "mac": "E0:E8:E6:A0:69:09",
                    "onu_rx_power": -28.86,
                },
            ],
        )

    def test_fit_parse_http_detail_page_extracts_optical_values(self):
        page = """
<script>
var onuinfo=new Array(
'0/1:1','NA','70:B6:4F:A4:6A:68','Up','','','','2026-03-06 18:11:42','2026-03-09 15:44:09','2026-03-09 13:27:45'
);
var onuOpmInfo=new Array('0/1:1','34.00','3.00','13.00','2.06','-26.20');
</script>
"""

        detail = fit_collector_service.parse_http_detail_page(page)

        self.assertEqual(
            detail,
            {
                "interface": "0/1",
                "pon_id": 1,
                "onu_id": 1,
                "name": "",
                "mac": "70:B6:4F:A4:6A:68",
                "status": ONU.STATUS_ONLINE,
                "first_up_time": "2026-03-06 18:11:42",
                "last_up_time": "2026-03-09 15:44:09",
                "last_off_time": "2026-03-09 13:27:45",
                "temperature_c": 34.0,
                "voltage_v": 3.0,
                "bias_current_ma": 13.0,
                "tx_power_dbm": 2.06,
                "onu_rx_power": -26.2,
            },
        )

    @patch("topology.services.fit_collector_service._FITHTTPSession.get_page", autospec=True)
    def test_fit_http_power_uses_all_onu_page_when_available(self, get_page_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-HTTP-ALL")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=1,
            status=ONU.STATUS_ONLINE,
        )
        all_page = """
<script>
var onutable=new Array(
'0/2:1','NA','E0:E8:E6:A0:69:09','Up','0101','9125','2','5','21','0','1163','30.00','3.00','14.00','1.72','-28.86','2026-03-11 16:14:06','2026-03-11 16:13:30','1','166365','5','1'
);
var lineNum=(onutable.length)/22;
</script>
"""

        def _fake_get_page(_session, path):
            self.assertEqual(path, "onuAllPonOnuList.asp")
            return all_page

        get_page_mock.side_effect = _fake_get_page

        result = fit_collector_service.fetch_power_for_onus(fit_olt, [onu])

        self.assertEqual(result[onu.id]["onu_rx_power"], -28.86)
        self.assertIsNone(result[onu.id]["olt_rx_power"])

    def test_fit_http_power_rejects_slot_without_configured_blade(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-HTTP-MISSING-BLADE")
        onu = self._create_fit_onu(fit_olt, pon_id=2, onu_id=1, status=ONU.STATUS_ONLINE)
        onu.slot_id = 2
        onu.save(update_fields=["slot_id"])

        with self.assertRaises(FITCollectorError) as ctx:
            fit_collector_service.fetch_power_for_onus(fit_olt, [onu])

        self.assertIn("Slot 2:", str(ctx.exception))
        self.assertIn("No configured FIT blade", str(ctx.exception))

    def test_fit_status_inventory_rejects_requested_slot_without_configured_blade(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-STATUS-MISSING-BLADE")

        with self.assertRaises(FITCollectorError) as ctx:
            fit_collector_service.fetch_status_inventory_for_interfaces(
                fit_olt,
                interfaces_by_slot={2: ["0/1"]},
            )

        self.assertIn("Slot 2:", str(ctx.exception))
        self.assertIn("No configured FIT blade", str(ctx.exception))

    @patch("topology.management.commands.discover_onus.fit_collector_service.fetch_status_inventory")
    def test_olt_list_include_topology_keeps_fit_telnet_config_and_hides_empty_branches(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-LIST")
        fit_olt.blade_ips = [{"ip": "192.168.100.2", "port": 23}, {"ip": "192.168.100.4", "port": 23}]
        fit_olt.save(update_fields=["blade_ips"])
        fetch_status_inventory_mock.return_value = [
            {
                "slot_id": 1,
                "pon_id": 1,
                "onu_id": 7,
                "interface": "0/1",
                "status": ONU.STATUS_ONLINE,
                "name": "",
            },
            {
                "slot_id": 2,
                "pon_id": 2,
                "onu_id": 13,
                "interface": "0/2",
                "status": ONU.STATUS_ONLINE,
                "name": "",
            }
        ]

        call_command("discover_onus", olt_id=fit_olt.id, force=True)

        request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") if isinstance(response.data, dict) else response.data
        fit_row = next((row for row in (rows or []) if row.get("id") == fit_olt.id), None)
        self.assertIsNotNone(fit_row)
        self.assertEqual(fit_row.get("protocol"), "telnet")
        self.assertEqual(fit_row.get("blade_ips"), [{"ip": "192.168.100.2", "port": 23}, {"ip": "192.168.100.4", "port": 23}])
        self.assertEqual(fit_row.get("telnet_username"), "bifrost")
        self.assertEqual(fit_row.get("slot_count"), 2)
        self.assertEqual(fit_row.get("pon_count"), 2)
        self.assertEqual([slot.get("slot_number") for slot in fit_row.get("slots", [])], [1, 2])

        slot_one = next((slot for slot in fit_row.get("slots", []) if slot.get("slot_number") == 1), None)
        slot_two = next((slot for slot in fit_row.get("slots", []) if slot.get("slot_number") == 2), None)
        self.assertIsNotNone(slot_one)
        self.assertIsNotNone(slot_two)
        self.assertEqual(slot_one.get("pon_count"), 1)
        self.assertEqual([pon.get("pon_number") for pon in slot_one.get("pons", [])], [1])
        self.assertEqual(slot_two.get("pon_count"), 1)
        self.assertEqual([pon.get("pon_number") for pon in slot_two.get("pons", [])], [2])

    @patch("topology.management.commands.discover_onus.fit_collector_service.fetch_status_inventory")
    def test_run_discovery_returns_503_for_fit_collector_failure(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-DISCOVERY-ACTION")
        fetch_status_inventory_mock.side_effect = FITCollectorError(
            "Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused"
        )
        admin_user = User.objects.create_superuser(
            username="admin-fit-run-discovery",
            password="admin-fit-run-discovery",
            email="admin-fit-run-discovery@example.com",
        )
        request = self.api_factory.post(f"/api/olts/{fit_olt.id}/run_discovery/", {}, format="json")
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "run_discovery"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data.get("status"), "error")
        self.assertIn("Blade 192.168.100.2:", response.data.get("detail", ""))

    @patch("topology.management.commands.poll_onu_status.fit_collector_service.fetch_status_inventory_for_interfaces")
    def test_run_polling_returns_503_for_fit_collector_failure(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POLL-ACTION")
        self._create_fit_onu(fit_olt, pon_id=2, onu_id=13, status=ONU.STATUS_UNKNOWN)
        fetch_status_inventory_mock.side_effect = FITCollectorError(
            "Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused"
        )
        admin_user = User.objects.create_superuser(
            username="admin-fit-run-polling",
            password="admin-fit-run-polling",
            email="admin-fit-run-polling@example.com",
        )
        request = self.api_factory.post(f"/api/olts/{fit_olt.id}/run_polling/", {}, format="json")
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "run_polling"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data.get("status"), "error")
        self.assertIn("Blade 192.168.100.2:", response.data.get("detail", ""))

    @patch(
        "topology.management.commands.poll_onu_status.fit_collector_service.fetch_status_inventory_for_interfaces",
        return_value=[],
    )
    def test_run_polling_returns_503_for_fit_empty_status_snapshot_without_marking_unreachable(self, _fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POLL-EMPTY")
        self._create_fit_onu(fit_olt, pon_id=2, onu_id=13, status=ONU.STATUS_UNKNOWN)
        fit_olt.collector_reachable = False
        fit_olt.collector_failure_count = 2
        fit_olt.last_collector_error = "previous collector failure"
        fit_olt.save(
            update_fields=["collector_reachable", "collector_failure_count", "last_collector_error"]
        )
        admin_user = User.objects.create_superuser(
            username="admin-fit-run-polling-empty",
            password="admin-fit-run-polling-empty",
            email="admin-fit-run-polling-empty@example.com",
        )
        request = self.api_factory.post(f"/api/olts/{fit_olt.id}/run_polling/", {}, format="json")
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "run_polling"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data.get("status"), "error")
        self.assertIn("no status data returned", response.data.get("detail", "").lower())

        fit_olt.refresh_from_db()
        self.assertTrue(fit_olt.collector_reachable)
        self.assertEqual(fit_olt.collector_failure_count, 0)
        self.assertEqual((fit_olt.last_collector_error or "").strip(), "")

    def test_maintenance_job_marks_fit_discovery_failure_as_failed(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-DISCOVERY-JOB")
        admin_user = User.objects.create_superuser(
            username="admin-fit-discovery-job",
            password="admin-fit-discovery-job",
            email="admin-fit-discovery-job@example.com",
        )
        job = MaintenanceJob.objects.create(
            olt=fit_olt,
            kind=MaintenanceJob.KIND_DISCOVERY,
            status=MaintenanceJob.STATUS_RUNNING,
            progress=5,
            detail="Starting maintenance task.",
            requested_by=admin_user,
            started_at=timezone.now(),
        )

        def fake_run_command(*args, **kwargs):
            OLT.objects.filter(id=fit_olt.id).update(
                discovery_healthy=False,
                last_collector_error="Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused",
            )
            return (
                f"OLT {fit_olt.id}: FIT discovery request failed "
                "(Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused)."
            )

        with patch.object(maintenance_job_service, "_run_command_with_timeout", side_effect=fake_run_command):
            maintenance_job_service._execute_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, MaintenanceJob.STATUS_FAILED)
        self.assertIn("Blade 192.168.100.2:", job.error)

    def test_maintenance_job_marks_fit_polling_failure_as_failed(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POLL-JOB")
        admin_user = User.objects.create_superuser(
            username="admin-fit-poll-job",
            password="admin-fit-poll-job",
            email="admin-fit-poll-job@example.com",
        )
        job = MaintenanceJob.objects.create(
            olt=fit_olt,
            kind=MaintenanceJob.KIND_POLLING,
            status=MaintenanceJob.STATUS_RUNNING,
            progress=5,
            detail="Starting maintenance task.",
            requested_by=admin_user,
            started_at=timezone.now(),
        )

        def fake_run_command(*args, **kwargs):
            OLT.objects.filter(id=fit_olt.id).update(
                collector_reachable=False,
                last_collector_error="Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused",
            )
            return (
                f"OLT {fit_olt.id}: FIT status polling failed "
                "(Blade 192.168.100.2: Telnet connection failed: [Errno 111] Connection refused)."
            )

        with patch.object(maintenance_job_service, "_run_command_with_timeout", side_effect=fake_run_command):
            maintenance_job_service._execute_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, MaintenanceJob.STATUS_FAILED)
        self.assertIn("Blade 192.168.100.2:", job.error)

    def test_pon_description_update_invalidates_topology_structure_cache(self):
        slot, pon, _ = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=32,
            snmp_index="28.32",
            serial="TPLGPON0032",
        )
        warm_request = self.api_factory.get("/api/olts/", {"include_topology": "true"})
        force_authenticate(warm_request, user=self.user)
        warm_response = OLTViewSet.as_view({"get": "list"})(warm_request)
        self.assertEqual(warm_response.status_code, 200)
        self.assertIsNotNone(cache_service.get_topology_structure(self.olt.id))

        operator_user = User.objects.create_user(username="operator-cache", password="operator-cache")
        UserProfile.objects.create(user=operator_user, role=UserProfile.ROLE_OPERATOR)
        update_request = self.api_factory.patch(
            f"/api/pons/{pon.id}/",
            {"description": "descricao-nova"},
            format="json",
        )
        force_authenticate(update_request, user=operator_user)
        update_response = OLTPONViewSet.as_view({"patch": "partial_update"})(update_request, pk=str(pon.id))

        self.assertEqual(update_response.status_code, 200)
        self.assertIsNone(cache_service.get_topology_structure(self.olt.id))

    @patch("topology.api.views.power_service.refresh_for_onus")
    def test_onu_power_snapshot_reads_latest_synced_sample_without_refresh(self, refresh_for_onus_mock):
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=40,
            snmp_index="28.40",
            serial="TPLGPOW0040",
        )
        snapshot_read_at = timezone.now() - timedelta(minutes=4)
        ONU.objects.filter(id=onu.id).update(
            latest_onu_rx_power=-18.4,
            latest_olt_rx_power=-22.1,
            latest_power_read_at=snapshot_read_at,
        )

        request = self.api_factory.get(f"/api/onu/{onu.id}/power/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("onu_rx_power"), -18.4)
        self.assertEqual(response.data.get("power_read_at"), snapshot_read_at.isoformat())
        refresh_for_onus_mock.assert_not_called()

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch("topology.api.views.zabbix_service.fetch_power_by_index")
    def test_onu_power_without_refresh_reads_live_zabbix_power_when_zabbix_db_enabled(self, fetch_power_mock):
        templates = dict(self.vendor.oid_templates or {})
        templates["power"] = {"supports_olt_rx_power": True}
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=["oid_templates"])
        _, _, onu = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=43,
            snmp_index="28.43",
            serial="TPLGPOW0043",
        )
        snapshot_read_at = timezone.now() - timedelta(days=1)
        ONU.objects.filter(id=onu.id).update(
            latest_onu_rx_power=-18.4,
            latest_olt_rx_power=-22.1,
            latest_power_read_at=snapshot_read_at,
        )
        fetch_power_mock.return_value = (
            {
                "28.43": {
                    "onu_rx_power": -19.7,
                    "olt_rx_power": -24.2,
                    "power_read_at": "2026-03-10T00:08:20+00:00",
                }
            },
            "2026-03-10T00:08:20+00:00",
        )

        request = self.api_factory.get(f"/api/onu/{onu.id}/power/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("onu_rx_power"), -19.7)
        self.assertEqual(response.data.get("olt_rx_power"), -24.2)
        self.assertEqual(response.data.get("power_read_at"), "2026-03-10T00:08:20+00:00")
        fetch_power_mock.assert_called_once()
        self.assertTrue(fetch_power_mock.call_args.kwargs.get("history_fallback"))

    @patch("topology.api.views.power_service.refresh_for_onus")
    def test_batch_power_without_refresh_reads_latest_synced_samples(self, refresh_for_onus_mock):
        slot, pon, onu_a = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=41,
            snmp_index="28.41",
            serial="TPLGPOW0041",
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=slot.slot_id,
            pon_id=pon.pon_id,
            onu_id=42,
            snmp_index="28.42",
            serial="TPLGPOW0042",
            name="cliente-power-b",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        snapshot_read_at = timezone.now() - timedelta(minutes=2)
        ONU.objects.filter(id=onu_a.id).update(
            latest_onu_rx_power=-17.6,
            latest_olt_rx_power=-21.5,
            latest_power_read_at=snapshot_read_at,
        )

        request = self.api_factory.post(
            "/api/onu/batch-power/",
            {"olt_id": self.olt.id, "slot_id": 2, "pon_id": 8, "refresh": False},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"post": "batch_power"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row_a = next((row for row in rows if int(row.get("onu_id") or 0) == int(onu_a.id)), None)
        row_b = next((row for row in rows if int(row.get("onu_id") or 0) == int(onu_b.id)), None)
        self.assertIsNotNone(row_a)
        self.assertIsNotNone(row_b)
        self.assertEqual(row_a.get("onu_rx_power"), -17.6)
        self.assertEqual(row_a.get("power_read_at"), snapshot_read_at.isoformat())
        self.assertIsNone(row_b.get("onu_rx_power"))
        self.assertIsNone(row_b.get("olt_rx_power"))
        self.assertIsNone(row_b.get("power_read_at"))
        refresh_for_onus_mock.assert_not_called()

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch("topology.api.views.zabbix_service.fetch_power_by_index")
    def test_batch_power_without_refresh_reads_live_zabbix_power_when_zabbix_db_enabled(self, fetch_power_mock):
        slot, pon, onu_a = self._create_topology_onu(
            slot_id=2,
            pon_id=8,
            onu_id=44,
            snmp_index="28.44",
            serial="TPLGPOW0044",
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=slot.slot_id,
            pon_id=pon.pon_id,
            onu_id=45,
            snmp_index="28.45",
            serial="TPLGPOW0045",
            name="cliente-power-live-b",
            status=ONU.STATUS_ONLINE,
            is_active=True,
            latest_onu_rx_power=-17.1,
            latest_olt_rx_power=-21.0,
            latest_power_read_at=timezone.now() - timedelta(days=1),
        )
        fetch_power_mock.return_value = (
            {
                "28.44": {
                    "onu_rx_power": -16.8,
                    "olt_rx_power": -20.9,
                    "power_read_at": "2026-03-10T00:09:00+00:00",
                },
                "28.45": {
                    "onu_rx_power": -18.0,
                    "olt_rx_power": -22.2,
                    "power_read_at": "2026-03-10T00:09:10+00:00",
                },
            },
            "2026-03-10T00:09:10+00:00",
        )

        request = self.api_factory.post(
            "/api/onu/batch-power/",
            {"olt_id": self.olt.id, "slot_id": 2, "pon_id": 8, "refresh": False},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"post": "batch_power"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row_a = next((row for row in rows if int(row.get("onu_id") or 0) == int(onu_a.id)), None)
        row_b = next((row for row in rows if int(row.get("onu_id") or 0) == int(onu_b.id)), None)
        self.assertEqual(row_a.get("onu_rx_power"), -16.8)
        self.assertEqual(row_b.get("onu_rx_power"), -18.0)
        self.assertEqual(row_b.get("power_read_at"), "2026-03-10T00:09:10+00:00")
        fetch_power_mock.assert_called_once()
        self.assertTrue(fetch_power_mock.call_args.kwargs.get("history_fallback"))

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
        self.assertTrue(self.olt.collector_reachable)

    @patch("topology.management.commands.discover_onus.unm_service.fetch_onu_inventory_map")
    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_prefers_unm_name_when_configured(
        self,
        fetch_discovery_rows_mock,
        fetch_unm_inventory_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "1",
                    "{#PON}": "2",
                    "{#ONU_ID}": "3",
                    "{#PON_ID}": "11",
                    "{#SNMPINDEX}": "11.3",
                    "{#SERIAL}": "ABCD12345678",
                    "{#ONU_NAME}": "zabbix-name",
                }
            ],
            timezone.now().isoformat(),
        )
        fetch_unm_inventory_mock.return_value = {
            (1, 2, 3): {"unm_object_id": 196700003, "name": "unm-name", "serial": ""},
        }

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        onu = ONU.objects.get(olt=self.olt, slot_id=1, pon_id=2, onu_id=3)
        self.assertEqual(onu.name, "unm-name")

    @patch("topology.management.commands.discover_onus.unm_service.fetch_onu_inventory_map")
    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_keeps_zabbix_name_when_unm_inventory_lookup_fails(
        self,
        fetch_discovery_rows_mock,
        fetch_unm_inventory_mock,
    ):
        from topology.services.unm_service import UNMServiceError

        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "1",
                    "{#PON}": "2",
                    "{#ONU_ID}": "3",
                    "{#PON_ID}": "11",
                    "{#SNMPINDEX}": "11.3",
                    "{#SERIAL}": "ABCD12345678",
                    "{#ONU_NAME}": "zabbix-name",
                }
            ],
            timezone.now().isoformat(),
        )
        fetch_unm_inventory_mock.side_effect = UNMServiceError("UNM query failed.")

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        onu = ONU.objects.get(olt=self.olt, slot_id=1, pon_id=2, onu_id=3)
        self.assertEqual(onu.name, "zabbix-name")

    def test_normalize_serial_prefers_serial_like_fragment_when_value_has_comma(self):
        self.assertEqual(_normalize_serial("TPLG-D22D7400,"), "TPLGD22D7400")
        self.assertEqual(_normalize_serial("thiago.sodre100, TPLG-D22D7400"), "TPLGD22D7400")
        self.assertEqual(_normalize_serial("TPLG-D22D7400, thiago.sodre100"), "TPLGD22D7400")
        self.assertEqual(_normalize_serial("1,DD72E68F39E5"), "DD72E68F39E5")
        self.assertEqual(_normalize_serial("1"), "")

    @patch("topology.management.commands.discover_onus.zabbix_service.get_hostid", return_value=None)
    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_normalizes_trailing_numeric_suffix_when_serial_missing(
        self,
        fetch_discovery_rows_mock,
        _get_hostid_mock,
    ):
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "3",
                    "{#PON}": "4",
                    "{#ONU_ID}": "5",
                    "{#PON_ID}": "285278980",
                    "{#SNMPINDEX}": "285278980.5",
                    "{#SERIAL}": "",
                    "{#ONU_NAME}": "alexandre.silva 1",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        onu = ONU.objects.get(olt=self.olt, slot_id=3, pon_id=4, onu_id=5)
        self.assertEqual(onu.name, "alexandre.silva")
        self.assertEqual(onu.serial, "")

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_clears_placeholder_name_when_c600_name_oid_is_blank(
        self,
        fetch_discovery_rows_mock,
    ):
        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="",
            name="1",
            is_active=True,
        )
        fetch_discovery_rows_mock.return_value = (
            [
                {
                    "{#SLOT}": "1",
                    "{#PON}": "2",
                    "{#ONU_ID}": "3",
                    "{#PON_ID}": "11",
                    "{#SNMPINDEX}": "11.3",
                    "{#SERIAL}": "1,DD72E68F39E5",
                    "{#ONU_NAME}": "",
                }
            ],
            timezone.now().isoformat(),
        )

        call_command("discover_onus", olt_id=self.olt.id, force=True)

        onu = ONU.objects.get(olt=self.olt, slot_id=1, pon_id=2, onu_id=3)
        self.assertEqual(onu.serial, "DD72E68F39E5")
        self.assertEqual(onu.name, "")

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

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_reactivates_existing_slot_and_pon(self, fetch_discovery_rows_mock):
        slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=1,
            slot_key="1",
            is_active=False,
        )
        pon = OLTPON.objects.create(
            olt=self.olt,
            slot=slot,
            pon_id=2,
            pon_key="1/2",
            description="keep me",
            is_active=False,
        )
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

        slot.refresh_from_db()
        pon.refresh_from_db()
        self.assertTrue(slot.is_active)
        self.assertTrue(pon.is_active)
        self.assertEqual(pon.description, "keep me")
        self.assertEqual(OLTSlot.objects.filter(olt=self.olt, slot_key="1").count(), 1)
        self.assertEqual(OLTPON.objects.filter(olt=self.olt, slot=slot, pon_id=2).count(), 1)

    @patch("topology.management.commands.discover_onus.zabbix_service.fetch_discovery_rows")
    def test_discover_onus_recreated_pon_inherits_manual_description(self, fetch_discovery_rows_mock):
        old_slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=1,
            rack_id=1,
            shelf_id=1,
            slot_key="1/1",
            is_active=True,
        )
        old_pon = OLTPON.objects.create(
            olt=self.olt,
            slot=old_slot,
            pon_id=2,
            pon_index=11,
            pon_key="1/2",
            description="preserve me",
            is_active=True,
        )
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

        old_slot.refresh_from_db()
        old_pon.refresh_from_db()
        new_slot = OLTSlot.objects.get(olt=self.olt, slot_key="1")
        new_pon = OLTPON.objects.get(olt=self.olt, slot=new_slot, pon_id=2)

        self.assertFalse(old_slot.is_active)
        self.assertFalse(old_pon.is_active)
        self.assertTrue(new_slot.is_active)
        self.assertTrue(new_pon.is_active)
        self.assertEqual(new_pon.description, "preserve me")

    @patch("topology.management.commands.discover_onus.fit_collector_service.fetch_status_inventory")
    def test_fit_discover_onus_creates_only_discovered_pons_and_blank_names(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-DISCOVERY")
        fetch_status_inventory_mock.return_value = [
            {
                "slot_id": 1,
                "pon_id": 2,
                "onu_id": 13,
                "interface": "0/2",
                "status": ONU.STATUS_ONLINE,
                "name": "",
                "mac": "70:B6:4F:27:3F:60",
            },
            {
                "slot_id": 1,
                "pon_id": 4,
                "onu_id": 5,
                "interface": "0/4",
                "status": ONU.STATUS_OFFLINE,
                "name": "cliente-fit",
                "mac": "58:D2:37:EA:DB:00",
            },
        ]

        call_command("discover_onus", olt_id=fit_olt.id, force=True)

        fit_olt.refresh_from_db()
        self.assertTrue(fit_olt.collector_reachable)
        self.assertEqual(OLTSlot.objects.filter(olt=fit_olt, is_active=True).count(), 1)
        self.assertEqual(
            list(
                OLTPON.objects.filter(olt=fit_olt, is_active=True)
                .order_by("pon_id")
                .values_list("pon_id", flat=True)
            ),
            [2, 4],
        )

        blank_onu = ONU.objects.get(olt=fit_olt, pon_id=2, onu_id=13)
        named_onu = ONU.objects.get(olt=fit_olt, pon_id=4, onu_id=5)
        self.assertEqual(blank_onu.name, "")
        self.assertEqual(blank_onu.serial, "70:B6:4F:27:3F:60")
        self.assertEqual(blank_onu.snmp_index, "1/0/2:13")
        self.assertEqual(named_onu.name, "cliente-fit")
        self.assertEqual(named_onu.serial, "58:D2:37:EA:DB:00")
        self.assertEqual(named_onu.snmp_index, "1/0/4:5")

    @patch("topology.management.commands.discover_onus.fit_collector_service.fetch_status_inventory")
    def test_fit_discover_onus_allows_blank_name_update(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-BLANK-NAME")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            name="old-name",
            status=ONU.STATUS_ONLINE,
        )
        fetch_status_inventory_mock.return_value = [
            {
                "slot_id": 1,
                "pon_id": 2,
                "onu_id": 13,
                "interface": "0/2",
                "status": ONU.STATUS_ONLINE,
                "name": "",
            }
        ]

        call_command("discover_onus", olt_id=fit_olt.id, force=True)

        onu.refresh_from_db()
        self.assertEqual(onu.name, "")

    @patch("topology.management.commands.discover_onus.fit_collector_service.fetch_status_inventory")
    def test_fit_discover_onus_preserves_unchanged_non_empty_name(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-PRESERVE-NAME")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            name="elizangela.fibra",
            status=ONU.STATUS_ONLINE,
        )
        fetch_status_inventory_mock.return_value = [
            {
                "slot_id": 1,
                "pon_id": 2,
                "onu_id": 13,
                "interface": "0/2",
                "status": ONU.STATUS_ONLINE,
                "name": "elizangela.fibra",
            }
        ]

        call_command("discover_onus", olt_id=fit_olt.id, force=True)

        onu.refresh_from_db()
        self.assertEqual(onu.name, "elizangela.fibra")

    @patch("topology.management.commands.poll_onu_status.fit_collector_service.fetch_status_inventory_for_interfaces")
    def test_fit_poll_onu_status_maps_down_to_offline_unknown(self, fetch_status_inventory_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POLL")
        onu_up = self._create_fit_onu(fit_olt, pon_id=2, onu_id=13, status=ONU.STATUS_UNKNOWN)
        onu_down = self._create_fit_onu(fit_olt, pon_id=2, onu_id=14, status=ONU.STATUS_ONLINE)
        fetch_status_inventory_mock.return_value = [
            {
                "slot_id": 1,
                "pon_id": 2,
                "onu_id": 13,
                "interface": "0/2",
                "status": ONU.STATUS_ONLINE,
                "name": "",
            },
            {
                "slot_id": 1,
                "pon_id": 2,
                "onu_id": 14,
                "interface": "0/2",
                "status": ONU.STATUS_OFFLINE,
                "name": "",
            },
        ]

        call_command("poll_onu_status", olt_id=fit_olt.id, force=True)

        onu_up.refresh_from_db()
        onu_down.refresh_from_db()
        fit_olt.refresh_from_db()
        _, kwargs = fetch_status_inventory_mock.call_args
        self.assertEqual(kwargs.get("interfaces_by_slot"), {1: ["0/2"]})
        self.assertTrue(fit_olt.collector_reachable)
        self.assertEqual(onu_up.status, ONU.STATUS_ONLINE)
        self.assertEqual(onu_down.status, ONU.STATUS_OFFLINE)

        active_log = ONULog.objects.get(onu=onu_down, offline_until__isnull=True)
        self.assertEqual(active_log.disconnect_reason, ONULog.REASON_UNKNOWN)

    def test_fit_topology_detail_hides_mac_serial_surrogate(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-TOPOLOGY-SERIAL")
        self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            status=ONU.STATUS_ONLINE,
            serial="70:B6:4F:27:3F:60",
        )

        request = self.api_factory.get(f"/api/olts/{fit_olt.id}/topology/")
        force_authenticate(request, user=self.user)
        response = OLTViewSet.as_view({"get": "topology"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 200)
        slots = response.data["slots"]
        first_slot = slots[0] if isinstance(slots, list) else next(iter(slots.values()))
        pons = first_slot["pons"]
        first_pon = pons[0] if isinstance(pons, list) else next(iter(pons.values()))
        onu_rows = first_pon["onus"]
        self.assertEqual(onu_rows[0]["serial"], "")

    @patch("topology.services.power_service.fit_collector_service.fetch_power_for_onus")
    def test_fit_power_service_telnet_skips_online_onu_ids_above_64(self, fetch_power_for_onus_mock):
        self._set_fit_transport("telnet")
        fit_olt = self._create_fit_olt(name="OLT-FIT-POWER")
        supported_onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=64,
            status=ONU.STATUS_ONLINE,
        )
        unsupported_onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=65,
            status=ONU.STATUS_ONLINE,
        )

        def _fake_power_fetch(_olt, onus):
            self.assertEqual([onu.onu_id for onu in onus], [64])
            return {
                supported_onu.id: {
                    "onu_id": supported_onu.id,
                    "slot_id": supported_onu.slot_id,
                    "pon_id": supported_onu.pon_id,
                    "onu_number": supported_onu.onu_id,
                    "onu_rx_power": -29.5,
                    "olt_rx_power": None,
                    "power_read_at": timezone.now().isoformat(),
                }
            }

        fetch_power_for_onus_mock.side_effect = _fake_power_fetch

        result = power_service.refresh_for_onus([supported_onu, unsupported_onu], force_refresh=True)

        self.assertEqual(result[supported_onu.id]["onu_rx_power"], -29.5)
        self.assertIsNone(result[supported_onu.id]["olt_rx_power"])
        self.assertEqual(result[unsupported_onu.id]["skipped_reason"], "unsupported_onu_id")

    @patch("topology.services.power_service.fit_collector_service.fetch_power_for_onus")
    def test_fit_power_service_http_allows_online_onu_ids_above_64(self, fetch_power_for_onus_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POWER-HTTP")
        onu_64 = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=64,
            status=ONU.STATUS_ONLINE,
        )
        onu_65 = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=65,
            status=ONU.STATUS_ONLINE,
        )

        def _fake_power_fetch(_olt, onus):
            self.assertEqual([onu.onu_id for onu in onus], [64, 65])
            now = timezone.now().isoformat()
            return {
                onu_64.id: {
                    "onu_id": onu_64.id,
                    "slot_id": onu_64.slot_id,
                    "pon_id": onu_64.pon_id,
                    "onu_number": onu_64.onu_id,
                    "onu_rx_power": -29.5,
                    "olt_rx_power": None,
                    "power_read_at": now,
                },
                onu_65.id: {
                    "onu_id": onu_65.id,
                    "slot_id": onu_65.slot_id,
                    "pon_id": onu_65.pon_id,
                    "onu_number": onu_65.onu_id,
                    "onu_rx_power": -28.1,
                    "olt_rx_power": None,
                    "power_read_at": now,
                },
            }

        fetch_power_for_onus_mock.side_effect = _fake_power_fetch

        result = power_service.refresh_for_onus([onu_64, onu_65], force_refresh=True)

        self.assertEqual(result[onu_64.id]["onu_rx_power"], -29.5)
        self.assertEqual(result[onu_65.id]["onu_rx_power"], -28.1)
        self.assertNotIn("skipped_reason", result[onu_65.id])

    def test_fit_power_report_hides_mac_serial_surrogate(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-POWER-REPORT-SERIAL")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            status=ONU.STATUS_ONLINE,
            serial="70:B6:4F:27:3F:60",
        )

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data["results"] if row["id"] == onu.id)
        self.assertEqual(row["serial"], "")

    def test_fit_alarm_clients_hides_mac_serial_surrogate(self):
        fit_olt = self._create_fit_olt(name="OLT-FIT-ALARM-SERIAL")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            status=ONU.STATUS_ONLINE,
            name="cliente-fit",
            serial="70:B6:4F:27:3F:60",
        )

        request = self.api_factory.get("/api/onu/alarm-clients/", {"search": "cliente-fit"})
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_clients"})(request)

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data["results"] if row["id"] == onu.id)
        self.assertEqual(row["serial"], "")

    @patch("topology.api.views.check_olt_reachability", return_value=(True, "HTTP UI request succeeded."))
    def test_collector_check_reports_http_collector_for_fit_vendor(self, _check_reachability_mock):
        fit_olt = self._create_fit_olt(name="OLT-FIT-CHECK")
        admin_user = User.objects.create_superuser(
            username="admin-fit-check",
            password="admin-fit-check",
            email="admin-fit-check@example.com",
        )
        request = self.api_factory.post(f"/api/olts/{fit_olt.id}/collector_check/", {}, format="json")
        force_authenticate(request, user=admin_user)
        response = OLTViewSet.as_view({"post": "collector_check"})(request, pk=str(fit_olt.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("collector"), "http")
        fit_olt.refresh_from_db()
        self.assertTrue(fit_olt.collector_reachable)

    @patch.object(ONUViewSet, "_has_usable_status_snapshot", return_value=True)
    @patch(
        "topology.api.views.power_service.refresh_for_onus",
        side_effect=FITCollectorError("Telnet connection failed."),
    )
    def test_batch_power_refresh_fit_returns_503_on_collector_failure(
        self,
        _refresh_for_onus_mock,
        _has_usable_status_snapshot_mock,
    ):
        fit_olt = self._create_fit_olt(name="OLT-FIT-BATCH-POWER")
        onu = self._create_fit_onu(
            fit_olt,
            pon_id=2,
            onu_id=13,
            status=ONU.STATUS_ONLINE,
        )
        operator_user = User.objects.create_user(
            username="operator-fit-power",
            password="operator-fit-power",
        )
        UserProfile.objects.create(user=operator_user, role=UserProfile.ROLE_OPERATOR)
        request = self.api_factory.post(
            "/api/onu/batch-power/",
            {"onu_ids": [onu.id], "refresh": True},
            format="json",
        )
        force_authenticate(request, user=operator_user)
        response = ONUViewSet.as_view({"post": "batch_power"})(request)

        self.assertEqual(response.status_code, 503)
        self.assertIn("Telnet connection failed.", response.data.get("detail", ""))

    @patch("topology.services.fit_collector_service.telnetlib.Telnet")
    def test_fit_telnet_login_enters_enable_mode_before_commands(self, telnet_mock):
        self._set_fit_transport("telnet")
        fit_olt = self._create_fit_olt(name="OLT-FIT-LOGIN")
        fake_telnet = _FakeFITTelnet(
            [
                b"",
                (
                    b"\r\n**************************************** \r\n"
                    b"Access Verification ../\r\nUsername:"
                ),
                b"\r\nPassword:",
                b"\r\nEPON> ",
                b"EPON# ",
            ]
        )
        telnet_mock.return_value = fake_telnet

        with _FITTelnetSession(fit_olt):
            pass

        written = b"".join(fake_telnet.writes)
        self.assertIn(b"bifrost\r", written)
        self.assertIn(b"acaidosdeuses%gabisat\r", written)
        self.assertIn(b"enable\r", written)
        self.assertTrue(fake_telnet.closed)

    @patch("topology.services.fit_collector_service.telnetlib.Telnet")
    def test_fit_telnet_run_command_advances_enter_key_pager(self, telnet_mock):
        self._set_fit_transport("telnet")
        fit_olt = self._create_fit_olt(name="OLT-FIT-PAGER")
        fake_telnet = _FakeFITTelnet(
            [
                b"",
                b"Username:",
                b"Password:",
                b"EPON> ",
                b"EPON# ",
                (
                    b"show onu info epon 0/1 all\r\n"
                    b"0/1:1  a0:94:6a:0e:31:cb Down    312e     9601   1  0  0    --             21     Yes      0H 0M 0S          \r\n"
                    b"--- Enter Key To Continue ----"
                ),
                (
                    b"\x1b[K\r"
                    b"0/1:2  70:b6:4f:27:3f:60 Up      3230     9125   1  0  0    CtcNegDone     21     Yes      1D 13H 16M 53S    cliente-fit\r\n"
                    b"\x1b[K\rEPON# "
                ),
            ]
        )
        telnet_mock.return_value = fake_telnet

        with _FITTelnetSession(fit_olt) as session:
            output = session.run_command("show onu info epon 0/1 all")

        written = b"".join(fake_telnet.writes)
        self.assertIn(b"show onu info epon 0/1 all\r", written)
        self.assertIn(b" ", written)
        self.assertNotIn("Enter Key To Continue", output)
        self.assertNotIn("\x1b[K", output)
        self.assertIn("0/1:1", output)
        self.assertIn("0/1:2", output)

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
        self.olt.collector_reachable = True
        self.olt.save(update_fields=["last_poll_at", "collector_reachable"])

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
        self.assertTrue(self.olt.collector_reachable)

    @patch("topology.management.commands.poll_onu_status.topology_counter_service.refresh_olt", side_effect=RuntimeError("boom"))
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_clears_cached_counters_when_counter_refresh_fails(
        self,
        fetch_status_mock,
        _refresh_counters_mock,
    ):
        slot, pon, onu = self._create_topology_onu(
            slot_id=1,
            pon_id=2,
            onu_id=3,
            snmp_index="11.3",
            serial="ABCD12345678",
            status=ONU.STATUS_ONLINE,
        )
        self.olt.cached_slot_count = 9
        self.olt.cached_pon_count = 18
        self.olt.cached_onu_count = 90
        self.olt.cached_online_count = 80
        self.olt.cached_offline_count = 10
        self.olt.cached_counts_at = timezone.now()
        self.olt.save(
            update_fields=[
                "cached_slot_count",
                "cached_pon_count",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
                "cached_counts_at",
            ]
        )
        slot.cached_pon_count = 8
        slot.cached_onu_count = 64
        slot.cached_online_count = 60
        slot.cached_offline_count = 4
        slot.save(
            update_fields=[
                "cached_pon_count",
                "cached_onu_count",
                "cached_online_count",
                "cached_offline_count",
            ]
        )
        pon.cached_onu_count = 64
        pon.cached_online_count = 60
        pon.cached_offline_count = 4
        pon.save(update_fields=["cached_onu_count", "cached_online_count", "cached_offline_count"])
        self.olt.last_poll_at = timezone.now() - timedelta(minutes=1)
        self.olt.collector_reachable = True
        self.olt.save(update_fields=["last_poll_at", "collector_reachable"])

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

        self.olt.refresh_from_db()
        slot.refresh_from_db()
        pon.refresh_from_db()
        onu.refresh_from_db()
        self.assertEqual(onu.status, ONU.STATUS_OFFLINE)
        self.assertIsNone(self.olt.cached_slot_count)
        self.assertIsNone(self.olt.cached_pon_count)
        self.assertIsNone(self.olt.cached_onu_count)
        self.assertIsNone(self.olt.cached_online_count)
        self.assertIsNone(self.olt.cached_offline_count)
        self.assertIsNone(self.olt.cached_counts_at)
        self.assertIsNone(slot.cached_pon_count)
        self.assertIsNone(slot.cached_onu_count)
        self.assertIsNone(slot.cached_online_count)
        self.assertIsNone(slot.cached_offline_count)
        self.assertIsNone(pon.cached_onu_count)
        self.assertIsNone(pon.cached_online_count)
        self.assertIsNone(pon.cached_offline_count)

    @patch("topology.management.commands.poll_onu_status.unm_service.fetch_current_alarm_state_map")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_uses_unm_current_alarm_for_topology_timestamp(
        self,
        fetch_status_mock,
        fetch_current_alarm_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
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

        fetch_status_mock.return_value = (
            {
                "11.3": {
                    "status": ONU.STATUS_OFFLINE,
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "901",
                }
            },
            timezone.now().isoformat(),
        )
        unm_time = datetime(2026, 3, 9, 11, 4, 11, tzinfo=dt_timezone(timedelta(hours=-3)))
        fetch_current_alarm_mock.return_value = {
            onu.id: {
                "disconnect_reason": ONULog.REASON_DYING_GASP,
                "occurred_at": unm_time,
            }
        }

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        self.assertEqual(log.disconnect_reason, ONULog.REASON_DYING_GASP)
        self.assertEqual(log.offline_since, unm_time)
        self.assertEqual(log.disconnect_window_start, unm_time)
        self.assertEqual(log.disconnect_window_end, unm_time)
        fetch_current_alarm_mock.assert_called_once()

    @patch("topology.management.commands.poll_onu_status.unm_service.fetch_current_alarm_state_map")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_unm_falls_back_to_unknown_without_current_alarm(
        self,
        fetch_status_mock,
        fetch_current_alarm_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=4,
            snmp_index="11.4",
            serial="ABCD12345679",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        current_epoch = int(timezone.now().timestamp())
        fetch_status_mock.return_value = (
            {
                "11.4": {
                    "status": ONU.STATUS_OFFLINE,
                    "reason": ONULog.REASON_LINK_LOSS,
                    "status_clock_epoch": current_epoch,
                    "status_itemid": "902",
                }
            },
            timezone.now().isoformat(),
        )
        fetch_current_alarm_mock.return_value = {}

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        expected_point = datetime.fromtimestamp(current_epoch, tz=dt_timezone.utc)
        self.assertEqual(log.disconnect_reason, ONULog.REASON_UNKNOWN)
        self.assertEqual(log.offline_since, expected_point)
        self.assertEqual(log.disconnect_window_start, expected_point)
        self.assertEqual(log.disconnect_window_end, expected_point)
        fetch_current_alarm_mock.assert_called_once()

    @patch("topology.management.commands.poll_onu_status.unm_service.fetch_current_alarm_state_map")
    @patch("topology.management.commands.poll_onu_status.zabbix_service.fetch_status_by_index")
    def test_poll_onu_status_updates_existing_open_log_with_unm_current_alarm(
        self,
        fetch_status_mock,
        fetch_current_alarm_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=5,
            snmp_index="11.5",
            serial="ABCD12345680",
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        old_point = timezone.now() - timedelta(hours=3)
        ONULog.objects.create(
            onu=onu,
            offline_since=old_point,
            disconnect_reason=ONULog.REASON_UNKNOWN,
            disconnect_window_start=old_point,
            disconnect_window_end=old_point,
        )

        fetch_status_mock.return_value = (
            {
                "11.5": {
                    "status": ONU.STATUS_OFFLINE,
                    "reason": ONULog.REASON_UNKNOWN,
                    "status_clock_epoch": int(timezone.now().timestamp()),
                    "status_itemid": "903",
                }
            },
            timezone.now().isoformat(),
        )
        unm_time = datetime(2026, 3, 9, 12, 15, 0, tzinfo=dt_timezone(timedelta(hours=-3)))
        fetch_current_alarm_mock.return_value = {
            onu.id: {
                "disconnect_reason": ONULog.REASON_LINK_LOSS,
                "occurred_at": unm_time,
            }
        }

        call_command("poll_onu_status", olt_id=self.olt.id, force=True)

        log = ONULog.objects.get(onu=onu, offline_until__isnull=True)
        self.assertEqual(log.disconnect_reason, ONULog.REASON_LINK_LOSS)
        self.assertEqual(log.offline_since, unm_time)
        self.assertEqual(log.disconnect_window_start, unm_time)
        self.assertEqual(log.disconnect_window_end, unm_time)

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
        self.assertFalse(self.olt.collector_reachable)
        self.assertFalse(ONULog.objects.filter(onu=onu, offline_until__isnull=True).exists())

    @override_settings(ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS=0)
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
        self.assertTrue(self.olt.collector_reachable)
        self.assertEqual((self.olt.last_collector_error or "").strip(), "")
        execute_now_mock.assert_called_once()
        check_reachability_mock.assert_called_once()

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

    def test_persist_power_samples_returns_inserted_row_count(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=99,
            snmp_index="11.99",
            serial="ABCD99999999",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        read_at = timezone.now().isoformat()
        result_map = {
            onu.id: {
                "onu_rx_power": -18.7,
                "olt_rx_power": -22.9,
                "power_read_at": read_at,
            }
        }

        first_inserted = persist_power_samples([onu], result_map, max_age_minutes=180)
        second_inserted = persist_power_samples([onu], result_map, max_age_minutes=180)

        self.assertEqual(first_inserted, 1)
        self.assertEqual(second_inserted, 0)
        self.assertEqual(ONUPowerSample.objects.filter(onu=onu).count(), 1)

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

    def test_fetch_power_by_index_falls_back_to_latest_valid_history_when_current_value_is_invalid(self):
        service = ZabbixService()
        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch("topology.services.zabbix_service.time.time", return_value=1763066623),
            patch.object(
                service,
                "get_items_by_keys",
                return_value={
                    "onuRxPower[28.40]": {
                        "itemid": "2001",
                        "key_": "onuRxPower[28.40]",
                        "lastvalue": "-80",
                        "lastclock": "1773014405",
                        "value_type": "0",
                    },
                    "oltRxPower[28.40]": {
                        "itemid": "2002",
                        "key_": "oltRxPower[28.40]",
                        "lastvalue": "0",
                        "lastclock": "0",
                        "value_type": "0",
                    },
                },
            ),
            patch.object(
                service,
                "get_latest_valid_power_history_samples",
                return_value={
                    "2001": {
                        "value": -23.01,
                        "clock": "2026-03-08T00:00:37+00:00",
                        "clock_epoch": 1772928037,
                    },
                    "2002": {
                        "value": -26.90,
                        "clock": "2026-03-08T00:00:12+00:00",
                        "clock_epoch": 1772928012,
                    },
                },
            ) as history_fallback_mock,
        ):
            rows, read_at = service.fetch_power_by_index(
                self.olt,
                ["28.40"],
                onu_rx_item_key_pattern="onuRxPower[{index}]",
                olt_rx_item_key_pattern="oltRxPower[{index}]",
            )

        history_fallback_mock.assert_called_once_with(
            item_specs={"2001": "0", "2002": "0"},
            time_from=1762461823,
        )
        row = rows.get("28.40") or {}
        self.assertEqual(row.get("onu_rx_power"), -23.01)
        self.assertEqual(row.get("olt_rx_power"), -26.90)
        self.assertEqual(row.get("power_read_at"), "2026-03-08T00:00:37+00:00")
        self.assertEqual(read_at, "2026-03-08T00:00:37+00:00")

    @override_settings(ZABBIX_DB_ENABLED=True)
    def test_get_latest_valid_power_history_samples_prefers_db_before_api(self):
        service = ZabbixService()
        with (
            patch.object(
                service,
                "_get_latest_valid_power_history_samples_from_db",
                return_value={
                    "2001": {
                        "value": -23.01,
                        "clock": "2026-03-08T00:00:37+00:00",
                        "clock_epoch": 1772928037,
                    }
                },
            ) as db_fallback_mock,
            patch.object(service, "_call") as call_mock,
        ):
            rows = service.get_latest_valid_power_history_samples(
                item_specs={"2001": "0"},
                time_from=1772000000,
                limit_per_item=10,
            )

        db_fallback_mock.assert_called_once_with(
            item_specs={"2001": "0"},
            time_from=1772000000,
            limit_per_item=10,
        )
        call_mock.assert_not_called()
        self.assertEqual(
            rows,
            {
                "2001": {
                    "value": -23.01,
                    "clock": "2026-03-08T00:00:37+00:00",
                    "clock_epoch": 1772928037,
                }
            },
        )

    @override_settings(ZABBIX_DB_ENABLED=True)
    def test_get_latest_valid_power_history_samples_partial_db_returns_only_db_results(self):
        """When DB returns partial data (only some items), the method returns
        ONLY what DB gave -- no API call for remaining items."""
        service = ZabbixService()
        partial_db_results = {
            "2001": {
                "value": -23.01,
                "clock": "2026-03-08T00:00:37+00:00",
                "clock_epoch": 1772928037,
            }
            # item "2002" intentionally missing from DB results
        }
        with (
            patch.object(
                service,
                "_get_latest_valid_power_history_samples_from_db",
                return_value=partial_db_results,
            ) as db_mock,
            patch.object(service, "_call") as call_mock,
        ):
            rows = service.get_latest_valid_power_history_samples(
                item_specs={"2001": "0", "2002": "0"},
                time_from=1772000000,
                limit_per_item=10,
            )

        db_mock.assert_called_once_with(
            item_specs={"2001": "0", "2002": "0"},
            time_from=1772000000,
            limit_per_item=10,
        )
        call_mock.assert_not_called()
        self.assertEqual(rows, partial_db_results)

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch.object(ZabbixService, "_call")
    @patch.object(
        ZabbixService,
        "_get_latest_valid_power_history_samples_from_db",
        return_value=None,
    )
    def test_get_latest_valid_power_history_samples_returns_empty_on_db_failure_no_api_fallback(
        self, db_mock, call_mock
    ):
        """When DB returns None (failure), the method returns {} with no API fallback."""
        service = ZabbixService()
        rows = service.get_latest_valid_power_history_samples(
            item_specs={"2001": "0"},
            time_from=1772000000,
            limit_per_item=10,
        )

        db_mock.assert_called_once()
        call_mock.assert_not_called()
        self.assertEqual(rows, {})

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch.object(ZabbixService, "_call")
    def test_fetch_previous_status_samples_uses_db_not_api(self, call_mock):
        """fetch_previous_status_samples must NOT call _call (the API) when
        ZABBIX_DB_ENABLED=True.  It should return a dict (possibly empty if
        the test DB lacks the item)."""
        service = ZabbixService()
        result = service.fetch_previous_status_samples(
            item_clock_by_itemid={"99999": 1772928000},
        )
        call_mock.assert_not_called()
        self.assertIsInstance(result, dict)

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch.object(ZabbixService, "_call")
    def test_fetch_previous_status_samples_skips_invalid_inputs(self, call_mock):
        """Invalid / empty inputs are silently skipped and no API call is made."""
        service = ZabbixService()
        result = service.fetch_previous_status_samples(
            item_clock_by_itemid={"": 100, "abc": 200, "0": 300, "1": -5},
        )
        call_mock.assert_not_called()
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})

    @override_settings(ZABBIX_DB_ENABLED=False)
    @patch.object(ZabbixService, "_call")
    def test_fetch_previous_status_samples_returns_empty_when_db_disabled(self, call_mock):
        """When ZABBIX_DB_ENABLED is False the method must return {} without
        calling the API."""
        service = ZabbixService()
        result = service.fetch_previous_status_samples(
            item_clock_by_itemid={"100": 1772928000},
        )
        call_mock.assert_not_called()
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})

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

    def test_discovery_rows_fallback_to_status_items_zte_c600_comma_prefixed_serial(self):
        zte_templates = _zabbix_vendor_templates()
        zte_templates["indexing"] = {
            "format": "pon_onu",
            "pon_encoding": "0x11rrsspp",
            "slot_from": "shelf",
            "pon_from": "port",
        }
        vendor = VendorProfile.objects.create(
            vendor="zte",
            model_name="C600-ZABBIX-TEST-COMMA",
            description="Zabbix mode test vendor zte c600 comma-prefixed serial",
            oid_templates=zte_templates,
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=False,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-ZTE-C600-ZABBIX-TEST-COMMA",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.26",
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
            patch.object(service, "get_single_item", return_value=None),
            patch.object(service, "get_discovery_rule", return_value=None),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[285278727.10]",
                        "name": "ONU 2/7/10 1,DD72E68F39E5: Status",
                        "lastclock": "1710000005",
                    }
                ],
            ),
        ):
            rows, _ = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#SNMPINDEX}"), "285278727.10")
        self.assertEqual(row.get("{#SLOT}"), "2")
        self.assertEqual(row.get("{#PON}"), "7")
        self.assertEqual(row.get("{#ONU_ID}"), "10")
        self.assertFalse(row.get("{#ONU_NAME}"))
        self.assertEqual(row.get("{#SERIAL}"), "DD72E68F39E5")

    def test_fetch_discovery_rows_repairs_malformed_lld_identity_with_status_fallback(self):
        zte_templates = _zabbix_vendor_templates()
        zte_templates["indexing"] = {
            "format": "pon_onu",
            "pon_encoding": "0x11rrsspp",
            "slot_from": "shelf",
            "pon_from": "port",
        }
        vendor = VendorProfile.objects.create(
            vendor="zte",
            model_name="C600-ZABBIX-TEST-REPAIR",
            description="Zabbix mode test vendor zte c600 malformed identity repair",
            oid_templates=zte_templates,
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=False,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-ZTE-C600-ZABBIX-TEST-REPAIR",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.27",
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
        malformed_lld = json.dumps(
            [
                {
                    "{#SNMPINDEX}": "285278980.5",
                    "{#SLOT}": "3",
                    "{#PON}": "4",
                    "{#ONU_ID}": "5",
                    "{#PON_ID}": "285278980",
                    "{#ONU_NAME}": "alexandre.silva 1",
                    "{#SERIAL}": "",
                }
            ]
        )
        with (
            patch.object(service, "get_hostid", return_value="10090"),
            patch.object(
                service,
                "get_single_item",
                return_value={
                    "itemid": "4496",
                    "key_": "onuDiscovery",
                    "value_type": "4",
                    "status": "0",
                    "state": "0",
                    "lastclock": "1710000005",
                    "lastvalue": malformed_lld,
                },
            ),
            patch.object(
                service,
                "get_items_by_key_prefix",
                return_value=[
                    {
                        "key_": "onuStatusValue[285278980.5]",
                        "name": "ONU 3/4/5 alexandre.silva 42061D3261D0: Status",
                        "lastclock": "1710000006",
                    }
                ],
            ),
        ):
            rows, read_at = service.fetch_discovery_rows(olt, "onuDiscovery")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("{#ONU_NAME}"), "alexandre.silva")
        self.assertEqual(row.get("{#SERIAL}"), "42061D3261D0")
        self.assertTrue(read_at)

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

    def test_get_items_by_keys_prefers_zabbix_db_reader_when_available(self):
        service = ZabbixService()
        db_rows = {
            "onuStatusValue[1]": {
                "itemid": "5001",
                "key_": "onuStatusValue[1]",
                "lastvalue": "online",
                "prevvalue": "offline",
                "lastclock": "1710000200",
                "state": "0",
                "status": "0",
                "error": "",
                "value_type": "4",
            }
        }

        with (
            patch.object(service, "_get_items_by_keys_from_db", return_value=db_rows) as db_reader_mock,
            patch.object(service, "_call") as api_call_mock,
        ):
            rows = service.get_items_by_keys("10001", ["onuStatusValue[1]"])

        self.assertEqual(rows, db_rows)
        db_reader_mock.assert_called_once_with("10001", ["onuStatusValue[1]"])
        api_call_mock.assert_not_called()

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch.object(ZabbixService, "_call")
    @patch.object(ZabbixService, "_get_items_by_keys_from_db", return_value=None)
    def test_get_items_by_keys_returns_empty_on_db_failure_no_api_fallback(
        self, db_reader_mock, api_call_mock
    ):
        service = ZabbixService()
        result = service.get_items_by_keys("10099", ["onuStatusValue[1]"])

        self.assertEqual(result, {})
        db_reader_mock.assert_called_once_with("10099", ["onuStatusValue[1]"])
        api_call_mock.assert_not_called()

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
        self.assertEqual(payload.get("synced_count"), 1)

        sample = ONUPowerSample.objects.get(onu=onu)
        self.assertEqual(sample.onu_rx_power, -19.5)
        self.assertEqual(sample.olt_rx_power, -23.1)
        onu.refresh_from_db()
        self.assertEqual(onu.latest_onu_rx_power, -19.5)
        self.assertEqual(onu.latest_olt_rx_power, -23.1)
        self.assertEqual(onu.latest_power_read_at, read_at)

    def test_sync_latest_power_snapshots_clears_stale_values(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=33,
            snmp_index="11.33",
            serial="ABCD12349999",
            status=ONU.STATUS_OFFLINE,
            is_active=True,
            latest_onu_rx_power=-19.1,
            latest_olt_rx_power=-23.0,
            latest_power_read_at=timezone.now() - timedelta(minutes=5),
        )

        updated = sync_latest_power_snapshots(
            [onu],
            {
                onu.id: {
                    "onu_rx_power": None,
                    "olt_rx_power": None,
                    "power_read_at": None,
                }
            },
        )

        self.assertEqual(updated, 1)
        onu.refresh_from_db()
        self.assertIsNone(onu.latest_onu_rx_power)
        self.assertIsNone(onu.latest_olt_rx_power)
        self.assertIsNone(onu.latest_power_read_at)

    def test_sync_latest_power_snapshots_preserves_existing_online_snapshot_when_requested(self):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=34,
            snmp_index="11.34",
            serial="ABCD12340034",
            status=ONU.STATUS_ONLINE,
            is_active=True,
            latest_onu_rx_power=-18.7,
            latest_olt_rx_power=-22.4,
            latest_power_read_at=timezone.now() - timedelta(minutes=5),
        )

        updated = sync_latest_power_snapshots(
            [onu],
            {
                onu.id: {
                    "onu_rx_power": None,
                    "olt_rx_power": None,
                    "power_read_at": None,
                }
            },
            preserve_existing_empty_online=True,
        )

        self.assertEqual(updated, 0)
        onu.refresh_from_db()
        self.assertEqual(onu.latest_onu_rx_power, -18.7)
        self.assertEqual(onu.latest_olt_rx_power, -22.4)
        self.assertIsNotNone(onu.latest_power_read_at)

    @patch("topology.services.power_service.zabbix_service.fetch_power_by_index")
    def test_collect_power_for_scheduler_preserves_existing_online_snapshot_when_current_value_is_invalid(
        self,
        fetch_power_mock,
    ):
        existing_read_at = timezone.now() - timedelta(hours=2)
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=2,
            onu_id=35,
            snmp_index="11.35",
            serial="ABCD12340035",
            status=ONU.STATUS_ONLINE,
            is_active=True,
            latest_onu_rx_power=-18.1,
            latest_olt_rx_power=-22.0,
            latest_power_read_at=existing_read_at,
        )
        self.olt.last_poll_at = timezone.now()
        self.olt.save(update_fields=["last_poll_at"])

        fetch_power_mock.return_value = (
            {
                "11.35": {
                    "onu_rx_power": None,
                    "olt_rx_power": None,
                    "power_read_at": None,
                }
            },
            None,
        )

        payload = collect_power_for_olt(
            self.olt,
            force_refresh=True,
            include_results=False,
            history_source=ONUPowerSample.SOURCE_SCHEDULER,
            use_history_fallback=False,
        )

        self.assertEqual(payload.get("stored_count"), 0)
        self.assertEqual(payload.get("synced_count"), 0)
        onu.refresh_from_db()
        self.assertEqual(onu.latest_onu_rx_power, -18.1)
        self.assertEqual(onu.latest_olt_rx_power, -22.0)
        self.assertEqual(onu.latest_power_read_at, existing_read_at)
        self.assertFalse(fetch_power_mock.call_args.kwargs.get("history_fallback", True))

    def test_scheduler_respects_configured_zabbix_power_sync_interval_even_when_power_interval_is_daily(self):
        self.olt.power_interval_seconds = 86400
        self.olt.last_power_at = timezone.now() - timedelta(minutes=10)
        self.olt.next_power_at = timezone.now() + timedelta(hours=23)
        self.olt.save(update_fields=["power_interval_seconds", "last_power_at", "next_power_at"])

        self.assertEqual(get_power_sync_interval_seconds(self.olt), 86400)
        self.assertFalse(_is_power_due(self.olt, timezone.now()))

    def test_fit_power_sync_interval_keeps_configured_schedule(self):
        fit_olt = self._create_fit_olt()
        fit_olt.power_interval_seconds = 1800
        fit_olt.save(update_fields=["power_interval_seconds"])

        self.assertEqual(get_power_sync_interval_seconds(fit_olt), 1800)

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

    def test_template_name_candidates_use_explicit_profile_names_without_zte_c300_fallback(self):
        vendor = VendorProfile.objects.create(
            vendor="zte",
            model_name="C600-TEST",
            description="ZTE C600 explicit template name test",
            oid_templates={
                "zabbix": {
                    "host_template_name": "OLT ZTE C600",
                    "host_template_names": ["OLT ZTE C600"],
                }
            },
            supports_onu_discovery=True,
            supports_onu_status=True,
            supports_power_monitoring=True,
            supports_disconnect_reason=True,
            default_thresholds={},
            is_active=True,
        )
        olt = OLT.objects.create(
            name="OLT-ZTE-C600-TEMPLATE",
            vendor_profile=vendor,
            protocol="snmp",
            ip_address="10.0.0.11",
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
        candidates = service._template_name_candidates_for_olt(olt)

        self.assertEqual(candidates, ["OLT ZTE C600"])

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

    @patch("topology.management.commands.run_scheduler.check_olt_reachability")
    def test_scheduler_recovery_schedules_immediate_poll(self, check_reachability_mock):
        self.olt.collector_reachable = False
        self.olt.collector_failure_count = 3
        self.olt.last_collector_check_at = None
        self.olt.next_poll_at = timezone.now() + timedelta(hours=1)
        self.olt.save(
            update_fields=["collector_reachable", "collector_failure_count", "last_collector_check_at", "next_poll_at"]
        )

        check_reachability_mock.return_value = (True, "")
        command = SchedulerCommand()
        command._run_collector_checks(base_interval_seconds=30, max_backoff_seconds=1800)

        self.olt.refresh_from_db()
        self.assertTrue(self.olt.collector_reachable)
        self.assertEqual(self.olt.collector_failure_count, 0)
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

    @override_settings(ALARM_HISTORY_POWER_MERGE_WINDOW_SECONDS=90)
    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_merges_nearby_onu_and_olt_power_samples_with_override(self, fetch_timeline_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=151,
            snmp_index="11.151",
            serial="ABCD01020151",
            name="cliente-hist-merge-window",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        base_epoch = int(timezone.now().timestamp())
        fetch_timeline_mock.return_value = {
            "status_samples": [
                {"clock_epoch": base_epoch - 900, "clock": None, "value": "online"},
            ],
            "status_previous": {"clock_epoch": base_epoch - 1200, "value": "online"},
            "onu_rx_samples": [
                {"clock_epoch": base_epoch - 121, "clock": None, "value": "-20.86"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": base_epoch - 60, "clock": None, "value": "-26.02"},
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
        power_history = response.data.get("power_history") or []
        self.assertEqual(len(power_history), 1)
        self.assertEqual(power_history[0].get("onu_rx_power"), -20.86)
        self.assertEqual(power_history[0].get("olt_rx_power"), -26.02)

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

    @patch("topology.api.views.unm_service.fetch_onu_alarm_history")
    @patch("topology.api.views.zabbix_service.fetch_onu_item_timelines")
    def test_alarm_history_uses_unm_source_when_olt_has_unm_enabled(
        self,
        fetch_timeline_mock,
        fetch_unm_alarm_history_mock,
    ):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=107,
            snmp_index="11.107",
            serial="ABCD01020107",
            name="cliente-hist-unm",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        base_epoch = int(timezone.now().timestamp())
        fetch_unm_alarm_history_mock.return_value = [
            {
                "id": "unm-1",
                "event_type": "link_loss",
                "event_label": "LINK LOSS",
                "event_code": 2400,
                "severity": 2,
                "start_at": timezone.now().isoformat(),
                "end_at": None,
                "status": "active",
                "duration_seconds": 120,
                "location": "OLT/PON/ONU",
            },
            {
                "id": "unm-2",
                "event_type": "unm",
                "event_label": "TX POWER HIGH ALARM",
                "event_code": 2592,
                "severity": 2,
                "start_at": (timezone.now() - timedelta(minutes=20)).isoformat(),
                "end_at": (timezone.now() - timedelta(minutes=10)).isoformat(),
                "status": "resolved",
                "duration_seconds": 600,
                "location": "OLT/PON/ONU",
            },
        ]
        fetch_timeline_mock.return_value = {
            "onu_rx_samples": [
                {"clock_epoch": base_epoch - 300, "clock": None, "value": "-22.5"},
            ],
            "olt_rx_samples": [
                {"clock_epoch": base_epoch - 300, "clock": None, "value": "-24.5"},
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
        self.assertEqual(response.data.get("source"), "unm")
        alarms = response.data.get("alarms") or []
        self.assertEqual(len(alarms), 2)
        self.assertEqual(alarms[0].get("event_label"), "LINK LOSS")
        self.assertEqual(alarms[1].get("event_code"), 2592)
        stats = response.data.get("stats") or {}
        self.assertEqual(stats.get("total"), 2)
        self.assertEqual(stats.get("link_loss"), 1)
        self.assertEqual(stats.get("unknown"), 1)
        power_history = response.data.get("power_history") or []
        self.assertEqual(len(power_history), 1)
        self.assertEqual(power_history[0].get("onu_rx_power"), -22.5)

    @patch("topology.api.views.unm_service.fetch_onu_alarm_history")
    def test_alarm_history_returns_503_when_unm_lookup_fails(self, fetch_unm_alarm_history_mock):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=108,
            snmp_index="11.108",
            serial="ABCD01020108",
            name="cliente-hist-unm-fail",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_unm_alarm_history_mock.side_effect = UNMServiceError("UNM query failed.")

        request = self.api_factory.get(
            f"/api/onu/{onu.id}/alarm-history/",
            {"alarm_days": 7, "power_days": 7},
        )
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "alarm_history"})(request, pk=str(onu.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data.get("detail"), "UNM query failed.")

    def test_unm_alarm_history_service_uses_available_history_tables_without_merge(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=109, snmp_index="11.109", name="cliente-unm-service")
        service = UNMService()
        now = timezone.now()
        queries = []

        def fake_query(_olt, query, params):
            queries.append(" ".join(str(query).split()))
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700109,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 109,
                        "cobjectname": "cliente-unm-service",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                return [
                    {"Tables_in_alarmdb": "t_alarmlogcur"},
                    {"Tables_in_alarmdb": "t_alarmloghist"},
                    {"Tables_in_alarmdb": "t_alarmloghist_1_1"},
                ]
            if "FROM alarmdb.t_alarmlogcur" in query:
                self.assertNotIn("ORDER BY", query)
                return [
                    {
                        "clogid": 11,
                        "cobjectid": 196700109,
                        "cneid": 13172740,
                        "calarmcode": 2340,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(minutes=5)),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "DYING_GASP",
                        "calarmexinfo": "",
                    }
                ]
            if "FROM alarmdb.t_alarmloghist" in query and "t_alarmloghist_1_1" not in query:
                self.assertNotIn("ORDER BY", query)
                return [
                    {
                        "clogid": 10,
                        "cobjectid": 196700109,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(hours=1)),
                        "cclearutctime": timezone.make_naive(now - timedelta(minutes=45)),
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "LINK LOSS",
                        "calarmexinfo": "",
                    }
                ]
            if "FROM alarmdb.t_alarmloghist_1_1" in query:
                self.assertNotIn("ORDER BY", query)
                return [
                    {
                        "clogid": 9,
                        "cobjectid": 196700109,
                        "cneid": 13172740,
                        "calarmcode": 2592,
                        "calarmlevel": 1,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=1)),
                        "cclearutctime": timezone.make_naive(now - timedelta(days=1, minutes=-2)),
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "TX POWER HIGH ALARM",
                        "calarmexinfo": "",
                    }
                ]
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now,
                alarm_limit=10,
            )

        self.assertEqual([alarm.get("event_code") for alarm in alarms], [2340, 2400, 2592])
        self.assertEqual([alarm.get("status") for alarm in alarms], ["active", "resolved", "resolved"])
        self.assertFalse(any("t_alarmloghist_merge" in query for query in queries))
        self.assertFalse(any("ORDER BY" in query for query in queries if "t_alarmlog" in query))

    def test_unm_alarm_history_service_falls_back_to_recent_window_when_direct_object_query_times_out(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=110, snmp_index="11.110", name="cliente-unm-fallback")
        service = UNMService()
        now = timezone.now()
        queries = []

        def fake_query(_olt, query, params):
            normalized_query = " ".join(str(query).split())
            queries.append(normalized_query)
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700110,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 110,
                        "cobjectname": "cliente-unm-fallback",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                return [{"Tables_in_alarmdb": "t_alarmlogcur"}]
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE cneid = %s" in query:
                raise UNMServiceError("UNM query failed.")
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE coccurutctime >= %s" in query:
                return [
                    {
                        "clogid": 21,
                        "cobjectid": 196700110,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=2)),
                        "cclearutctime": timezone.make_naive(now - timedelta(days=2, minutes=-5)),
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "LINK LOSS",
                        "calarmexinfo": "",
                    },
                    {
                        "clogid": 22,
                        "cobjectid": 196799999,
                        "cneid": 13172740,
                        "calarmcode": 2592,
                        "calarmlevel": 1,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=1)),
                        "cclearutctime": timezone.make_naive(now - timedelta(days=1, minutes=-1)),
                        "clocationinfo": "OTHER",
                        "clineport": "",
                        "calarminfo": "OTHER",
                        "calarmexinfo": "",
                    },
                ]
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE cclearutctime IS NULL" in query:
                return [
                    {
                        "clogid": 23,
                        "cobjectid": 196700110,
                        "cneid": 13172740,
                        "calarmcode": 2340,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=30)),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "DYING_GASP",
                        "calarmexinfo": "",
                    },
                    {
                        "clogid": 24,
                        "cobjectid": 196799999,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=20)),
                        "cclearutctime": None,
                        "clocationinfo": "OTHER",
                        "clineport": "",
                        "calarminfo": "OTHER ACTIVE",
                        "calarmexinfo": "",
                    },
                ]
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now,
                alarm_limit=10,
            )

        self.assertEqual([alarm.get("event_code") for alarm in alarms], [2400, 2340])
        self.assertEqual([alarm.get("status") for alarm in alarms], ["resolved", "active"])
        self.assertTrue(any("WHERE coccurutctime >= %s" in query for query in queries))
        self.assertTrue(any("WHERE cclearutctime IS NULL" in query for query in queries))

    def test_unm_alarm_history_service_returns_empty_when_fallback_succeeds_without_matches(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=111, snmp_index="11.111", name="cliente-unm-empty-fallback")
        service = UNMService()
        now = timezone.now()

        def fake_query(_olt, query, params):
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700111,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 111,
                        "cobjectname": "cliente-unm-empty-fallback",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                return [{"Tables_in_alarmdb": "t_alarmlogcur"}]
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE cneid = %s" in query:
                raise UNMServiceError("UNM query failed.")
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE coccurutctime >= %s" in query:
                return [
                    {
                        "clogid": 31,
                        "cobjectid": 196799999,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(days=1)),
                        "cclearutctime": timezone.make_naive(now - timedelta(days=1, minutes=-1)),
                        "clocationinfo": "OTHER",
                        "clineport": "",
                        "calarminfo": "OTHER",
                        "calarmexinfo": "",
                    }
                ]
            if "FROM alarmdb.t_alarmlogcur" in query and "WHERE cclearutctime IS NULL" in query:
                return []
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now,
                alarm_limit=10,
            )

        self.assertEqual(alarms, [])

    def test_unm_current_alarm_state_map_uses_latest_current_alarm_per_onu(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu_a = self._create_topology_onu(onu_id=113, snmp_index="11.113", name="cliente-unm-current-a")
        _, _, onu_b = self._create_topology_onu(
            slot_id=2,
            pon_id=1,
            onu_id=114,
            snmp_index="21.114",
            name="cliente-unm-current-b",
        )
        service = UNMService()

        def fake_query(_olt, query, params):
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if "FROM alarmdb.t_alarmlogcur cur" in query:
                return [
                    {
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 113,
                        "clogid": 41,
                        "cobjectid": 196700113,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": datetime(2026, 3, 9, 10, 0, 0),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "LINK LOSS",
                        "calarmexinfo": "",
                    },
                    {
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 113,
                        "clogid": 42,
                        "cobjectid": 196700113,
                        "cneid": 13172740,
                        "calarmcode": 2592,
                        "calarmlevel": 1,
                        "coccurutctime": datetime(2026, 3, 9, 10, 5, 0),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "TX POWER HIGH ALARM",
                        "calarmexinfo": "",
                    },
                    {
                        "cslotno": 2,
                        "cponno": 1,
                        "cauthno": 114,
                        "clogid": 43,
                        "cobjectid": 196700114,
                        "cneid": 13172740,
                        "calarmcode": 2340,
                        "calarmlevel": 2,
                        "coccurutctime": datetime(2026, 3, 9, 9, 55, 0),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "DYING_GASP",
                        "calarmexinfo": "",
                    },
                ]
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarm_state = service.fetch_current_alarm_state_map(olt=self.olt, onus=[onu_a, onu_b])

        self.assertEqual(alarm_state[onu_a.id]["disconnect_reason"], ONULog.REASON_UNKNOWN)
        self.assertEqual(alarm_state[onu_a.id]["occurred_at"].isoformat(), "2026-03-09T07:05:00-03:00")
        self.assertEqual(alarm_state[onu_a.id]["event_code"], 2592)
        self.assertEqual(alarm_state[onu_b.id]["disconnect_reason"], ONULog.REASON_DYING_GASP)
        self.assertEqual(alarm_state[onu_b.id]["occurred_at"].isoformat(), "2026-03-09T06:55:00-03:00")
        self.assertEqual(alarm_state[onu_b.id]["event_code"], 2340)

    def test_unm_alarm_history_service_uses_merge_table_when_available(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=110, snmp_index="11.110", name="cliente-unm-merge")
        service = UNMService()
        now = timezone.now()
        table_queries = []

        def fake_query(_olt, query, params):
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700110,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 110,
                        "cobjectname": "cliente-unm-merge",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                return [
                    {"Tables_in_alarmdb": "t_alarmlogcur"},
                    {"Tables_in_alarmdb": "t_alarmloghist_merge"},
                    {"Tables_in_alarmdb": "t_alarmloghist_1_1"},
                ]
            if "FROM alarmdb.t_alarmlogcur" in query:
                table_queries.append("cur")
                return []
            if "FROM alarmdb.t_alarmloghist_merge" in query:
                table_queries.append("merge")
                return [
                    {
                        "clogid": 8,
                        "cobjectid": 196700110,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(minutes=10)),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "LINK LOSS",
                        "calarmexinfo": "",
                    }
                ]
            if "FROM alarmdb.t_alarmloghist_1_1" in query:
                self.fail("Partition history table should not be queried when merge table exists.")
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now,
                alarm_limit=10,
            )

        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].get("event_code"), 2400)
        self.assertEqual(table_queries, ["cur", "merge"])

    def test_unm_alarm_history_service_returns_current_rows_when_history_table_discovery_fails(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=111, snmp_index="11.111", name="cliente-unm-current-only")
        service = UNMService()
        now = timezone.now()

        def fake_query(_olt, query, params):
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700111,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 111,
                        "cobjectname": "cliente-unm-current-only",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": -10800}]
            if "FROM alarmdb.t_alarmlogcur" in query:
                return [
                    {
                        "clogid": 7,
                        "cobjectid": 196700111,
                        "cneid": 13172740,
                        "calarmcode": 2340,
                        "calarmlevel": 2,
                        "coccurutctime": timezone.make_naive(now - timedelta(minutes=2)),
                        "cclearutctime": None,
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "DYING_GASP",
                        "calarmexinfo": "",
                    }
                ]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                raise UNMServiceError("UNM query failed.")
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now,
                alarm_limit=10,
            )

        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].get("event_code"), 2340)

    def test_unm_alarm_history_service_uses_unm_offset_for_naive_datetimes(self):
        self.olt.unm_enabled = True
        self.olt.unm_host = "192.168.30.101"
        self.olt.unm_port = 3306
        self.olt.unm_username = "unm2000"
        self.olt.unm_password = "secret"
        self.olt.unm_mneid = 13172740
        self.olt.save(
            update_fields=[
                "unm_enabled",
                "unm_host",
                "unm_port",
                "unm_username",
                "unm_password",
                "unm_mneid",
            ]
        )
        _, _, onu = self._create_topology_onu(onu_id=112, snmp_index="11.112", name="cliente-unm-timezone")
        service = UNMService()
        now = timezone.now()

        def fake_query(_olt, query, params):
            if "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())" in query:
                return [{"utc_offset_seconds": 7200}]
            if "FROM integratecfgdb.t_ontdevice" in query:
                return [
                    {
                        "cobjectid": 196700112,
                        "cslotno": 1,
                        "cponno": 1,
                        "cauthno": 112,
                        "cobjectname": "cliente-unm-timezone",
                        "caliasname": "",
                        "clogicalsn": "",
                    }
                ]
            if str(query).strip() == "SHOW TABLES FROM alarmdb":
                return [{"Tables_in_alarmdb": "t_alarmlogcur"}]
            if "FROM alarmdb.t_alarmlogcur" in query:
                return [
                    {
                        "clogid": 6,
                        "cobjectid": 196700112,
                        "cneid": 13172740,
                        "calarmcode": 2400,
                        "calarmlevel": 2,
                        "coccurutctime": datetime(2026, 3, 9, 10, 15, 0),
                        "cclearutctime": datetime(2026, 3, 9, 10, 45, 0),
                        "clocationinfo": "OLT/PON/ONU",
                        "clineport": "",
                        "calarminfo": "LINK LOSS",
                        "calarmexinfo": "",
                    }
                ]
            self.fail(f"Unexpected UNM query: {query}")

        with patch.object(service, "_query", side_effect=fake_query):
            alarms = service.fetch_onu_alarm_history(
                olt=self.olt,
                onu=onu,
                alarm_cutoff=now - timedelta(days=7),
                alarm_end=now + timedelta(days=1),
                alarm_limit=10,
            )

        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].get("start_at"), "2026-03-09T12:15:00+02:00")
        self.assertEqual(alarms[0].get("end_at"), "2026-03-09T12:45:00+02:00")

    @patch("topology.api.views.power_service.refresh_for_onus")
    def test_power_report_discards_sentinel_power_values(self, refresh_for_onus_mock):
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
            latest_onu_rx_power=0.0,
            latest_olt_rx_power=-40.0,
            latest_power_read_at=timezone.now(),
        )

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
        refresh_for_onus_mock.assert_not_called()

    @patch("topology.api.views.power_service.refresh_for_onus")
    def test_power_report_uses_latest_synced_snapshot_without_live_read(self, refresh_for_onus_mock):
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=303,
            snmp_index="11.303",
            serial="ABCD01020303",
            name="cliente-power-cache-ignore",
            status=ONU.STATUS_ONLINE,
            is_active=True,
            latest_onu_rx_power=-18.5,
            latest_olt_rx_power=-22.8,
            latest_power_read_at=timezone.now(),
        )
        cache_service.set_many_onu_power(
            self.olt.id,
            {
                onu.id: {
                    "onu_id": onu.id,
                    "slot_id": onu.slot_id,
                    "pon_id": onu.pon_id,
                    "onu_number": onu.onu_id,
                    "onu_rx_power": -18.5,
                    "olt_rx_power": -22.8,
                    "power_read_at": timezone.now().isoformat(),
                }
            },
            ttl=3600,
        )
        live_read_at = onu.latest_power_read_at

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row = next((item for item in rows if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("onu_rx_power"), -18.5)
        self.assertEqual(row.get("power_read_at"), live_read_at.isoformat())
        refresh_for_onus_mock.assert_not_called()

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch("topology.api.views.zabbix_service.fetch_power_by_index")
    def test_power_report_reads_live_zabbix_power_when_zabbix_db_enabled(self, fetch_power_mock):
        templates = dict(self.vendor.oid_templates or {})
        templates["power"] = {"supports_olt_rx_power": True}
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=["oid_templates"])
        onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=304,
            snmp_index="11.304",
            serial="ABCD01020304",
            name="cliente-power-live-report",
            status=ONU.STATUS_ONLINE,
            is_active=True,
            latest_onu_rx_power=-18.5,
            latest_olt_rx_power=-22.8,
            latest_power_read_at=timezone.now() - timedelta(days=1),
        )
        fetch_power_mock.return_value = (
            {
                "11.304": {
                    "onu_rx_power": -17.2,
                    "olt_rx_power": -21.4,
                    "power_read_at": "2026-03-10T00:10:00+00:00",
                }
            },
            "2026-03-10T00:10:00+00:00",
        )

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results") or []
        row = next((item for item in rows if item.get("id") == onu.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("onu_rx_power"), -17.2)
        self.assertEqual(row.get("olt_rx_power"), -21.4)
        self.assertEqual(row.get("power_read_at"), "2026-03-10T00:10:00+00:00")
        fetch_power_mock.assert_called_once()
        self.assertTrue(fetch_power_mock.call_args.kwargs.get("history_fallback"))

    @override_settings(
        ZABBIX_DB_ENABLED=True,
        POWER_LATEST_READS_HISTORY_FALLBACK_MAX_ITEMS=1,
    )
    @patch("topology.api.views.zabbix_service.fetch_power_by_index")
    def test_power_report_skips_history_fallback_for_large_live_reads(self, fetch_power_mock):
        onu_a = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=305,
            snmp_index="11.305",
            serial="ABCD01020305",
            name="cliente-power-live-report-a",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=306,
            snmp_index="11.306",
            serial="ABCD01020306",
            name="cliente-power-live-report-b",
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        fetch_power_mock.return_value = (
            {
                "11.305": {
                    "onu_rx_power": -17.2,
                    "olt_rx_power": -21.4,
                    "power_read_at": "2026-03-10T00:10:00+00:00",
                },
                "11.306": {
                    "onu_rx_power": -17.9,
                    "olt_rx_power": -22.0,
                    "power_read_at": "2026-03-10T00:10:05+00:00",
                },
            },
            "2026-03-10T00:10:05+00:00",
        )

        request = self.api_factory.get("/api/onu/power-report/")
        force_authenticate(request, user=self.user)
        response = ONUViewSet.as_view({"get": "power_report"})(request)

        self.assertEqual(response.status_code, 200)
        row_ids = {item.get("id") for item in response.data.get("results") or []}
        self.assertIn(onu_a.id, row_ids)
        self.assertIn(onu_b.id, row_ids)
        fetch_power_mock.assert_called_once()
        self.assertFalse(fetch_power_mock.call_args.kwargs.get("history_fallback"))

    @patch("topology.api.views.power_service.refresh_for_onus")
    def test_power_report_discards_out_of_range_power_values(self, refresh_for_onus_mock):
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
            latest_onu_rx_power=-80.0,
            latest_olt_rx_power=1.0,
            latest_power_read_at=timezone.now(),
        )

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
        refresh_for_onus_mock.assert_not_called()

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

    # ------------------------------------------------------------------
    # get_items_by_key_prefix — DB-only (no API fallback)
    # ------------------------------------------------------------------

    @override_settings(ZABBIX_DB_ENABLED=True)
    @patch.object(ZabbixService, "_call")
    def test_get_items_by_key_prefix_uses_db_not_api(self, api_call_mock):
        """When ZABBIX_DB_ENABLED=True, get_items_by_key_prefix queries the DB
        directly and never calls the JSON-RPC API."""
        service = ZabbixService()
        with patch.object(
            service,
            "_get_latest_history_rows_from_db",
            return_value={
                9001: {"lastvalue": "online", "lastclock": 1772000100, "prevvalue": "offline"},
            },
        ) as db_history_mock:
            with patch("topology.services.zabbix_service.connections") as mock_conns:
                mock_cursor = mock_conns.__getitem__.return_value.cursor.return_value.__enter__.return_value
                mock_cursor.fetchall.return_value = [
                    (9001, 'onuStatusValue[1/1:1]', 'ONU 1/1:1 Status', 0, 1, 0),
                ]
                result = service.get_items_by_key_prefix("10001", 'onuStatusValue[')

        api_call_mock.assert_not_called()
        db_history_mock.assert_called_once()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["itemid"], "9001")
        self.assertEqual(result[0]["key_"], "onuStatusValue[1/1:1]")
        self.assertEqual(result[0]["name"], "ONU 1/1:1 Status")
        self.assertEqual(result[0]["lastvalue"], "online")
        self.assertEqual(result[0]["lastclock"], 1772000100)
        self.assertEqual(result[0]["state"], "0")

    @override_settings(ZABBIX_DB_ENABLED=False)
    @patch.object(ZabbixService, "_call")
    def test_get_items_by_key_prefix_returns_empty_when_db_disabled(self, api_call_mock):
        """When ZABBIX_DB_ENABLED=False, get_items_by_key_prefix returns []
        and does not call the API."""
        service = ZabbixService()
        result = service.get_items_by_key_prefix("10001", 'onuStatusValue[')

        self.assertEqual(result, [])
        api_call_mock.assert_not_called()
