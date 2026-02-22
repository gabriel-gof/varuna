import threading
from io import StringIO
from unittest.mock import ANY, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, VendorProfile
from topology.services.power_service import power_service
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
    def test_discovery_keeps_lost_onu_during_disable_grace_period(self, mock_walk):
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
        self.assertTrue(stale.is_active)
        self.assertEqual(stale.status, ONU.STATUS_ONLINE)

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
    @patch('topology.services.power_service.cache_service.set_onu_power', return_value=True)
    @patch('topology.services.power_service.snmp_service.get')
    def test_refresh_for_onus_uses_interval_aware_cache_ttl(self, mock_snmp_get, mock_cache_set, _mock_cache_get):
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
        self.assertTrue(mock_cache_set.called)
        called_ttl = mock_cache_set.call_args.kwargs.get('ttl')
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


class SettingsApiContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
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
