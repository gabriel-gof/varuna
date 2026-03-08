from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from topology.models import OLTPON, OLT, OLTSlot, ONU, ONULog, UserProfile, VendorProfile
from topology.services.history_service import get_latest_power_snapshot_map
from topology.services.power_values import normalize_power_value
from topology.services.vendor_profile import get_default_protocol, supports_olt_rx_power


def _live_count(fallback_fn):
    return int(fallback_fn())


def _cached_count(obj, attr_name, fallback_fn):
    cached_value = getattr(obj, attr_name, None)
    if cached_value is not None:
        return int(cached_value)
    return _live_count(fallback_fn)


class VendorProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for VendorProfile
    """
    supports_olt_rx_power = serializers.SerializerMethodField()
    default_protocol = serializers.SerializerMethodField()

    class Meta:
        model = VendorProfile
        fields = [
            'id',
            'vendor',
            'model_name',
            'description',
            'supports_onu_discovery',
            'supports_onu_status',
            'supports_power_monitoring',
            'supports_disconnect_reason',
            'supports_olt_rx_power',
            'default_protocol',
        ]
        read_only_fields = ['id']

    def get_supports_olt_rx_power(self, obj):
        return supports_olt_rx_power(obj)

    def get_default_protocol(self, obj):
        return get_default_protocol(obj)


# ============================================
# Nested Topology Serializers
# ============================================

class ONUNestedSerializer(serializers.ModelSerializer):
    """
    Nested serializer for ONU within topology tree
    """

    onu_number = serializers.IntegerField(source='onu_id', read_only=True)
    client_name = serializers.CharField(source='name', read_only=True)
    serial_number = serializers.CharField(source='serial', read_only=True)
    disconnect_reason = serializers.ReadOnlyField()
    offline_since = serializers.ReadOnlyField()
    disconnect_window_start = serializers.ReadOnlyField()
    disconnect_window_end = serializers.ReadOnlyField()
    onu_rx_power = serializers.ReadOnlyField()
    olt_rx_power = serializers.ReadOnlyField()
    power_read_at = serializers.ReadOnlyField()

    class Meta:
        model = ONU
        fields = [
            'id',
            'onu_number',
            'name',
            'client_name',
            'serial_number',
            'status',
            'disconnect_reason',
            'offline_since',
            'disconnect_window_start',
            'disconnect_window_end',
            'onu_rx_power',
            'olt_rx_power',
            'power_read_at',
            'last_discovered_at',
        ]
        read_only_fields = fields

    def _get_active_log(self, obj):
        prefetched = getattr(obj, 'active_logs', None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None

        log = ONULog.objects.filter(
            onu=obj,
            offline_until__isnull=True,
        ).order_by('-offline_since').first()
        return log

    def _get_power(self, obj):
        power_map = self.context.get('power_map') if isinstance(self.context, dict) else None
        if isinstance(power_map, dict) and obj.id in power_map:
            return power_map[obj.id] or {}
        return get_latest_power_snapshot_map([obj.id]).get(obj.id) or {}

    def _supports_olt_rx_power(self, obj):
        return supports_olt_rx_power(obj.olt)

    @staticmethod
    def _as_iso(value):
        if not value:
            return None
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return value

    def to_representation(self, obj):
        active_log = self._get_active_log(obj)
        power = self._get_power(obj)
        supports_olt_rx_power = self._supports_olt_rx_power(obj)

        disconnect_reason = None
        offline_since = None
        disconnect_window_start = None
        disconnect_window_end = None

        if active_log:
            disconnect_reason = active_log.disconnect_reason
            offline_since = self._as_iso(active_log.offline_since)
            window_anchor = (
                active_log.disconnect_window_end
                or active_log.disconnect_window_start
                or active_log.offline_since
            )
            disconnect_window_start = self._as_iso(active_log.disconnect_window_start or window_anchor)
            disconnect_window_end = self._as_iso(active_log.disconnect_window_end or window_anchor)
        elif obj.status == ONU.STATUS_OFFLINE:
            disconnect_reason = ONULog.REASON_UNKNOWN

        onu_rx_power = normalize_power_value(power.get('onu_rx_power'))
        olt_rx_power = (
            normalize_power_value(power.get('olt_rx_power'))
            if supports_olt_rx_power else None
        )
        power_read_at = None
        if onu_rx_power is not None or olt_rx_power is not None:
            power_read_at = self._as_iso(power.get('power_read_at'))

        return {
            'id': obj.id,
            'onu_number': obj.onu_id,
            'name': obj.name,
            'client_name': obj.name,
            'serial_number': obj.serial,
            'status': obj.status,
            'disconnect_reason': disconnect_reason,
            'offline_since': offline_since,
            'disconnect_window_start': disconnect_window_start,
            'disconnect_window_end': disconnect_window_end,
            'onu_rx_power': onu_rx_power,
            'olt_rx_power': olt_rx_power,
            'power_read_at': power_read_at,
            'last_discovered_at': self._as_iso(obj.last_discovered_at),
        }


class PONNestedSerializer(serializers.ModelSerializer):
    """
    Nested serializer for PON within topology tree
    """

    pon_number = serializers.IntegerField(source='pon_id', read_only=True)
    onus = ONUNestedSerializer(many=True, read_only=True)
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

    class Meta:
        model = OLTPON
        fields = [
            'id',
            'pon_number',
            'pon_key',
            'name',
            'description',
            'onus',
            'onu_count',
            'online_count',
            'offline_count',
            'is_active',
        ]
        read_only_fields = fields

    def get_onu_count(self, obj):
        return _live_count(
            lambda: obj.onus.filter(is_active=True).count(),
        )

    def get_online_count(self, obj):
        return _live_count(
            lambda: obj.onus.filter(is_active=True, status=ONU.STATUS_ONLINE).count(),
        )

    def get_offline_count(self, obj):
        return _live_count(
            lambda: obj.onus.filter(is_active=True).exclude(status=ONU.STATUS_ONLINE).count(),
        )


class SlotNestedSerializer(serializers.ModelSerializer):
    """
    Nested serializer for Slot within topology tree
    """

    slot_number = serializers.IntegerField(source='slot_id', read_only=True)
    pons = PONNestedSerializer(many=True, read_only=True)
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

    class Meta:
        model = OLTSlot
        fields = [
            'id',
            'slot_number',
            'slot_key',
            'name',
            'pons',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
            'is_active',
        ]
        read_only_fields = fields

    def get_pon_count(self, obj):
        return _live_count(
            lambda: obj.pons.filter(is_active=True).count(),
        )

    def get_onu_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(pon_ref__slot=obj, is_active=True).count(),
        )

    def get_online_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(
                pon_ref__slot=obj,
                is_active=True,
                status=ONU.STATUS_ONLINE,
            ).count(),
        )

    def get_offline_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(pon_ref__slot=obj, is_active=True).exclude(
                status=ONU.STATUS_ONLINE
            ).count(),
        )


class OLTTopologySerializer(serializers.ModelSerializer):
    """
    Serializer for OLT with full topology tree (slots → PONs → ONUs)
    """

    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    vendor_display = serializers.SerializerMethodField()
    collector_reachable = serializers.BooleanField(read_only=True)
    last_collector_check_at = serializers.DateTimeField(read_only=True)
    last_collector_error = serializers.CharField(read_only=True)
    collector_failure_count = serializers.IntegerField(read_only=True)
    snmp_reachable = serializers.BooleanField(source='collector_reachable', read_only=True)
    last_snmp_check_at = serializers.DateTimeField(source='last_collector_check_at', read_only=True)
    last_snmp_error = serializers.CharField(source='last_collector_error', read_only=True)
    snmp_failure_count = serializers.IntegerField(source='collector_failure_count', read_only=True)
    slots = SlotNestedSerializer(many=True, read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()
    supports_olt_rx_power = serializers.SerializerMethodField()

    class Meta:
        model = OLT
        fields = [
            'id',
            'name',
            'ip_address',
            'vendor_profile',
            'vendor_display',
            'vendor_profile_name',
            'snmp_port',
            'snmp_community',
            'snmp_version',
            'collector_reachable',
            'last_collector_check_at',
            'last_collector_error',
            'collector_failure_count',
            'snmp_reachable',
            'last_snmp_check_at',
            'last_snmp_error',
            'snmp_failure_count',
            'discovery_enabled',
            'discovery_interval_minutes',
            'polling_enabled',
            'polling_interval_seconds',
            'power_interval_seconds',
            'last_discovery_at',
            'last_poll_at',
            'last_power_at',
            'protocol',
            'telnet_port',
            'telnet_username',
            'blade_ips',
            'slots',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
            'supports_olt_rx_power',
            'is_active',
        ]
        read_only_fields = [
            'id',
            'last_discovery_at',
            'last_poll_at',
            'last_power_at',
            'collector_reachable',
            'last_collector_check_at',
            'last_collector_error',
            'collector_failure_count',
            'snmp_reachable',
            'last_snmp_check_at',
            'last_snmp_error',
            'snmp_failure_count',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
        ]

    def get_vendor_display(self, obj):
        return (obj.vendor_profile.vendor or '').upper()

    def get_slot_count(self, obj):
        return _live_count(
            lambda: obj.slots.filter(is_active=True).count(),
        )

    def get_pon_count(self, obj):
        return _live_count(
            lambda: OLTPON.objects.filter(olt=obj, is_active=True).count(),
        )

    def get_onu_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(olt=obj, is_active=True).count(),
        )

    def get_online_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(
                olt=obj,
                is_active=True,
                status=ONU.STATUS_ONLINE,
            ).count(),
        )

    def get_offline_count(self, obj):
        return _live_count(
            lambda: ONU.objects.filter(olt=obj, is_active=True).exclude(
                status=ONU.STATUS_ONLINE
            ).count(),
        )

    def get_supports_olt_rx_power(self, obj):
        return supports_olt_rx_power(obj)


# ============================================
# Standard Serializers
# ============================================

class OLTSerializer(serializers.ModelSerializer):
    """
    Serializer for OLT (standard, without nested topology)
    """

    MAX_DISCOVERY_INTERVAL_MINUTES = 7 * 24 * 60
    MAX_POLLING_INTERVAL_SECONDS = 7 * 24 * 60 * 60
    MAX_POWER_INTERVAL_SECONDS = 7 * 24 * 60 * 60

    name = serializers.CharField(max_length=100, trim_whitespace=True)
    protocol = serializers.CharField(required=False, allow_blank=True)
    snmp_community = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    snmp_port = serializers.IntegerField(required=False)
    snmp_version = serializers.CharField(required=False, allow_blank=True)
    telnet_port = serializers.IntegerField(required=False)
    telnet_username = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    telnet_password = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    telnet_password_configured = serializers.SerializerMethodField()
    blade_ips = serializers.JSONField(required=False, allow_null=True, default=None)
    unm_password = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    unm_password_configured = serializers.SerializerMethodField()
    vendor_profile = serializers.PrimaryKeyRelatedField(
        queryset=VendorProfile.objects.filter(is_active=True)
    )
    vendor_display = serializers.SerializerMethodField()
    model_display = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    collector_reachable = serializers.BooleanField(read_only=True)
    last_collector_check_at = serializers.DateTimeField(read_only=True)
    last_collector_error = serializers.CharField(read_only=True)
    collector_failure_count = serializers.IntegerField(read_only=True)
    snmp_reachable = serializers.BooleanField(source='collector_reachable', read_only=True)
    last_snmp_check_at = serializers.DateTimeField(source='last_collector_check_at', read_only=True)
    last_snmp_error = serializers.CharField(source='last_collector_error', read_only=True)
    snmp_failure_count = serializers.IntegerField(source='collector_failure_count', read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()
    supports_olt_rx_power = serializers.SerializerMethodField()

    class Meta:
        model = OLT
        fields = [
            'id',
            'name',
            'vendor_profile',
            'vendor_display',
            'model_display',
            'vendor_profile_name',
            'protocol',
            'ip_address',
            'snmp_port',
            'snmp_community',
            'snmp_version',
            'telnet_port',
            'telnet_username',
            'telnet_password',
            'telnet_password_configured',
            'blade_ips',
            'unm_enabled',
            'unm_host',
            'unm_port',
            'unm_username',
            'unm_password',
            'unm_password_configured',
            'unm_mneid',
            'collector_reachable',
            'last_collector_check_at',
            'last_collector_error',
            'collector_failure_count',
            'snmp_reachable',
            'last_snmp_check_at',
            'last_snmp_error',
            'snmp_failure_count',
            'discovery_enabled',
            'discovery_interval_minutes',
            'last_discovery_at',
            'next_discovery_at',
            'discovery_healthy',
            'polling_enabled',
            'polling_interval_seconds',
            'power_interval_seconds',
            'history_days',
            'last_poll_at',
            'next_poll_at',
            'last_power_at',
            'next_power_at',
            'is_active',
            'created_at',
            'updated_at',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
            'supports_olt_rx_power',
        ]
        read_only_fields = [
            'id',
            'last_discovery_at',
            'next_discovery_at',
            'discovery_healthy',
            'last_poll_at',
            'next_poll_at',
            'last_power_at',
            'next_power_at',
            'collector_reachable',
            'last_collector_check_at',
            'last_collector_error',
            'collector_failure_count',
            'snmp_reachable',
            'last_snmp_check_at',
            'last_snmp_error',
            'snmp_failure_count',
            'created_at',
            'updated_at',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
        ]

    def get_vendor_display(self, obj):
        return (obj.vendor_profile.vendor or '').upper()

    def get_slot_count(self, obj):
        return _cached_count(
            obj,
            'cached_slot_count',
            lambda: OLTSlot.objects.filter(olt=obj, is_active=True).count(),
        )

    def get_pon_count(self, obj):
        return _cached_count(
            obj,
            'cached_pon_count',
            lambda: OLTPON.objects.filter(olt=obj, is_active=True).count(),
        )

    def get_onu_count(self, obj):
        return _cached_count(
            obj,
            'cached_onu_count',
            lambda: ONU.objects.filter(olt=obj, is_active=True).count(),
        )

    def get_online_count(self, obj):
        return _cached_count(
            obj,
            'cached_online_count',
            lambda: ONU.objects.filter(
                olt=obj,
                is_active=True,
                status=ONU.STATUS_ONLINE,
            ).count(),
        )

    def get_offline_count(self, obj):
        return _cached_count(
            obj,
            'cached_offline_count',
            lambda: ONU.objects.filter(olt=obj, is_active=True).exclude(
                status=ONU.STATUS_ONLINE
            ).count(),
        )

    def get_supports_olt_rx_power(self, obj):
        return supports_olt_rx_power(obj)

    def get_telnet_password_configured(self, obj):
        return bool(str(obj.telnet_password or '').strip())

    def get_unm_password_configured(self, obj):
        return bool(str(obj.unm_password or '').strip())

    @staticmethod
    def _preserve_password(attrs, instance, field_name):
        incoming = attrs.get(field_name, serializers.empty)
        if incoming is serializers.empty:
            return str(getattr(instance, field_name, '') or '')
        if instance is not None and not str(incoming or '').strip():
            return str(getattr(instance, field_name, '') or '')
        return str(incoming or '')

    def validate_name(self, value):
        name = str(value or '').strip()
        if not name:
            raise serializers.ValidationError('Name cannot be empty.')

        queryset = OLT.objects.filter(name=name)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.filter(is_active=True).exists():
            raise serializers.ValidationError('An active OLT with this name already exists.')
        return name

    def validate_protocol(self, value):
        protocol = str(value or '').strip().lower()
        valid_protocols = {choice[0] for choice in OLT.PROTOCOL_CHOICES}
        if protocol not in valid_protocols:
            raise serializers.ValidationError('Unsupported protocol.')
        return protocol

    def validate_snmp_community(self, value):
        return str(value or '').strip()

    def validate_snmp_port(self, value):
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError('SNMP port must be an integer.')
        if port < 1 or port > 65535:
            raise serializers.ValidationError('SNMP port must be between 1 and 65535.')
        return port

    def validate_snmp_version(self, value):
        version = str(value or '').strip().lower()
        if version != 'v2c':
            raise serializers.ValidationError('Only SNMP v2c is currently supported.')
        return version

    def validate_telnet_port(self, value):
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError('Telnet port must be an integer.')
        if port < 1 or port > 65535:
            raise serializers.ValidationError('Telnet port must be between 1 and 65535.')
        return port

    def validate_telnet_username(self, value):
        return str(value or '').strip()

    def validate_unm_port(self, value):
        if value in (None, ''):
            return 3306
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError('UNM port must be an integer.')
        if port < 1 or port > 65535:
            raise serializers.ValidationError('UNM port must be between 1 and 65535.')
        return port

    def validate_unm_mneid(self, value):
        if value in (None, ''):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError('UNM MNEID must be a positive integer.')
        if parsed <= 0:
            raise serializers.ValidationError('UNM MNEID must be a positive integer.')
        return parsed

    def validate_history_days(self, value):
        if not (7 <= value <= 30):
            raise serializers.ValidationError('history_days must be between 7 and 30.')
        return value

    def validate_blade_ips(self, value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise serializers.ValidationError('blade_ips must be a list of IP addresses.')
        import ipaddress
        cleaned = []
        for entry in value:
            ip_str = str(entry or '').strip()
            if not ip_str:
                continue
            try:
                ipaddress.ip_address(ip_str)
            except ValueError:
                raise serializers.ValidationError(f'Invalid IP address: {ip_str}')
            cleaned.append(ip_str)
        return cleaned if cleaned else None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not self.instance:
            attrs = self._apply_vendor_defaults(attrs)

        instance = self.instance
        vendor_profile = attrs.get('vendor_profile', getattr(instance, 'vendor_profile', None))
        expected_protocol = get_default_protocol(vendor_profile) if vendor_profile else OLT.PROTOCOL_SNMP
        protocol = str(
            attrs.get('protocol', getattr(instance, 'protocol', expected_protocol) or expected_protocol)
        ).strip().lower()
        if protocol != expected_protocol:
            raise serializers.ValidationError(
                {'protocol': f'This vendor profile requires {expected_protocol.upper()} protocol.'}
            )
        attrs['protocol'] = protocol

        effective_telnet_password = self._preserve_password(attrs, instance, 'telnet_password')
        effective_unm_password = self._preserve_password(attrs, instance, 'unm_password')
        effective_telnet_username = attrs.get('telnet_username', getattr(instance, 'telnet_username', ''))
        effective_unm_host = attrs.get('unm_host', getattr(instance, 'unm_host', None))
        effective_unm_username = attrs.get('unm_username', getattr(instance, 'unm_username', ''))
        effective_unm_mneid = attrs.get('unm_mneid', getattr(instance, 'unm_mneid', None))

        validation_errors = {}
        if protocol == OLT.PROTOCOL_SNMP:
            effective_snmp_community = str(
                attrs.get('snmp_community', getattr(instance, 'snmp_community', '')) or ''
            ).strip()
            if not effective_snmp_community:
                validation_errors['snmp_community'] = 'SNMP community cannot be empty.'
            attrs['snmp_community'] = effective_snmp_community
            attrs['snmp_port'] = self.validate_snmp_port(
                attrs.get('snmp_port', getattr(instance, 'snmp_port', 161))
            )
            attrs['snmp_version'] = self.validate_snmp_version(
                attrs.get('snmp_version', getattr(instance, 'snmp_version', 'v2c'))
            )
            attrs.setdefault('telnet_port', getattr(instance, 'telnet_port', 23) or 23)
            attrs.setdefault('telnet_username', getattr(instance, 'telnet_username', '') or '')
        elif protocol == OLT.PROTOCOL_TELNET:
            attrs['telnet_port'] = self.validate_telnet_port(
                attrs.get('telnet_port', getattr(instance, 'telnet_port', 23))
            )
            attrs['telnet_username'] = self.validate_telnet_username(effective_telnet_username)
            if not attrs['telnet_username']:
                validation_errors['telnet_username'] = 'Telnet username cannot be empty.'
            if not str(effective_telnet_password or '').strip():
                validation_errors['telnet_password'] = 'Telnet password is required.'
            attrs.setdefault('snmp_port', getattr(instance, 'snmp_port', 161) or 161)
            attrs.setdefault('snmp_version', getattr(instance, 'snmp_version', 'v2c') or 'v2c')
            attrs['snmp_community'] = str(
                attrs.get('snmp_community', getattr(instance, 'snmp_community', '')) or ''
            )

        unm_enabled = bool(attrs.get('unm_enabled', getattr(instance, 'unm_enabled', False)))
        if unm_enabled:
            if not str(effective_unm_host or '').strip():
                validation_errors['unm_host'] = 'UNM host is required when UNM is enabled.'
            if not str(effective_unm_username or '').strip():
                validation_errors['unm_username'] = 'UNM username is required when UNM is enabled.'
            if not str(effective_unm_password or '').strip():
                validation_errors['unm_password'] = 'UNM password is required when UNM is enabled.'
            if effective_unm_mneid in (None, ''):
                validation_errors['unm_mneid'] = 'UNM MNEID is required when UNM is enabled.'

        if validation_errors:
            raise serializers.ValidationError(validation_errors)

        attrs['telnet_password'] = effective_telnet_password
        attrs['unm_password'] = effective_unm_password

        discovery_interval = attrs.get(
            'discovery_interval_minutes',
            getattr(self.instance, 'discovery_interval_minutes', None),
        )
        polling_interval = attrs.get(
            'polling_interval_seconds',
            getattr(self.instance, 'polling_interval_seconds', None),
        )
        power_interval = attrs.get(
            'power_interval_seconds',
            getattr(self.instance, 'power_interval_seconds', None),
        )

        self._validate_interval_ranges(
            discovery_interval=discovery_interval,
            polling_interval=polling_interval,
            power_interval=power_interval,
        )
        return attrs

    def _validate_interval_ranges(self, discovery_interval, polling_interval, power_interval):
        if discovery_interval is None or int(discovery_interval) <= 0:
            raise serializers.ValidationError(
                {'discovery_interval_minutes': 'Discovery interval must be greater than 0 minutes.'}
            )
        if polling_interval is None or int(polling_interval) <= 0:
            raise serializers.ValidationError(
                {'polling_interval_seconds': 'Polling interval must be greater than 0 seconds.'}
            )
        if power_interval is None or int(power_interval) <= 0:
            raise serializers.ValidationError(
                {'power_interval_seconds': 'Power interval must be greater than 0 seconds.'}
            )

        if int(discovery_interval) > self.MAX_DISCOVERY_INTERVAL_MINUTES:
            raise serializers.ValidationError(
                {
                    'discovery_interval_minutes': (
                        f'Discovery interval must be <= {self.MAX_DISCOVERY_INTERVAL_MINUTES} minutes.'
                    )
                }
            )
        if int(polling_interval) > self.MAX_POLLING_INTERVAL_SECONDS:
            raise serializers.ValidationError(
                {
                    'polling_interval_seconds': (
                        f'Polling interval must be <= {self.MAX_POLLING_INTERVAL_SECONDS} seconds.'
                    )
                }
            )
        if int(power_interval) > self.MAX_POWER_INTERVAL_SECONDS:
            raise serializers.ValidationError(
                {
                    'power_interval_seconds': (
                        f'Power interval must be <= {self.MAX_POWER_INTERVAL_SECONDS} seconds.'
                    )
                }
            )

    def _apply_vendor_defaults(self, validated_data):
        vendor_profile = validated_data.get('vendor_profile')
        defaults_cfg = {}
        if vendor_profile and isinstance(vendor_profile.default_thresholds, dict):
            defaults_cfg = vendor_profile.default_thresholds

        validated_data.setdefault('protocol', get_default_protocol(vendor_profile))
        if 'discovery_interval_minutes' not in validated_data:
            validated_data['discovery_interval_minutes'] = int(
                defaults_cfg.get('discovery_interval_minutes', 240)
            )
        if 'polling_interval_seconds' not in validated_data:
            validated_data['polling_interval_seconds'] = int(
                defaults_cfg.get('polling_interval_seconds', 300)
            )
        if 'power_interval_seconds' not in validated_data:
            validated_data['power_interval_seconds'] = int(
                defaults_cfg.get('power_interval_seconds', 300)
            )
        validated_data.setdefault('snmp_port', 161)
        validated_data.setdefault('snmp_version', 'v2c')
        validated_data.setdefault('snmp_community', 'public')
        validated_data.setdefault('telnet_port', 23)
        validated_data.setdefault('telnet_username', '')
        return validated_data

    def _reset_runtime_state(self, olt):
        olt.collector_reachable = None
        olt.last_collector_check_at = None
        olt.last_collector_error = ''
        olt.collector_failure_count = 0
        olt.next_discovery_at = None
        olt.discovery_healthy = True
        olt.next_poll_at = None
        olt.last_power_at = None
        olt.next_power_at = None
        olt.cached_slot_count = None
        olt.cached_pon_count = None
        olt.cached_onu_count = None
        olt.cached_online_count = None
        olt.cached_offline_count = None
        olt.cached_counts_at = None
        return {
            'collector_reachable',
            'last_collector_check_at',
            'last_collector_error',
            'collector_failure_count',
            'next_discovery_at',
            'discovery_healthy',
            'next_poll_at',
            'last_power_at',
            'next_power_at',
            'cached_slot_count',
            'cached_pon_count',
            'cached_onu_count',
            'cached_online_count',
            'cached_offline_count',
            'cached_counts_at',
        }

    def create(self, validated_data):
        validated_data = self._apply_vendor_defaults(validated_data)
        existing = OLT.objects.filter(name=validated_data.get('name'), is_active=False).first()
        if existing:
            for field, value in validated_data.items():
                setattr(existing, field, value)
            existing.is_active = True
            reset_fields = self._reset_runtime_state(existing)
            update_fields = set(validated_data.keys()) | {'is_active'} | reset_fields
            existing.save(update_fields=sorted(update_fields))
            return existing
        return super().create(validated_data)

    def update(self, instance, validated_data):
        tracked_connectivity_fields = {
            'vendor_profile',
            'protocol',
            'ip_address',
            'snmp_port',
            'snmp_community',
            'snmp_version',
            'telnet_port',
            'telnet_username',
            'telnet_password',
            'blade_ips',
        }
        tracked_interval_fields = {
            'discovery_interval_minutes',
            'polling_interval_seconds',
            'power_interval_seconds',
        }
        connectivity_changed = any(
            field in validated_data and validated_data[field] != getattr(instance, field)
            for field in tracked_connectivity_fields
        )
        interval_changed = any(
            field in validated_data and validated_data[field] != getattr(instance, field)
            for field in tracked_interval_fields
        )

        olt = super().update(instance, validated_data)
        extra_update_fields = set()

        if connectivity_changed:
            extra_update_fields |= self._reset_runtime_state(olt)
        elif interval_changed:
            if 'discovery_interval_minutes' in validated_data and olt.last_discovery_at:
                olt.next_discovery_at = olt.last_discovery_at + timedelta(
                    minutes=olt.discovery_interval_minutes
                )
                extra_update_fields.add('next_discovery_at')
            if 'polling_interval_seconds' in validated_data and olt.last_poll_at:
                olt.next_poll_at = olt.last_poll_at + timedelta(
                    seconds=olt.polling_interval_seconds
                )
                extra_update_fields.add('next_poll_at')
            if 'power_interval_seconds' in validated_data and olt.last_power_at:
                olt.next_power_at = olt.last_power_at + timedelta(
                    seconds=olt.power_interval_seconds
                )
                extra_update_fields.add('next_power_at')

        if extra_update_fields:
            olt.updated_at = timezone.now()
            extra_update_fields.add('updated_at')
            olt.save(update_fields=sorted(extra_update_fields))
        return olt


class OLTSlotSerializer(serializers.ModelSerializer):
    """
    Serializer for OLTSlot
    """

    olt_name = serializers.CharField(source='olt.name', read_only=True)

    class Meta:
        model = OLTSlot
        fields = [
            'id',
            'olt',
            'olt_name',
            'slot_id',
            'slot_key',
            'name',
            'rack_id',
            'shelf_id',
            'is_active',
            'last_discovered_at',
            'created_at',
        ]
        read_only_fields = ['id', 'last_discovered_at', 'created_at']


class OLTPONSerializer(serializers.ModelSerializer):
    """
    Serializer for OLTPON
    """

    olt_name = serializers.CharField(source='olt.name', read_only=True)
    slot_id = serializers.IntegerField(source='slot.slot_id', read_only=True)
    slot_key = serializers.CharField(source='slot.slot_key', read_only=True)

    class Meta:
        model = OLTPON
        fields = [
            'id',
            'olt',
            'olt_name',
            'slot',
            'slot_id',
            'slot_key',
            'pon_id',
            'pon_key',
            'pon_index',
            'name',
            'description',
            'rack_id',
            'shelf_id',
            'port_id',
            'is_active',
            'last_discovered_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'olt', 'olt_name', 'slot', 'slot_id', 'slot_key',
            'pon_id', 'pon_key', 'pon_index', 'name', 'rack_id', 'shelf_id',
            'port_id', 'is_active', 'last_discovered_at', 'created_at',
        ]


class ONUSerializer(serializers.ModelSerializer):
    """
    Serializer for ONU
    """

    olt_name = serializers.CharField(source='olt.name', read_only=True)
    client_name = serializers.CharField(source='name', read_only=True)
    serial_number = serializers.CharField(source='serial', read_only=True)
    slot = serializers.IntegerField(source='slot_ref_id', read_only=True)
    pon = serializers.IntegerField(source='pon_ref_id', read_only=True)
    slot_key = serializers.CharField(source='slot_ref.slot_key', read_only=True)
    pon_key = serializers.CharField(source='pon_ref.pon_key', read_only=True)
    slot_name = serializers.CharField(source='slot_ref.name', read_only=True)
    pon_name = serializers.CharField(source='pon_ref.name', read_only=True)

    class Meta:
        model = ONU
        fields = [
            'id',
            'olt',
            'olt_name',
            'slot_id',
            'slot',
            'slot_key',
            'slot_name',
            'pon_id',
            'pon',
            'pon_key',
            'pon_name',
            'onu_id',
            'snmp_index',
            'name',
            'client_name',
            'serial_number',
            'status',
            'is_active',
            'last_discovered_at',
        ]
        read_only_fields = ['id', 'last_discovered_at']


class ONULogSerializer(serializers.ModelSerializer):
    """
    Serializer for ONULog
    """

    class Meta:
        model = ONULog
        fields = [
            'id',
            'onu',
            'offline_since',
            'offline_until',
            'disconnect_window_start',
            'disconnect_window_end',
            'disconnect_reason',
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for UserProfile
    """

    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = UserProfile
        fields = ['id', 'user', 'username', 'email', 'role', 'last_login_ip', 'created_at']
        read_only_fields = ['id', 'created_at']
