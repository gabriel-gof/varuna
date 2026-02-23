import threading
from datetime import timedelta
from io import StringIO
from unittest.mock import ANY, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, UserProfile, VendorProfile
from topology.services.power_service import power_service
from topology.services.snmp_service import SNMPService
from topology.management.commands.discover_onus import _normalize_serial
from topology.services.vendor_profile import map_status_code, parse_onu_index


def build_vendor_profile(
    name='C300',
    *,
    oid_templates=None,
    supports_onu_discovery=True,
    supports_onu_status=True,
    supports_power_monitoring=True,
):
    templates = oid_templates or {
        'indexing': {
            'pon_encoding': '0x11rrsspp',
            'slot_from': 'shelf',
            'pon_from': 'port',
        },
        'discovery': {
            'onu_name_oid': '1.3.6.1.4.1.test.1',
            'onu_serial_oid': '1.3.6.1.4.1.test.2',
            'onu_status_oid': '1.3.6.1.4.1.test.3',
            'deactivate_missing': True,
        },
        'status': {
            'onu_status_oid': '1.3.6.1.4.1.test.3',
            'get_chunk_size': 5,
            'status_map': {
                '4': {'status': 'online'},
                '5': {'status': 'offline', 'reason': 'dying_gasp'},
                '2': {'status': 'offline', 'reason': 'link_loss'},
            },
        },
    }
    return VendorProfile.objects.create(
        vendor='zte',
        model_name=name,
        oid_templates=templates,
        supports_onu_discovery=supports_onu_discovery,
        supports_onu_status=supports_onu_status,
        supports_power_monitoring=supports_power_monitoring,
        default_thresholds={'discovery_interval_minutes': 5, 'polling_interval_seconds': 60},
        is_active=True,
    )


class VendorProfileParserTests(TestCase):
    def test_parse_onu_index_legacy_zte(self):
        identity = parse_onu_index('285278465.6', {'pon_encoding': '0x11rrsspp', 'slot_from': 'shelf', 'pon_from': 'port'})
        self.assertIsNotNone(identity)
        self.assertEqual(identity['onu_id'], 6)
        self.assertEqual(identity['slot_id'], 1)
        self.assertEqual(identity['pon_id'], 1)

    def test_parse_onu_index_regex(self):
        identity = parse_onu_index(
            '10.7.44',
            {
                'regex': r'^(?P<slot_id>\d+)\.(?P<pon_id>\d+)\.(?P<onu_id>\d+)$',
            },
        )
        self.assertEqual(identity['slot_id'], 10)
        self.assertEqual(identity['pon_id'], 7)
        self.assertEqual(identity['onu_id'], 44)

    def test_parse_onu_index_with_fixed_slot(self):
        identity = parse_onu_index(
            '2.17',
            {
                'regex': r'^(?P<pon_id>\d+)\.(?P<onu_id>\d+)$',
                'fixed': {'slot_id': 1},
            },
        )
        self.assertEqual(identity['slot_id'], 1)
        self.assertEqual(identity['pon_id'], 2)
        self.assertEqual(identity['onu_id'], 17)

    def test_status_mapping_unknown_defaults(self):
        mapped = map_status_code(None, {})
        self.assertEqual(mapped['status'], ONU.STATUS_UNKNOWN)
        self.assertEqual(mapped['reason'], ONULog.REASON_UNKNOWN)

    def test_vsol_like_phase_state_mapping(self):
        status_map = {
            '1': {'status': 'offline', 'reason': 'link_loss'},
            '2': {'status': 'offline', 'reason': 'link_loss'},
            '3': {'status': 'online'},
            '4': {'status': 'offline', 'reason': 'dying_gasp'},
            '5': {'status': 'offline', 'reason': 'dying_gasp'},
        }

        los = map_status_code(1, status_map)
        self.assertEqual(los['status'], ONU.STATUS_OFFLINE)
        self.assertEqual(los['reason'], ONULog.REASON_LINK_LOSS)

        dying_gasp = map_status_code(4, status_map)
        self.assertEqual(dying_gasp['status'], ONU.STATUS_OFFLINE)
        self.assertEqual(dying_gasp['reason'], ONULog.REASON_DYING_GASP)

        online = map_status_code(3, status_map)
        self.assertEqual(online['status'], ONU.STATUS_ONLINE)
        self.assertEqual(online['reason'], '')


class DiscoveryCommandTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='DISCOVERY')
        self.olt = OLT.objects.create(
            name='OLT-DISC',
            vendor_profile=self.vendor,
            ip_address='10.0.0.1',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_discovery_deactivates_stale_onus(self, mock_walk):
        stale = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=99,
            snmp_index='285278465.99',
            name='stale',
            serial='stale',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']
        index = '285278465.1'

        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [{'oid': f'{base_serial_oid}.{index}', 'value': 'vendor,SERIAL-A'}],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        self.olt.refresh_from_db()
        stale.refresh_from_db()
        new_onu = ONU.objects.get(olt=self.olt, onu_id=1)

        self.assertTrue(self.olt.discovery_healthy)
        self.assertTrue(self.olt.snmp_reachable)
        self.assertTrue(new_onu.is_active)
        self.assertEqual(new_onu.status, ONU.STATUS_ONLINE)
        self.assertFalse(stale.is_active)
        self.assertEqual(stale.status, ONU.STATUS_UNKNOWN)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_discovery_ignores_disable_grace_and_deactivates_missing_immediately(self, mock_walk):
        templates = dict(self.vendor.oid_templates or {})
        discovery_cfg = dict(templates.get('discovery', {}))
        discovery_cfg['disable_lost_after_minutes'] = 60
        templates['discovery'] = discovery_cfg
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=['oid_templates'])

        stale = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=88,
            snmp_index='285278465.88',
            name='still-grace',
            serial='still-grace',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        base_name_oid = discovery_cfg['onu_name_oid']
        base_serial_oid = discovery_cfg['onu_serial_oid']
        base_status_oid = discovery_cfg['onu_status_oid']
        index = '285278465.1'
        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [{'oid': f'{base_serial_oid}.{index}', 'value': 'vendor,SERIAL-A'}],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        stale.refresh_from_db()
        self.assertFalse(stale.is_active)
        self.assertEqual(stale.status, ONU.STATUS_UNKNOWN)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_discovery_deletes_inactive_lost_onu_after_delete_window(self, mock_walk):
        templates = dict(self.vendor.oid_templates or {})
        discovery_cfg = dict(templates.get('discovery', {}))
        discovery_cfg['disable_lost_after_minutes'] = 0
        discovery_cfg['delete_lost_after_minutes'] = 5
        templates['discovery'] = discovery_cfg
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=['oid_templates'])

        stale = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=77,
            snmp_index='285278465.77',
            name='delete-me',
            serial='delete-me',
            status=ONU.STATUS_UNKNOWN,
            is_active=False,
        )
        stale.logs.create(
            offline_since=timezone.now() - timezone.timedelta(days=1),
            disconnect_reason=ONULog.REASON_UNKNOWN,
        )
        old_timestamp = timezone.now() - timezone.timedelta(minutes=10)
        ONU.objects.filter(id=stale.id).update(last_discovered_at=old_timestamp)

        base_name_oid = discovery_cfg['onu_name_oid']
        base_serial_oid = discovery_cfg['onu_serial_oid']
        base_status_oid = discovery_cfg['onu_status_oid']
        index = '285278465.1'
        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [{'oid': f'{base_serial_oid}.{index}', 'value': 'vendor,SERIAL-A'}],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        self.assertFalse(ONU.objects.filter(id=stale.id).exists())

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_discovery_preserves_existing_serial_on_partial_serial_walk(self, mock_walk):
        existing = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='old-name',
            serial='SERIAL-OLD',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']
        index = '285278465.1'

        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        existing.refresh_from_db()
        self.assertEqual(existing.serial, 'SERIAL-OLD')
        self.assertEqual(existing.name, 'client-a')
        self.assertTrue(existing.is_active)


class PollingCommandTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='POLLING')
        self.olt = OLT.objects.create(
            name='OLT-POLL',
            vendor_profile=self.vendor,
            ip_address='10.0.0.2',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )
        self.onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='client-poll',
            serial='SERIAL-POLL',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_marks_olt_unreachable_when_no_status_data(self, mock_get):
        mock_get.return_value = None

        call_command('poll_onu_status', olt_id=self.olt.id)

        self.olt.refresh_from_db()
        self.onu.refresh_from_db()

        self.assertFalse(self.olt.snmp_reachable)
        self.assertGreaterEqual(self.olt.snmp_failure_count, 1)
        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_tracks_online_offline_transitions(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']

        mock_get.return_value = {f'{status_oid}.{self.onu.snmp_index}': '5'}
        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        self.assertEqual(self.onu.status, ONU.STATUS_OFFLINE)
        open_log = ONULog.objects.get(onu=self.onu, offline_until__isnull=True)
        self.assertEqual(open_log.disconnect_reason, ONULog.REASON_DYING_GASP)
        self.assertIsNone(open_log.disconnect_window_start)
        self.assertIsNone(open_log.disconnect_window_end)

        mock_get.return_value = {f'{status_oid}.{self.onu.snmp_index}': '4'}
        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        open_log.refresh_from_db()
        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)
        self.assertIsNotNone(open_log.offline_until)

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_sets_disconnect_window_when_previous_online_snapshot_is_trusted(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']
        previous_poll = timezone.now() - timezone.timedelta(minutes=5)
        self.olt.last_poll_at = previous_poll
        self.olt.snmp_reachable = True
        self.olt.save(update_fields=['last_poll_at', 'snmp_reachable'])

        mock_get.return_value = {f'{status_oid}.{self.onu.snmp_index}': '5'}
        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        self.assertEqual(self.onu.status, ONU.STATUS_OFFLINE)
        open_log = ONULog.objects.get(onu=self.onu, offline_until__isnull=True)
        self.assertEqual(open_log.disconnect_reason, ONULog.REASON_DYING_GASP)
        self.assertIsNotNone(open_log.disconnect_window_start)
        self.assertIsNotNone(open_log.disconnect_window_end)
        self.assertEqual(open_log.disconnect_window_start, previous_poll)
        self.assertLessEqual(open_log.disconnect_window_start, open_log.disconnect_window_end)

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_keeps_disconnect_window_empty_when_previous_snapshot_is_not_trusted(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']
        self.olt.last_poll_at = timezone.now() - timezone.timedelta(minutes=5)
        self.olt.snmp_reachable = False
        self.olt.save(update_fields=['last_poll_at', 'snmp_reachable'])

        mock_get.return_value = {f'{status_oid}.{self.onu.snmp_index}': '5'}
        call_command('poll_onu_status', olt_id=self.olt.id)

        open_log = ONULog.objects.get(onu=self.onu, offline_until__isnull=True)
        self.assertIsNone(open_log.disconnect_window_start)
        self.assertIsNone(open_log.disconnect_window_end)

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_recovers_from_failed_large_chunks(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']
        onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index='285278465.2',
            name='client-b',
            serial='SERIAL-B',
            status=ONU.STATUS_UNKNOWN,
            is_active=True,
        )
        onu_c = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=3,
            snmp_index='285278465.3',
            name='client-c',
            serial='SERIAL-C',
            status=ONU.STATUS_UNKNOWN,
            is_active=True,
        )
        self.onu.status = ONU.STATUS_UNKNOWN
        self.onu.save(update_fields=['status'])

        def _side_effect(_olt, oids, **_kwargs):
            if len(oids) > 1:
                return None
            return {oids[0]: '4'}

        mock_get.side_effect = _side_effect

        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        onu_b.refresh_from_db()
        onu_c.refresh_from_db()

        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)
        self.assertEqual(onu_b.status, ONU.STATUS_ONLINE)
        self.assertEqual(onu_c.status, ONU.STATUS_ONLINE)
        self.assertTrue(any(len(call.args[1]) > 1 for call in mock_get.call_args_list))
        self.assertTrue(any(len(call.args[1]) == 1 for call in mock_get.call_args_list))
        self.olt.refresh_from_db()
        self.assertIsNotNone(self.olt.last_poll_at)

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_preserves_previous_status_when_partial_snapshot_missing(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']
        onu_missing = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index='285278465.2',
            name='client-missing',
            serial='SERIAL-MISSING',
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        open_log = ONULog.objects.create(
            onu=onu_missing,
            offline_since=timezone.now() - timezone.timedelta(minutes=10),
            disconnect_reason=ONULog.REASON_LINK_LOSS,
        )
        self.onu.status = ONU.STATUS_UNKNOWN
        self.onu.save(update_fields=['status'])

        def _side_effect(_olt, oids, **_kwargs):
            response = {}
            for oid in oids:
                if oid.endswith(f".{self.onu.snmp_index}"):
                    response[oid] = '4'
            return response

        mock_get.side_effect = _side_effect

        output = StringIO()
        call_command('poll_onu_status', olt_id=self.olt.id, stdout=output)

        self.onu.refresh_from_db()
        onu_missing.refresh_from_db()
        open_log.refresh_from_db()

        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)
        self.assertEqual(onu_missing.status, ONU.STATUS_OFFLINE)
        self.assertIsNone(open_log.offline_until)
        self.assertIn('missing=1', output.getvalue())
        self.assertIn('missing_preserved=1', output.getvalue())

    @patch('topology.management.commands.poll_onu_status.snmp_service.get')
    def test_poll_normalizes_snmp_index_lookup(self, mock_get):
        status_oid = self.vendor.oid_templates['status']['onu_status_oid']
        weird_index_onu = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index='.285278465.2.',
            name='client-weird',
            serial='SERIAL-WEIRD',
            status=ONU.STATUS_UNKNOWN,
            is_active=True,
        )
        self.onu.status = ONU.STATUS_UNKNOWN
        self.onu.save(update_fields=['status'])

        mock_get.return_value = {
            f'{status_oid}.285278465.1': '4',
            f'{status_oid}.285278465.2': '4',
        }

        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        weird_index_onu.refresh_from_db()

        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)
        self.assertEqual(weird_index_onu.status, ONU.STATUS_ONLINE)


class PowerServiceResilienceTests(TestCase):
    def setUp(self):
        vendor = build_vendor_profile(name='POWER-RESILIENCE')
        templates = dict(vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.90',
            'olt_rx_oid': '1.3.6.1.4.1.test.91',
        }
        vendor.oid_templates = templates
        vendor.save(update_fields=['oid_templates'])

        self.olt = OLT.objects.create(
            name='OLT-POWER-RESILIENCE',
            vendor_profile=vendor,
            ip_address='10.0.0.55',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )
        self.onu_a = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        self.onu_b = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index='285278465.2',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        self.onu_c = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=3,
            snmp_index='285278465.3',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        self.onu_offline = ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=4,
            snmp_index='285278465.4',
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        self.onus = [self.onu_a, self.onu_b, self.onu_c]

    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_recovers_from_failed_large_chunks(self, mock_snmp_get, _mock_cache_get, _mock_cache_set):
        def _side_effect(_olt, oids, **_kwargs):
            if len(oids) > 2:
                return None
            response = {}
            for oid in oids:
                if oid.startswith('1.3.6.1.4.1.test.90.'):
                    response[oid] = '5000'
                else:
                    response[oid] = '-22000'
            return response

        mock_snmp_get.side_effect = _side_effect

        with patch.object(power_service, 'chunk_size', 6):
            with patch.object(power_service, 'chunk_retry_attempts', 1):
                with patch.object(power_service, 'single_oid_retry_attempts', 1):
                    result_map = power_service.refresh_for_onus(self.onus, force_refresh=True)

        self.assertEqual(len(result_map), 3)
        for onu in self.onus:
            self.assertEqual(result_map[onu.id]['onu_rx_power'], -20.0)
            self.assertEqual(result_map[onu.id]['olt_rx_power'], -22.0)
            self.assertIsNotNone(result_map[onu.id]['power_read_at'])

        self.assertTrue(any(len(call.args[1]) > 2 for call in mock_snmp_get.call_args_list))
        self.assertTrue(any(len(call.args[1]) == 1 for call in mock_snmp_get.call_args_list))

    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_recovers_missing_varbinds_with_single_oid_retry(self, mock_snmp_get, _mock_cache_get, _mock_cache_set):
        def _side_effect(_olt, oids, **_kwargs):
            if len(oids) == 1:
                oid = oids[0]
                return {oid: '5000' if oid.startswith('1.3.6.1.4.1.test.90.') else '-22000'}

            if len(oids) == 2:
                first = oids[0]
                return {first: '5000' if first.startswith('1.3.6.1.4.1.test.90.') else '-22000'}

            response = {}
            for oid in oids:
                if oid.endswith('.1'):
                    continue
                response[oid] = '5000' if oid.startswith('1.3.6.1.4.1.test.90.') else '-22000'
            return response

        mock_snmp_get.side_effect = _side_effect

        with patch.object(power_service, 'chunk_size', 2):
            with patch.object(power_service, 'chunk_retry_attempts', 1):
                with patch.object(power_service, 'single_oid_retry_attempts', 1):
                    result_map = power_service.refresh_for_onus([self.onu_a], force_refresh=True)

        payload = result_map[self.onu_a.id]
        self.assertEqual(payload['onu_rx_power'], -20.0)
        self.assertEqual(payload['olt_rx_power'], -22.0)
        self.assertIsNotNone(payload['power_read_at'])
        self.assertTrue(any(len(call.args[1]) == 1 for call in mock_snmp_get.call_args_list))

    @override_settings(POWER_CACHE_TTL=60)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.cache_service.set_many_onu_power', return_value=True)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_uses_interval_aware_cache_ttl(self, mock_snmp_get, mock_cache_set_many, _mock_cache_get):
        self.olt.power_interval_seconds = 3600
        self.olt.save(update_fields=['power_interval_seconds'])

        def _side_effect(_olt, oids, **_kwargs):
            response = {}
            for oid in oids:
                response[oid] = '5000' if oid.startswith('1.3.6.1.4.1.test.90.') else '-22000'
            return response

        mock_snmp_get.side_effect = _side_effect

        with patch.object(power_service, 'chunk_size', 2):
            result_map = power_service.refresh_for_onus([self.onu_a], force_refresh=True)

        self.assertIn(self.onu_a.id, result_map)
        self.assertTrue(mock_cache_set_many.called)
        called_ttl = mock_cache_set_many.call_args.kwargs.get('ttl')
        self.assertEqual(called_ttl, 7200)

    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_skips_offline_onus(self, mock_snmp_get, _mock_cache_get, _mock_cache_set):
        def _side_effect(_olt, oids, **_kwargs):
            response = {}
            for oid in oids:
                response[oid] = '5000' if oid.startswith('1.3.6.1.4.1.test.90.') else '-22000'
            return response

        mock_snmp_get.side_effect = _side_effect

        with patch.object(power_service, 'chunk_size', 8):
            result_map = power_service.refresh_for_onus([self.onu_a, self.onu_offline], force_refresh=True)

        online_payload = result_map[self.onu_a.id]
        offline_payload = result_map[self.onu_offline.id]

        self.assertEqual(online_payload['onu_rx_power'], -20.0)
        self.assertEqual(online_payload['olt_rx_power'], -22.0)
        self.assertIsNotNone(online_payload['power_read_at'])

        self.assertIsNone(offline_payload['onu_rx_power'])
        self.assertIsNone(offline_payload['olt_rx_power'])
        self.assertIsNone(offline_payload['power_read_at'])
        self.assertEqual(offline_payload.get('skipped_reason'), 'offline')

        all_requested_oids = []
        for call in mock_snmp_get.call_args_list:
            all_requested_oids.extend(call.args[1])
        offline_index = str(self.onu_offline.snmp_index).strip('.')
        self.assertFalse(
            any(
                oid.endswith(f'.{offline_index}') or f'.{offline_index}.' in oid
                for oid in all_requested_oids
            )
        )

    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_supports_onu_only_power_oid(self, mock_snmp_get, _mock_cache_get, _mock_cache_set):
        templates = dict(self.olt.vendor_profile.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.90',
        }
        self.olt.vendor_profile.oid_templates = templates
        self.olt.vendor_profile.save(update_fields=['oid_templates'])

        def _side_effect(_olt, oids, **_kwargs):
            return {oid: '5000' for oid in oids}

        mock_snmp_get.side_effect = _side_effect

        with patch.object(power_service, 'chunk_size', 8):
            result_map = power_service.refresh_for_onus([self.onu_a], force_refresh=True)

        payload = result_map[self.onu_a.id]
        self.assertEqual(payload['onu_rx_power'], -20.0)
        self.assertIsNone(payload['olt_rx_power'])
        self.assertIsNotNone(payload['power_read_at'])

        all_requested_oids = []
        for call in mock_snmp_get.call_args_list:
            all_requested_oids.extend(call.args[1])
        self.assertTrue(all_requested_oids)
        self.assertTrue(all(oid.startswith('1.3.6.1.4.1.test.90.') for oid in all_requested_oids))

    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_onu_power', return_value=None)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_parses_dbm_string_values(self, mock_snmp_get, _mock_cache_get, _mock_cache_set):
        templates = dict(self.olt.vendor_profile.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.90',
        }
        self.olt.vendor_profile.oid_templates = templates
        self.olt.vendor_profile.save(update_fields=['oid_templates'])

        def _side_effect(_olt, oids, **_kwargs):
            return {oid: '-27.214(dBm)' for oid in oids}

        mock_snmp_get.side_effect = _side_effect

        result_map = power_service.refresh_for_onus([self.onu_a], force_refresh=True)
        payload = result_map[self.onu_a.id]
        self.assertEqual(payload['onu_rx_power'], -27.21)
        self.assertIsNone(payload['olt_rx_power'])
        self.assertIsNotNone(payload['power_read_at'])


    @patch('topology.services.power_service.cache_service.set_many_onu_power', return_value=True)
    @patch('topology.services.power_service.cache_service.get_many_onu_power')
    @patch('topology.services.power_service.snmp_service.get', return_value=None)
    def test_refresh_for_onus_keeps_cached_snapshot_when_forced_refresh_fails(
        self,
        _mock_snmp_get,
        mock_get_many_onu_power,
        mock_set_many_onu_power,
    ):
        cached_read_at = timezone.now().isoformat()
        mock_get_many_onu_power.return_value = {
            self.onu_a.id: {
                'onu_rx_power': -19.35,
                'olt_rx_power': -22.14,
                'power_read_at': cached_read_at,
            }
        }

        result_map = power_service.refresh_for_onus([self.onu_a], force_refresh=True)
        payload = result_map[self.onu_a.id]

        self.assertEqual(payload['onu_rx_power'], -19.35)
        self.assertEqual(payload['olt_rx_power'], -22.14)
        self.assertEqual(payload['power_read_at'], cached_read_at)
        self.assertTrue(mock_set_many_onu_power.called)
        cache_batch = mock_set_many_onu_power.call_args.args[1]
        self.assertEqual(cache_batch[self.onu_a.id]['power_read_at'], cached_read_at)

class SettingsApiContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        UserProfile.objects.create(user=self.user, role=UserProfile.ROLE_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.vendor = build_vendor_profile(name='SETTINGS-API')

    def _create_olt(self, **kwargs):
        payload = {
            'name': 'OLT-API-1',
            'vendor_profile': self.vendor,
            'ip_address': '10.0.0.10',
            'snmp_community': 'public',
            'snmp_port': 161,
            'snmp_version': 'v2c',
            'discovery_enabled': True,
            'polling_enabled': True,
            'discovery_interval_minutes': 240,
            'polling_interval_seconds': 300,
            'power_interval_seconds': 300,
            'is_active': True,
        }
        payload.update(kwargs)
        return OLT.objects.create(**payload)

    def test_create_rejects_invalid_runtime_config_values(self):
        response = self.client.post(
            '/api/olts/',
            {
                'name': 'OLT-INVALID',
                'vendor_profile': self.vendor.id,
                'protocol': 'snmp',
                'ip_address': '10.0.0.20',
                'snmp_community': '',
                'snmp_port': 70000,
                'snmp_version': 'v3',
                'discovery_interval_minutes': 0,
                'polling_interval_seconds': 0,
                'power_interval_seconds': 0,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('snmp_community', response.data)
        self.assertIn('snmp_port', response.data)
        self.assertIn('snmp_version', response.data)

    def test_create_rejects_non_positive_intervals(self):
        response = self.client.post(
            '/api/olts/',
            {
                'name': 'OLT-INVALID-INTERVALS',
                'vendor_profile': self.vendor.id,
                'protocol': 'snmp',
                'ip_address': '10.0.0.21',
                'snmp_community': 'public',
                'snmp_port': 161,
                'snmp_version': 'v2c',
                'discovery_interval_minutes': 0,
                'polling_interval_seconds': 0,
                'power_interval_seconds': 0,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('discovery_interval_minutes', response.data)

    def test_create_reactivates_inactive_olt_with_same_name(self):
        olt = self._create_olt(name='OLT-REUSE', is_active=False, ip_address='10.0.0.1')

        response = self.client.post(
            '/api/olts/',
            {
                'name': 'OLT-REUSE',
                'vendor_profile': self.vendor.id,
                'protocol': 'snmp',
                'ip_address': '10.0.0.99',
                'snmp_community': 'private',
                'snmp_port': 162,
                'snmp_version': 'v2c',
                'discovery_interval_minutes': 60,
                'polling_interval_seconds': 120,
                'power_interval_seconds': 180,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(OLT.objects.filter(name='OLT-REUSE').count(), 1)
        olt.refresh_from_db()
        self.assertTrue(olt.is_active)
        self.assertEqual(olt.ip_address, '10.0.0.99')
        self.assertEqual(olt.snmp_port, 162)
        self.assertEqual(olt.id, response.data['id'])

    def test_delete_soft_deactivates_olt_and_topology(self):
        olt = self._create_olt(name='OLT-SOFT-DELETE')
        slot = OLTSlot.objects.create(
            olt=olt,
            slot_id=1,
            slot_key='1',
            is_active=True,
        )
        pon = OLTPON.objects.create(
            olt=olt,
            slot=slot,
            pon_id=1,
            pon_key='1/1',
            is_active=True,
        )
        onu = ONU.objects.create(
            olt=olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        log = ONULog.objects.create(
            onu=onu,
            offline_since=timezone.now() - timezone.timedelta(minutes=5),
            disconnect_reason=ONULog.REASON_LINK_LOSS,
        )

        response = self.client.delete(f'/api/olts/{olt.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        olt.refresh_from_db()
        slot.refresh_from_db()
        pon.refresh_from_db()
        onu.refresh_from_db()
        log.refresh_from_db()

        self.assertFalse(olt.is_active)
        self.assertFalse(olt.discovery_enabled)
        self.assertFalse(olt.polling_enabled)
        self.assertFalse(slot.is_active)
        self.assertFalse(pon.is_active)
        self.assertFalse(onu.is_active)
        self.assertEqual(onu.status, ONU.STATUS_UNKNOWN)
        self.assertIsNotNone(log.offline_until)

    def test_include_topology_returns_disconnect_window_fields(self):
        olt = self._create_olt(name='OLT-TOPOLOGY-DISCONNECT-WINDOW')
        slot = OLTSlot.objects.create(
            olt=olt,
            slot_id=1,
            slot_key='1',
            is_active=True,
        )
        pon = OLTPON.objects.create(
            olt=olt,
            slot=slot,
            pon_id=1,
            pon_key='1/1',
            is_active=True,
        )
        onu = ONU.objects.create(
            olt=olt,
            slot_ref=slot,
            pon_ref=pon,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )
        window_start = timezone.now() - timezone.timedelta(minutes=5)
        window_end = timezone.now()
        ONULog.objects.create(
            onu=onu,
            offline_since=window_end,
            disconnect_reason=ONULog.REASON_LINK_LOSS,
            disconnect_window_start=window_start,
            disconnect_window_end=window_end,
        )

        response = self.client.get('/api/olts/?include_topology=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        rows = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertTrue(rows)
        payload = rows[0]
        onu_payload = payload['slots'][0]['pons'][0]['onus'][0]
        self.assertEqual(onu_payload['id'], onu.id)
        self.assertIn('disconnect_window_start', onu_payload)
        self.assertIn('disconnect_window_end', onu_payload)
        self.assertEqual(onu_payload['disconnect_window_start'], window_start.isoformat())
        self.assertEqual(onu_payload['disconnect_window_end'], window_end.isoformat())

    def test_run_polling_rejects_unsupported_vendor_capability(self):
        vendor = build_vendor_profile(
            name='NO-POLL',
            supports_onu_status=False,
        )
        olt = self._create_olt(name='OLT-NO-POLL', vendor_profile=vendor)
        response = self.client.post(f'/api/olts/{olt.id}/run_polling/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['status'], 'error')
        self.assertIn('does not support ONU status polling', response.data['detail'])

    @patch('topology.api.views.OLTViewSet._queue_background_olt_job', return_value=True)
    def test_run_discovery_background_returns_accepted(self, mock_queue):
        olt = self._create_olt(name='OLT-BG-DISCOVERY')
        response = self.client.post(
            f'/api/olts/{olt.id}/run_discovery/',
            {'background': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], 'accepted')
        self.assertEqual(response.data['olt_id'], olt.id)
        self.assertIn('scheduled', response.data['detail'].lower())
        mock_queue.assert_called_once()
        self.assertEqual(mock_queue.call_args.kwargs['kind'], 'discovery')
        self.assertEqual(mock_queue.call_args.kwargs['olt_id'], olt.id)
        self.assertTrue(callable(mock_queue.call_args.kwargs['runner']))

    @patch('topology.api.views.OLTViewSet._queue_background_olt_job', return_value=False)
    def test_run_polling_background_returns_already_running(self, mock_queue):
        olt = self._create_olt(name='OLT-BG-POLLING')
        response = self.client.post(
            f'/api/olts/{olt.id}/run_polling/',
            {'background': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], 'already_running')
        self.assertEqual(response.data['olt_id'], olt.id)
        mock_queue.assert_called_once()
        self.assertEqual(mock_queue.call_args.kwargs['kind'], 'polling')
        self.assertEqual(mock_queue.call_args.kwargs['olt_id'], olt.id)

    @patch('topology.api.views.call_command')
    def test_background_actions_are_serialized_per_olt(self, mock_call_command):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.55',
            'olt_rx_oid': '1.3.6.1.4.1.test.56',
        }
        vendor = build_vendor_profile(name='SETTINGS-BG-SERIALIZED', oid_templates=templates)
        olt = self._create_olt(name='OLT-BG-SERIALIZED', vendor_profile=vendor)

        started = threading.Event()
        release = threading.Event()

        def _side_effect(command_name, *args, **kwargs):
            if command_name == 'discover_onus':
                started.set()
                release.wait(timeout=1.5)
            return None

        mock_call_command.side_effect = _side_effect

        first = self.client.post(
            f'/api/olts/{olt.id}/run_discovery/',
            {'background': True},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(first.data['status'], 'accepted')
        self.assertTrue(started.wait(timeout=1.0))

        second = self.client.post(
            f'/api/olts/{olt.id}/run_polling/',
            {'background': True},
            format='json',
        )

        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.data['status'], 'already_running')
        self.assertIn('already running', second.data['detail'].lower())

        release.set()

    def test_refresh_power_rejects_missing_vendor_power_templates(self):
        olt = self._create_olt(name='OLT-NO-POWER-TPL')
        response = self.client.post(f'/api/olts/{olt.id}/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['status'], 'error')
        self.assertEqual(
            sorted(response.data['missing_templates']),
            ['power.onu_rx_oid'],
        )

    @patch('topology.api.views.OLTViewSet._queue_background_olt_job', return_value=True)
    def test_refresh_power_background_returns_accepted(self, mock_queue):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.50',
            'olt_rx_oid': '1.3.6.1.4.1.test.51',
        }
        power_vendor = build_vendor_profile(name='SETTINGS-POWER-BG', oid_templates=templates)
        olt = self._create_olt(name='OLT-BG-POWER', vendor_profile=power_vendor)
        response = self.client.post(
            f'/api/olts/{olt.id}/refresh_power/',
            {'background': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], 'accepted')
        self.assertEqual(response.data['olt_id'], olt.id)
        self.assertIn('scheduled', response.data['detail'].lower())
        mock_queue.assert_called_once()
        self.assertEqual(mock_queue.call_args.kwargs['kind'], 'power')
        self.assertEqual(mock_queue.call_args.kwargs['olt_id'], olt.id)
        self.assertTrue(callable(mock_queue.call_args.kwargs['runner']))

    @patch('topology.api.views.power_service.refresh_for_onus')
    @patch('topology.api.views.call_command')
    def test_refresh_power_runs_polling_when_status_snapshot_missing(self, mock_call_command, mock_refresh):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.61',
            'olt_rx_oid': '1.3.6.1.4.1.test.62',
        }
        power_vendor = build_vendor_profile(name='SETTINGS-POWER-PREPOLL', oid_templates=templates)
        olt = self._create_olt(name='OLT-POWER-PREPOLL', vendor_profile=power_vendor, last_poll_at=None)
        onu = ONU.objects.create(
            olt=olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_UNKNOWN,
            is_active=True,
        )
        mock_refresh.return_value = {
            onu.id: {
                'onu_id': onu.id,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'onu_rx_power': -20.10,
                'olt_rx_power': -22.00,
                'power_read_at': timezone.now().isoformat(),
            }
        }

        response = self.client.post(f'/api/olts/{olt.id}/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_call_command.assert_any_call('poll_onu_status', olt_id=olt.id, force=True, stdout=ANY)

    @patch('topology.api.views.power_service.refresh_for_onus')
    @patch('topology.api.views.call_command')
    def test_refresh_power_skips_polling_when_status_snapshot_exists(self, mock_call_command, mock_refresh):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.63',
            'olt_rx_oid': '1.3.6.1.4.1.test.64',
        }
        power_vendor = build_vendor_profile(name='SETTINGS-POWER-WITH-STATUS', oid_templates=templates)
        olt = self._create_olt(
            name='OLT-POWER-WITH-STATUS',
            vendor_profile=power_vendor,
            last_poll_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        onu = ONU.objects.create(
            olt=olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        mock_refresh.return_value = {
            onu.id: {
                'onu_id': onu.id,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'onu_rx_power': -20.10,
                'olt_rx_power': -22.00,
                'power_read_at': timezone.now().isoformat(),
            }
        }

        response = self.client.post(f'/api/olts/{olt.id}/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            any(call.args and call.args[0] == 'poll_onu_status' for call in mock_call_command.call_args_list)
        )

    @patch('topology.api.views.power_service.refresh_for_onus')
    def test_refresh_power_updates_power_schedule_fields(self, mock_refresh):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.10',
            'olt_rx_oid': '1.3.6.1.4.1.test.11',
        }
        power_vendor = build_vendor_profile(name='SETTINGS-POWER', oid_templates=templates)
        olt = self._create_olt(
            name='OLT-POWER-SCHEDULE',
            vendor_profile=power_vendor,
            power_interval_seconds=180,
        )
        onu = ONU.objects.create(
            olt=olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='client-power',
            serial='SERIAL-POWER',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        mock_refresh.return_value = {
            onu.id: {
                'onu_id': onu.id,
                'slot_id': onu.slot_id,
                'pon_id': onu.pon_id,
                'onu_number': onu.onu_id,
                'onu_rx_power': -20.10,
                'olt_rx_power': -22.00,
                'power_read_at': timezone.now().isoformat(),
            }
        }

        response = self.client.post(f'/api/olts/{olt.id}/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['attempted_count'], 1)
        self.assertEqual(response.data['skipped_not_online_count'], 0)
        self.assertEqual(response.data['skipped_offline_count'], 0)
        self.assertEqual(response.data['skipped_unknown_count'], 0)
        self.assertEqual(response.data['collected_count'], 1)
        self.assertTrue(response.data['last_power_at'])
        self.assertTrue(response.data['next_power_at'])

        olt.refresh_from_db()
        self.assertIsNotNone(olt.last_power_at)
        self.assertIsNotNone(olt.next_power_at)
        self.assertEqual(
            int((olt.next_power_at - olt.last_power_at).total_seconds()),
            olt.power_interval_seconds,
        )

    @patch('topology.api.views.power_service.refresh_for_onus')
    def test_refresh_power_all_runs_for_every_valid_olt(self, mock_refresh):
        templates = dict(self.vendor.oid_templates or {})
        templates['power'] = {
            'onu_rx_oid': '1.3.6.1.4.1.test.20',
            'olt_rx_oid': '1.3.6.1.4.1.test.21',
        }
        power_vendor = build_vendor_profile(name='SETTINGS-POWER-ALL', oid_templates=templates)
        olt_a = self._create_olt(name='OLT-POWER-A', vendor_profile=power_vendor)
        olt_b = self._create_olt(name='OLT-POWER-B', vendor_profile=power_vendor)

        onu_a = ONU.objects.create(
            olt=olt_a,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )
        onu_b = ONU.objects.create(
            olt=olt_b,
            slot_id=1,
            pon_id=1,
            onu_id=2,
            snmp_index='285278465.2',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        def _mock_refresh(onus, force_refresh=True):
            return {
                onu.id: {
                    'onu_id': onu.id,
                    'slot_id': onu.slot_id,
                    'pon_id': onu.pon_id,
                    'onu_number': onu.onu_id,
                    'onu_rx_power': -18.50,
                    'olt_rx_power': -21.20,
                    'power_read_at': timezone.now().isoformat(),
                }
                for onu in onus
            }

        mock_refresh.side_effect = _mock_refresh

        response = self.client.post('/api/olts/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['olt_count'], 2)
        self.assertEqual(response.data['completed_count'], 2)
        self.assertEqual(response.data['skipped_count'], 0)
        self.assertEqual(response.data['error_count'], 0)
        self.assertEqual(response.data['total_onu_count'], 2)
        self.assertEqual(response.data['total_attempted_count'], 2)
        self.assertEqual(response.data['total_skipped_not_online_count'], 0)
        self.assertEqual(response.data['total_skipped_offline_count'], 0)
        self.assertEqual(response.data['total_skipped_unknown_count'], 0)
        self.assertEqual(response.data['total_collected_count'], 2)

        olt_a.refresh_from_db()
        olt_b.refresh_from_db()
        self.assertIsNotNone(olt_a.last_power_at)
        self.assertIsNotNone(olt_b.last_power_at)
        self.assertIsNotNone(olt_a.next_power_at)
        self.assertIsNotNone(olt_b.next_power_at)

    def test_snmp_check_rejects_snmp_v3_until_credentials_exist(self):
        olt = self._create_olt(name='OLT-SNMP-V3', snmp_version='v3')
        response = self.client.post(f'/api/olts/{olt.id}/snmp_check/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['reachable'])
        self.assertIn('SNMP v3', response.data['detail'])

    @patch('topology.api.views.snmp_service.get', return_value=None)
    def test_snmp_check_returns_busy_when_maintenance_running(self, mock_get):
        """SNMP check should not mark OLT unreachable if a background job is in-flight."""
        olt = self._create_olt(name='OLT-BUSY')
        from topology.api.views import _background_jobs_by_olt
        _background_jobs_by_olt[olt.id] = 'power'
        try:
            response = self.client.post(f'/api/olts/{olt.id}/snmp_check/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(response.data['reachable'])
            self.assertTrue(response.data.get('busy'))
            olt.refresh_from_db()
            # Should NOT be marked unreachable — snmp_reachable stays as-is (None or True)
            self.assertNotEqual(olt.snmp_reachable, False)
        finally:
            _background_jobs_by_olt.pop(olt.id, None)

    @patch('topology.api.views.snmp_service.get', return_value=None)
    def test_snmp_check_marks_unreachable_when_no_maintenance(self, mock_get):
        """SNMP check should mark OLT unreachable when no background job is running."""
        olt = self._create_olt(name='OLT-DOWN')
        response = self.client.post(f'/api/olts/{olt.id}/snmp_check/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['reachable'])
        self.assertFalse(response.data.get('busy', False))
        olt.refresh_from_db()
        self.assertFalse(olt.snmp_reachable)


class DiscoveryPartialWalkGuardTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='GUARD')
        self.olt = OLT.objects.create(
            name='OLT-GUARD',
            vendor_profile=self.vendor,
            ip_address='10.0.0.50',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_partial_walk_skips_deactivation(self, mock_walk):
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Create 20 existing active ONUs
        for i in range(1, 21):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns only 1 ONU (5% of 20 — below 30% default threshold)
        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.285278465.1', 'value': 'client-1'}],
            [{'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,SERIAL-1'}],
            [{'oid': f'{base_status_oid}.285278465.1', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        # All 20 ONUs should still be active (deactivation skipped)
        active_count = ONU.objects.filter(olt=self.olt, is_active=True).count()
        self.assertEqual(active_count, 20)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_full_walk_proceeds_with_deactivation(self, mock_walk):
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Create 4 existing active ONUs
        for i in range(1, 5):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns 3 out of 4 (75% — above 30% threshold)
        name_rows = [{'oid': f'{base_name_oid}.285278465.{i}', 'value': f'client-{i}'} for i in range(1, 4)]
        serial_rows = [{'oid': f'{base_serial_oid}.285278465.{i}', 'value': f'vendor,SERIAL-{i}'} for i in range(1, 4)]
        status_rows = [{'oid': f'{base_status_oid}.285278465.{i}', 'value': '4'} for i in range(1, 4)]

        mock_walk.side_effect = [name_rows, serial_rows, status_rows]

        call_command('discover_onus', olt_id=self.olt.id)

        # ONU 4 should be deactivated
        onu4 = ONU.objects.get(olt=self.olt, onu_id=4)
        self.assertFalse(onu4.is_active)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_first_discovery_always_proceeds(self, mock_walk):
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # No existing ONUs — first discovery should always proceed
        index = '285278465.1'
        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [{'oid': f'{base_serial_oid}.{index}', 'value': 'vendor,SERIAL-A'}],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        self.assertEqual(ONU.objects.filter(olt=self.olt, is_active=True).count(), 1)


    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_partial_walk_guard_at_exact_boundary_does_not_skip(self, mock_walk):
        """Exactly 30% should NOT trigger the guard (guard triggers on strictly less than)."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Create 10 existing active ONUs
        for i in range(1, 11):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns exactly 3 out of 10 (30% — not strictly below 0.3 threshold)
        name_rows = [{'oid': f'{base_name_oid}.285278465.{i}', 'value': f'client-{i}'} for i in range(1, 4)]
        serial_rows = [{'oid': f'{base_serial_oid}.285278465.{i}', 'value': f'vendor,SERIAL-{i}'} for i in range(1, 4)]
        status_rows = [{'oid': f'{base_status_oid}.285278465.{i}', 'value': '4'} for i in range(1, 4)]

        mock_walk.side_effect = [name_rows, serial_rows, status_rows]

        call_command('discover_onus', olt_id=self.olt.id)

        # ONUs 4-10 should be deactivated (guard did NOT skip)
        for onu_id in range(4, 11):
            onu = ONU.objects.get(olt=self.olt, onu_id=onu_id)
            self.assertFalse(onu.is_active, f"ONU {onu_id} should be deactivated")

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_partial_walk_guard_custom_ratio(self, mock_walk):
        """Custom min_safe_ratio=0.9 should trigger guard more aggressively."""
        templates = dict(self.vendor.oid_templates or {})
        discovery_cfg = dict(templates.get('discovery', {}))
        discovery_cfg['min_safe_ratio'] = 0.9
        templates['discovery'] = discovery_cfg
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=['oid_templates'])

        base_name_oid = discovery_cfg['onu_name_oid']
        base_serial_oid = discovery_cfg['onu_serial_oid']
        base_status_oid = discovery_cfg['onu_status_oid']

        # Create 10 existing active ONUs
        for i in range(1, 11):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns 8 out of 10 (80% < 90% threshold → guard triggers)
        name_rows = [{'oid': f'{base_name_oid}.285278465.{i}', 'value': f'client-{i}'} for i in range(1, 9)]
        serial_rows = [{'oid': f'{base_serial_oid}.285278465.{i}', 'value': f'vendor,SERIAL-{i}'} for i in range(1, 9)]
        status_rows = [{'oid': f'{base_status_oid}.285278465.{i}', 'value': '4'} for i in range(1, 9)]

        mock_walk.side_effect = [name_rows, serial_rows, status_rows]

        call_command('discover_onus', olt_id=self.olt.id)

        # All 10 should still be active (guard skipped deactivation)
        active_count = ONU.objects.filter(olt=self.olt, is_active=True).count()
        self.assertEqual(active_count, 10)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_partial_walk_guard_still_upserts_discovered_onus(self, mock_walk):
        """When guard skips deactivation, discovered ONUs should still be created/updated."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Create 20 existing active ONUs
        for i in range(1, 21):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'old-client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns 2 ONUs (10% → guard triggers) but with updated names
        mock_walk.side_effect = [
            [
                {'oid': f'{base_name_oid}.285278465.1', 'value': 'new-client-1'},
                {'oid': f'{base_name_oid}.285278465.2', 'value': 'new-client-2'},
            ],
            [
                {'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,SERIAL-1'},
                {'oid': f'{base_serial_oid}.285278465.2', 'value': 'vendor,SERIAL-2'},
            ],
            [
                {'oid': f'{base_status_oid}.285278465.1', 'value': '4'},
                {'oid': f'{base_status_oid}.285278465.2', 'value': '4'},
            ],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        # Names should be updated despite guard
        onu1 = ONU.objects.get(olt=self.olt, onu_id=1)
        onu2 = ONU.objects.get(olt=self.olt, onu_id=2)
        self.assertEqual(onu1.name, 'new-client-1')
        self.assertEqual(onu2.name, 'new-client-2')
        # All 20 still active
        self.assertEqual(ONU.objects.filter(olt=self.olt, is_active=True).count(), 20)


class ReaderRolePermissionTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='READER-PERMISSIONS')
        self.admin_user = User.objects.create_user(username='admin-user', password='AdminPass123!')
        self.viewer_user = User.objects.create_user(username='viewer-user', password='ViewerPass123!')
        UserProfile.objects.create(user=self.admin_user, role=UserProfile.ROLE_ADMIN)
        UserProfile.objects.create(user=self.viewer_user, role=UserProfile.ROLE_VIEWER)

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin_user)

        self.viewer_client = APIClient()
        self.viewer_client.force_authenticate(user=self.viewer_user)

        self.olt = OLT.objects.create(
            name='OLT-READER-PERM',
            vendor_profile=self.vendor,
            ip_address='10.0.0.90',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )
        self.slot = OLTSlot.objects.create(
            olt=self.olt,
            slot_id=1,
            slot_key='1',
            is_active=True,
        )
        self.pon = OLTPON.objects.create(
            olt=self.olt,
            slot=self.slot,
            pon_id=1,
            pon_key='1/1',
            is_active=True,
        )
        self.onu = ONU.objects.create(
            olt=self.olt,
            slot_ref=self.slot,
            pon_ref=self.pon,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            serial='SERIAL-READER',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

    def test_viewer_can_read_topology_data(self):
        response = self.viewer_client.get('/api/olts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_viewer_cannot_create_or_modify_olts(self):
        create_response = self.viewer_client.post(
            '/api/olts/',
            {
                'name': 'OLT-BLOCKED',
                'vendor_profile': self.vendor.id,
                'protocol': 'snmp',
                'ip_address': '10.0.0.91',
                'snmp_community': 'public',
                'snmp_port': 161,
                'snmp_version': 'v2c',
                'discovery_interval_minutes': 240,
                'polling_interval_seconds': 300,
                'power_interval_seconds': 300,
            },
            format='json',
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

        patch_response = self.viewer_client.patch(
            f'/api/olts/{self.olt.id}/',
            {'name': 'OLT-RENAMED'},
            format='json',
        )
        self.assertEqual(patch_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_viewer_cannot_run_maintenance_actions(self):
        for endpoint in [
            f'/api/olts/{self.olt.id}/run_discovery/',
            f'/api/olts/{self.olt.id}/run_polling/',
            f'/api/olts/{self.olt.id}/refresh_power/',
            f'/api/olts/{self.olt.id}/snmp_check/',
            '/api/olts/refresh_power/',
        ]:
            response = self.viewer_client.post(endpoint, {}, format='json')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_viewer_cannot_patch_pon_description(self):
        response = self.viewer_client.patch(
            f'/api/pons/{self.pon.id}/',
            {'description': 'blocked'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_viewer_cannot_force_power_refresh_but_can_read_cached_power(self):
        refresh_response = self.viewer_client.post(
            '/api/onu/batch-power/',
            {
                'olt_id': self.olt.id,
                'slot_id': self.slot.slot_id,
                'pon_id': self.pon.pon_id,
                'refresh': True,
            },
            format='json',
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_403_FORBIDDEN)

        cached_response = self.viewer_client.post(
            '/api/onu/batch-power/',
            {
                'olt_id': self.olt.id,
                'slot_id': self.slot.slot_id,
                'pon_id': self.pon.pon_id,
                'refresh': False,
            },
            format='json',
        )
        self.assertEqual(cached_response.status_code, status.HTTP_200_OK)
        self.assertEqual(cached_response.data['count'], 1)


class DiscoveryBatchOperationsTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='BATCH')
        self.olt = OLT.objects.create(
            name='OLT-BATCH',
            vendor_profile=self.vendor,
            ip_address='10.0.0.60',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_batch_mixed_create_and_update(self, mock_walk):
        """Verifies bulk ops handle mix of new and existing ONUs in one pass."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Pre-create ONU 1 (will be updated)
        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='old-name',
            serial='OLD-SERIAL',
            status=ONU.STATUS_OFFLINE,
            is_active=True,
        )

        # Walk returns ONU 1 (update) and ONU 2 (create)
        mock_walk.side_effect = [
            [
                {'oid': f'{base_name_oid}.285278465.1', 'value': 'new-name'},
                {'oid': f'{base_name_oid}.285278465.2', 'value': 'client-2'},
            ],
            [
                {'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,NEW-SERIAL'},
                {'oid': f'{base_serial_oid}.285278465.2', 'value': 'vendor,SERIAL-2'},
            ],
            [
                {'oid': f'{base_status_oid}.285278465.1', 'value': '4'},
                {'oid': f'{base_status_oid}.285278465.2', 'value': '4'},
            ],
        ]

        output = StringIO()
        call_command('discover_onus', olt_id=self.olt.id, stdout=output)

        onu1 = ONU.objects.get(olt=self.olt, onu_id=1)
        onu2 = ONU.objects.get(olt=self.olt, onu_id=2)

        self.assertEqual(onu1.name, 'new-name')
        self.assertEqual(onu1.serial, 'NEW-SERIAL')
        self.assertEqual(onu1.status, ONU.STATUS_ONLINE)
        self.assertTrue(onu1.is_active)

        self.assertEqual(onu2.name, 'client-2')
        self.assertEqual(onu2.serial, 'SERIAL-2')
        self.assertTrue(onu2.is_active)

        self.assertIn('created=1', output.getvalue())
        self.assertIn('updated=1', output.getvalue())

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_batch_serial_preservation_on_empty_serial_walk(self, mock_walk):
        """bulk_update path must preserve old serial when new serial is empty."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='old-name',
            serial='KEEP-THIS-SERIAL',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        # Name walk returns data, serial walk returns empty for this ONU
        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.285278465.1', 'value': 'updated-name'}],
            [],  # empty serial walk
            [{'oid': f'{base_status_oid}.285278465.1', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        onu = ONU.objects.get(olt=self.olt, onu_id=1)
        self.assertEqual(onu.serial, 'KEEP-THIS-SERIAL')
        self.assertEqual(onu.name, 'updated-name')

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_batch_large_discovery(self, mock_walk):
        """Verify bulk create/update with many ONUs (simulates real OLT)."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Pre-create 50 ONUs (will be updated)
        for i in range(1, 51):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns 50 existing + 20 new = 70 total
        name_rows = [{'oid': f'{base_name_oid}.285278465.{i}', 'value': f'client-{i}'} for i in range(1, 71)]
        serial_rows = [{'oid': f'{base_serial_oid}.285278465.{i}', 'value': f'vendor,SERIAL-{i}'} for i in range(1, 71)]
        status_rows = [{'oid': f'{base_status_oid}.285278465.{i}', 'value': '4'} for i in range(1, 71)]

        mock_walk.side_effect = [name_rows, serial_rows, status_rows]

        output = StringIO()
        call_command('discover_onus', olt_id=self.olt.id, stdout=output)

        self.assertEqual(ONU.objects.filter(olt=self.olt, is_active=True).count(), 70)
        self.assertIn('created=20', output.getvalue())
        self.assertIn('updated=50', output.getvalue())

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_dry_run_still_works_with_batch(self, mock_walk):
        """dry-run should not write to DB but still report counts."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        ONU.objects.create(
            olt=self.olt,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            name='existing',
            serial='SERIAL-1',
            status=ONU.STATUS_ONLINE,
            is_active=True,
        )

        mock_walk.side_effect = [
            [
                {'oid': f'{base_name_oid}.285278465.1', 'value': 'existing'},
                {'oid': f'{base_name_oid}.285278465.2', 'value': 'new-onu'},
            ],
            [
                {'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,SERIAL-1'},
                {'oid': f'{base_serial_oid}.285278465.2', 'value': 'vendor,SERIAL-2'},
            ],
            [
                {'oid': f'{base_status_oid}.285278465.1', 'value': '4'},
                {'oid': f'{base_status_oid}.285278465.2', 'value': '4'},
            ],
        ]

        output = StringIO()
        call_command('discover_onus', olt_id=self.olt.id, dry_run=True, stdout=output)

        # New ONU should NOT exist in DB
        self.assertFalse(ONU.objects.filter(olt=self.olt, onu_id=2).exists())
        self.assertIn('created=1', output.getvalue())
        self.assertIn('updated=1', output.getvalue())


class WalkTimeoutTests(TestCase):
    def test_walk_uses_custom_timeout_parameter(self):
        """walk() passes timeout/retries to UdpTransportTarget.create()."""
        service = SNMPService()

        class MockOLT:
            ip_address = '10.0.0.1'
            snmp_port = 161
            snmp_community = 'public'
            snmp_version = 'v2c'
            name = 'test-olt'

        from unittest.mock import AsyncMock, MagicMock

        create_mock = AsyncMock(return_value=MagicMock())
        mock_transport = MagicMock()
        mock_transport.create = create_mock

        async def mock_bulk_cmd(engine, auth, transport, ctx, non_rep, max_rep, *var_binds, **kwargs):
            return None, None, None, []

        original_modules = service._pysnmp
        try:
            service._pysnmp = {
                'SnmpEngine': MagicMock,
                'CommunityData': lambda *a, **kw: MagicMock(),
                'UdpTransportTarget': mock_transport,
                'ContextData': MagicMock,
                'ObjectType': lambda *a: MagicMock(),
                'ObjectIdentity': lambda *a: MagicMock(),
                'getCmd': None,
                'nextCmd': None,
                'bulkCmd': mock_bulk_cmd,
            }

            service.walk(MockOLT(), '1.3.6.1.4.1.test.1', timeout=45.0, retries=2)

            create_mock.assert_called_once()
            call_kwargs = create_mock.call_args
            self.assertEqual(call_kwargs.kwargs.get('timeout') or call_kwargs[1].get('timeout'), 45.0)
            self.assertEqual(call_kwargs.kwargs.get('retries') or call_kwargs[1].get('retries'), 2)
        finally:
            service._pysnmp = original_modules

    def test_walk_default_timeout_is_30s(self):
        """walk() defaults to timeout=30.0, retries=0."""
        service = SNMPService()

        class MockOLT:
            ip_address = '10.0.0.1'
            snmp_port = 161
            snmp_community = 'public'
            snmp_version = 'v2c'
            name = 'test-olt'

        from unittest.mock import AsyncMock, MagicMock

        create_mock = AsyncMock(return_value=MagicMock())
        mock_transport = MagicMock()
        mock_transport.create = create_mock

        async def mock_bulk_cmd(engine, auth, transport, ctx, non_rep, max_rep, *var_binds, **kwargs):
            return None, None, None, []

        original_modules = service._pysnmp
        try:
            service._pysnmp = {
                'SnmpEngine': MagicMock,
                'CommunityData': lambda *a, **kw: MagicMock(),
                'UdpTransportTarget': mock_transport,
                'ContextData': MagicMock,
                'ObjectType': lambda *a: MagicMock(),
                'ObjectIdentity': lambda *a: MagicMock(),
                'getCmd': None,
                'nextCmd': None,
                'bulkCmd': mock_bulk_cmd,
            }

            service.walk(MockOLT(), '1.3.6.1.4.1.test.1')

            create_mock.assert_called_once()
            call_kwargs = create_mock.call_args
            self.assertEqual(call_kwargs.kwargs.get('timeout') or call_kwargs[1].get('timeout'), 30.0)
            self.assertEqual(call_kwargs.kwargs.get('retries') or call_kwargs[1].get('retries'), 0)
        finally:
            service._pysnmp = original_modules


class DiscoveryGhostFilteringTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='GHOST')
        self.olt = OLT.objects.create(
            name='OLT-GHOST',
            vendor_profile=self.vendor,
            ip_address='10.0.0.70',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_ghost_indices_filtered_during_discovery(self, mock_walk):
        """Empty name+serial indices are excluded; those ONUs get deactivated."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Pre-create 2 ONUs
        for i in range(1, 3):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns ONU 1 (real) + index 99 (ghost: empty name and serial)
        # ONU 2 is missing from walk → should be deactivated
        mock_walk.side_effect = [
            [
                {'oid': f'{base_name_oid}.285278465.1', 'value': 'client-1'},
                {'oid': f'{base_name_oid}.285278465.99', 'value': ''},
            ],
            [
                {'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,SERIAL-1'},
                {'oid': f'{base_serial_oid}.285278465.99', 'value': ''},
            ],
            [
                {'oid': f'{base_status_oid}.285278465.1', 'value': '4'},
                {'oid': f'{base_status_oid}.285278465.99', 'value': '1'},
            ],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        # ONU 1 still active, ONU 2 deactivated (missing), ghost index 99 not created
        onu1 = ONU.objects.get(olt=self.olt, onu_id=1)
        onu2 = ONU.objects.get(olt=self.olt, onu_id=2)
        self.assertTrue(onu1.is_active)
        self.assertFalse(onu2.is_active)
        self.assertFalse(ONU.objects.filter(olt=self.olt, onu_id=99).exists())

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_index_with_name_only_is_not_ghost(self, mock_walk):
        """An index with name but no serial is valid, not a ghost."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.285278465.1', 'value': 'has-name'}],
            [],  # serial walk returns nothing for this index
            [{'oid': f'{base_status_oid}.285278465.1', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        onu = ONU.objects.get(olt=self.olt, onu_id=1)
        self.assertTrue(onu.is_active)
        self.assertEqual(onu.name, 'has-name')

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_index_with_serial_only_is_not_ghost(self, mock_walk):
        """An index with serial but no name is valid, not a ghost."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        mock_walk.side_effect = [
            [],  # name walk returns nothing for this index
            [{'oid': f'{base_serial_oid}.285278465.1', 'value': 'vendor,HAS-SERIAL'}],
            [{'oid': f'{base_status_oid}.285278465.1', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        onu = ONU.objects.get(olt=self.olt, onu_id=1)
        self.assertTrue(onu.is_active)
        self.assertEqual(onu.serial, 'HAS-SERIAL')


class DiscoveryDefaultMinSafeRatioTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='RATIO')
        self.olt = OLT.objects.create(
            name='OLT-RATIO',
            vendor_profile=self.vendor,
            ip_address='10.0.0.80',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_default_min_safe_ratio_is_30_percent(self, mock_walk):
        """Default min_safe_ratio is 0.3. Walk returning 25% should trigger guard."""
        base_name_oid = self.vendor.oid_templates['discovery']['onu_name_oid']
        base_serial_oid = self.vendor.oid_templates['discovery']['onu_serial_oid']
        base_status_oid = self.vendor.oid_templates['discovery']['onu_status_oid']

        # Create 20 existing active ONUs
        for i in range(1, 21):
            ONU.objects.create(
                olt=self.olt,
                slot_id=1,
                pon_id=1,
                onu_id=i,
                snmp_index=f'285278465.{i}',
                name=f'client-{i}',
                serial=f'SERIAL-{i}',
                status=ONU.STATUS_ONLINE,
                is_active=True,
            )

        # Walk returns 5 out of 20 (25% < 30% default threshold → guard triggers)
        name_rows = [{'oid': f'{base_name_oid}.285278465.{i}', 'value': f'client-{i}'} for i in range(1, 6)]
        serial_rows = [{'oid': f'{base_serial_oid}.285278465.{i}', 'value': f'vendor,SERIAL-{i}'} for i in range(1, 6)]
        status_rows = [{'oid': f'{base_status_oid}.285278465.{i}', 'value': '4'} for i in range(1, 6)]

        mock_walk.side_effect = [name_rows, serial_rows, status_rows]

        call_command('discover_onus', olt_id=self.olt.id)

        # All 20 should still be active (guard skipped deactivation)
        active_count = ONU.objects.filter(olt=self.olt, is_active=True).count()
        self.assertEqual(active_count, 20)

    @patch('topology.management.commands.discover_onus.snmp_service.walk')
    def test_walk_timeout_configurable_from_vendor_profile(self, mock_walk):
        """Discovery reads walk_timeout_seconds from vendor config and passes to walks."""
        templates = dict(self.vendor.oid_templates or {})
        discovery_cfg = dict(templates.get('discovery', {}))
        discovery_cfg['walk_timeout_seconds'] = 60
        templates['discovery'] = discovery_cfg
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=['oid_templates'])

        base_name_oid = discovery_cfg['onu_name_oid']
        base_serial_oid = discovery_cfg['onu_serial_oid']
        base_status_oid = discovery_cfg['onu_status_oid']
        index = '285278465.1'

        mock_walk.side_effect = [
            [{'oid': f'{base_name_oid}.{index}', 'value': 'client-a'}],
            [{'oid': f'{base_serial_oid}.{index}', 'value': 'vendor,SERIAL-A'}],
            [{'oid': f'{base_status_oid}.{index}', 'value': '4'}],
        ]

        call_command('discover_onus', olt_id=self.olt.id)

        # Verify walk was called with timeout=60.0
        for call in mock_walk.call_args_list:
            self.assertEqual(call.kwargs.get('timeout'), 60.0)


class WalkIterationCapTests(TestCase):
    def test_walk_stops_at_max_walk_rows(self):
        service = SNMPService()

        # Build a mock OLT
        class MockOLT:
            ip_address = '10.0.0.1'
            snmp_port = 161
            snmp_community = 'public'
            snmp_version = 'v2c'
            name = 'test-olt'

        base_oid = '1.3.6.1.4.1.test.99'
        counter = {'i': 0}

        async def mock_bulk_cmd(engine, auth, transport, ctx, non_rep, max_rep, *var_binds, **kwargs):
            binds = []
            for _ in range(max_rep):
                counter['i'] += 1
                from unittest.mock import MagicMock
                vb = MagicMock()
                vb.__getitem__ = lambda self, idx: (
                    MagicMock(
                        __str__=lambda s: f'{base_oid}.{counter["i"]}',
                    ) if idx == 0 else MagicMock(prettyPrint=lambda: str(counter['i']))
                )
                binds.append(vb)
            return None, None, None, binds

        # Patch the modules to use our mock
        original_modules = service._pysnmp
        try:
            from unittest.mock import AsyncMock, MagicMock
            mock_transport = MagicMock()
            mock_transport.create = AsyncMock(return_value=MagicMock())

            service._pysnmp = {
                'SnmpEngine': MagicMock,
                'CommunityData': lambda *a, **kw: MagicMock(),
                'UdpTransportTarget': mock_transport,
                'ContextData': MagicMock,
                'ObjectType': lambda *a: MagicMock(),
                'ObjectIdentity': lambda *a: MagicMock(),
                'getCmd': None,
                'nextCmd': None,
                'bulkCmd': mock_bulk_cmd,
            }

            results = service.walk(MockOLT(), base_oid, max_walk_rows=50)
            self.assertLessEqual(len(results), 75)  # may overshoot by one bulk batch
            self.assertGreaterEqual(len(results), 50)
        finally:
            service._pysnmp = original_modules


class NormalizeSerialTests(TestCase):
    def test_normalize_serial_uppercases(self):
        self.assertEqual(_normalize_serial("FHTT6a0e1cfa"), "FHTT6A0E1CFA")

    def test_normalize_serial_strips_na_sentinel(self):
        self.assertEqual(_normalize_serial("N/A"), "")

    def test_normalize_serial_strips_sentinel_case_insensitive(self):
        self.assertEqual(_normalize_serial("n/a"), "")
        self.assertEqual(_normalize_serial("None"), "")
        self.assertEqual(_normalize_serial("null"), "")
        self.assertEqual(_normalize_serial("NA"), "")
        self.assertEqual(_normalize_serial("--"), "")
        self.assertEqual(_normalize_serial("-"), "")

    def test_normalize_serial_strips_vendor_prefix_and_uppercases(self):
        self.assertEqual(_normalize_serial("vendor,serial-a"), "SERIAL-A")

    def test_normalize_serial_preserves_empty(self):
        self.assertEqual(_normalize_serial(""), "")
        self.assertEqual(_normalize_serial(None), "")


class AuthenticationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='operator1',
            password='VarunaPass123!',
        )
        UserProfile.objects.create(user=self.user, role=UserProfile.ROLE_VIEWER)

    def _login(self, password='VarunaPass123!'):
        return self.client.post(
            '/api/auth/login/',
            {'username': self.user.username, 'password': password},
            format='json',
        )

    def _auth_header(self, token):
        return {'HTTP_AUTHORIZATION': f'Token {token}'}

    def test_login_returns_token_and_user_payload(self):
        response = self._login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['user']['username'], self.user.username)
        self.assertEqual(response.data['user']['role'], UserProfile.ROLE_VIEWER)
        self.assertFalse(response.data['user']['can_modify_settings'])
        self.assertTrue(Token.objects.filter(key=response.data['token'], user=self.user).exists())

    def test_login_rejects_invalid_credentials(self):
        response = self._login(password='wrong-password')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_authenticated_user(self):
        login = self._login()
        token = login.data['token']
        response = self.client.get('/api/auth/me/', **self._auth_header(token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['role'], UserProfile.ROLE_VIEWER)
        self.assertFalse(response.data['can_modify_settings'])

    def test_logout_revokes_token(self):
        login = self._login()
        token = login.data['token']
        logout = self.client.post('/api/auth/logout/', {}, format='json', **self._auth_header(token))
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(key=token).exists())

        me = self.client.get('/api/auth/me/', **self._auth_header(token))
        self.assertEqual(me.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_rotates_token_and_invalidates_old_password(self):
        login = self._login()
        old_token = login.data['token']

        change = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'VarunaPass123!',
                'new_password': 'VarunaPass456!',
            },
            format='json',
            **self._auth_header(old_token),
        )
        self.assertEqual(change.status_code, status.HTTP_200_OK)
        self.assertEqual(change.data['detail'], 'Password updated.')
        self.assertIn('token', change.data)
        self.assertNotEqual(old_token, change.data['token'])

        me_old = self.client.get('/api/auth/me/', **self._auth_header(old_token))
        self.assertEqual(me_old.status_code, status.HTTP_401_UNAUTHORIZED)

        me_new = self.client.get('/api/auth/me/', **self._auth_header(change.data['token']))
        self.assertEqual(me_new.status_code, status.HTTP_200_OK)

        old_login = self._login(password='VarunaPass123!')
        self.assertEqual(old_login.status_code, status.HTTP_401_UNAUTHORIZED)
        new_login = self._login(password='VarunaPass456!')
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

    def test_change_password_validates_policy(self):
        login = self._login()
        token = login.data['token']

        response = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'VarunaPass123!',
                'new_password': '123',
            },
            format='json',
            **self._auth_header(token),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', response.data)


class EnsureAuthUserCommandTests(TestCase):
    def test_command_creates_user_profile_and_superuser(self):
        call_command(
            'ensure_auth_user',
            username='bootstrap',
            password='BootstrapPass123!',
            role=UserProfile.ROLE_ADMIN,
            superuser=True,
        )

        user = User.objects.get(username='bootstrap')
        self.assertTrue(user.check_password('BootstrapPass123!'))
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.profile.role, UserProfile.ROLE_ADMIN)

    def test_command_updates_password_only_with_force_flag(self):
        user = User.objects.create_user(username='existing', password='OldPass123!')
        UserProfile.objects.create(user=user, role=UserProfile.ROLE_VIEWER)

        call_command(
            'ensure_auth_user',
            username='existing',
            password='NewPass123!',
            role=UserProfile.ROLE_OPERATOR,
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPass123!'))
        self.assertEqual(user.profile.role, UserProfile.ROLE_OPERATOR)

        call_command(
            'ensure_auth_user',
            username='existing',
            password='NewPass123!',
            role=UserProfile.ROLE_OPERATOR,
            force_password=True,
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewPass123!'))


class PollingCommandSchedulingTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='POLL-SCHED')
        self.olt_due = OLT.objects.create(
            name='OLT-POLL-DUE',
            vendor_profile=self.vendor,
            ip_address='10.0.10.1',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            polling_enabled=True,
            is_active=True,
        )
        self.olt_not_due = OLT.objects.create(
            name='OLT-POLL-NOT-DUE',
            vendor_profile=self.vendor,
            ip_address='10.0.10.2',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            polling_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.poll_onu_status.Command._poll_for_olt')
    def test_poll_command_runs_only_due_olts_by_default(self, poll_mock):
        now = timezone.now()
        self.olt_due.next_poll_at = now - timedelta(seconds=10)
        self.olt_due.save(update_fields=['next_poll_at'])
        self.olt_not_due.next_poll_at = now + timedelta(minutes=5)
        self.olt_not_due.save(update_fields=['next_poll_at'])

        call_command('poll_onu_status')

        self.assertEqual(poll_mock.call_count, 1)
        called_olt = poll_mock.call_args[0][0]
        self.assertEqual(called_olt.id, self.olt_due.id)

    @patch('topology.management.commands.poll_onu_status.Command._poll_for_olt')
    def test_poll_command_force_runs_not_due_olt(self, poll_mock):
        self.olt_not_due.next_poll_at = timezone.now() + timedelta(minutes=20)
        self.olt_not_due.save(update_fields=['next_poll_at'])

        call_command('poll_onu_status', olt_id=self.olt_not_due.id, force=True)

        self.assertEqual(poll_mock.call_count, 1)
        called_olt = poll_mock.call_args[0][0]
        self.assertEqual(called_olt.id, self.olt_not_due.id)

    @patch('topology.management.commands.poll_onu_status.mark_olt_unreachable')
    @patch('topology.management.commands.poll_onu_status.Command._fetch_status_chunk_resilient')
    def test_poll_runtime_budget_stops_before_snmp_fetch(self, fetch_mock, mark_unreachable_mock):
        templates = dict(self.vendor.oid_templates or {})
        status_cfg = dict(templates.get('status') or {})
        status_cfg['max_runtime_seconds'] = 30
        templates['status'] = status_cfg
        self.vendor.oid_templates = templates
        self.vendor.save(update_fields=['oid_templates'])

        ONU.objects.create(
            olt=self.olt_due,
            slot_id=1,
            pon_id=1,
            onu_id=1,
            snmp_index='285278465.1',
            serial='POLL-RUNTIME-1',
            status=ONU.STATUS_UNKNOWN,
            is_active=True,
        )

        with patch('topology.management.commands.poll_onu_status.time.monotonic', side_effect=[0.0, 31.0, 31.1]):
            call_command('poll_onu_status', olt_id=self.olt_due.id, force=True)

        fetch_mock.assert_not_called()
        self.assertTrue(mark_unreachable_mock.called)
        error_detail = mark_unreachable_mock.call_args.kwargs.get('error', '')
        self.assertIn('runtime_exhausted=True', error_detail)


class DiscoveryCommandSchedulingTests(TestCase):
    def setUp(self):
        self.vendor = build_vendor_profile(name='DISCOVERY-SCHED')
        self.olt_due = OLT.objects.create(
            name='OLT-DISCOVERY-DUE',
            vendor_profile=self.vendor,
            ip_address='10.0.11.1',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            is_active=True,
        )
        self.olt_not_due = OLT.objects.create(
            name='OLT-DISCOVERY-NOT-DUE',
            vendor_profile=self.vendor,
            ip_address='10.0.11.2',
            snmp_community='public',
            snmp_port=161,
            snmp_version='v2c',
            discovery_enabled=True,
            is_active=True,
        )

    @patch('topology.management.commands.discover_onus.Command._discover_for_olt')
    def test_discovery_command_runs_only_due_olts_by_default(self, discover_mock):
        now = timezone.now()
        self.olt_due.next_discovery_at = now - timedelta(minutes=1)
        self.olt_due.save(update_fields=['next_discovery_at'])
        self.olt_not_due.next_discovery_at = now + timedelta(minutes=30)
        self.olt_not_due.save(update_fields=['next_discovery_at'])

        call_command('discover_onus')

        self.assertEqual(discover_mock.call_count, 1)
        called_olt = discover_mock.call_args[0][0]
        self.assertEqual(called_olt.id, self.olt_due.id)

    @patch('topology.management.commands.discover_onus.Command._discover_for_olt')
    def test_discovery_command_force_runs_not_due_olt(self, discover_mock):
        self.olt_not_due.next_discovery_at = timezone.now() + timedelta(hours=2)
        self.olt_not_due.save(update_fields=['next_discovery_at'])

        call_command('discover_onus', olt_id=self.olt_not_due.id, force=True)

        self.assertEqual(discover_mock.call_count, 1)
        called_olt = discover_mock.call_args[0][0]
        self.assertEqual(called_olt.id, self.olt_not_due.id)
