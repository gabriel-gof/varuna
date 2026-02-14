from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from topology.models import OLT, ONU, ONULog, VendorProfile
from topology.services.vendor_profile import map_status_code, parse_onu_index


def build_vendor_profile(name='C300'):
    return VendorProfile.objects.create(
        vendor='zte',
        model_name=name,
        oid_templates={
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
        },
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

    def test_status_mapping_unknown_defaults(self):
        mapped = map_status_code(None, {})
        self.assertEqual(mapped['status'], ONU.STATUS_UNKNOWN)
        self.assertEqual(mapped['reason'], ONULog.REASON_UNKNOWN)


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

        mock_get.return_value = {f'{status_oid}.{self.onu.snmp_index}': '4'}
        call_command('poll_onu_status', olt_id=self.olt.id)

        self.onu.refresh_from_db()
        open_log.refresh_from_db()
        self.assertEqual(self.onu.status, ONU.STATUS_ONLINE)
        self.assertIsNotNone(open_log.offline_until)
