from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from topology.models import OLT, OLTPON, OLTSlot, ONU, ONULog, VendorProfile
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

    def test_refresh_power_rejects_missing_vendor_power_templates(self):
        olt = self._create_olt(name='OLT-NO-POWER-TPL')
        response = self.client.post(f'/api/olts/{olt.id}/refresh_power/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['status'], 'error')
        self.assertEqual(
            sorted(response.data['missing_templates']),
            ['power.olt_rx_oid', 'power.onu_rx_oid'],
        )

    def test_snmp_check_rejects_snmp_v3_until_credentials_exist(self):
        olt = self._create_olt(name='OLT-SNMP-V3', snmp_version='v3')
        response = self.client.post(f'/api/olts/{olt.id}/snmp_check/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['reachable'])
        self.assertIn('SNMP v3', response.data['detail'])
