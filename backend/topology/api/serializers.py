from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from topology.models import OLTPON, OLT, OLTSlot, ONU, ONULog, UserProfile, VendorProfile
from topology.services.cache_service import cache_service


class VendorProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for VendorProfile
    """

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
        ]
        read_only_fields = ['id']


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
    disconnect_reason = serializers.SerializerMethodField()
    offline_since = serializers.SerializerMethodField()
    onu_rx_power = serializers.SerializerMethodField()
    olt_rx_power = serializers.SerializerMethodField()
    power_read_at = serializers.SerializerMethodField()

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

        cache_key = '_active_log_cache'
        if not hasattr(self, cache_key):
            setattr(self, cache_key, {})
        cache = getattr(self, cache_key)
        if obj.id in cache:
            return cache[obj.id]

        log = ONULog.objects.filter(
            onu=obj,
            offline_until__isnull=True,
        ).order_by('-offline_since').first()
        cache[obj.id] = log
        return log

    def get_disconnect_reason(self, obj):
        log = self._get_active_log(obj)
        if log:
            return log.disconnect_reason
        return None

    def get_offline_since(self, obj):
        log = self._get_active_log(obj)
        if log and log.offline_since:
            return log.offline_since.isoformat()
        return None

    def _get_power(self, obj):
        power_map = self.context.get('power_map') if isinstance(self.context, dict) else None
        if isinstance(power_map, dict) and obj.id in power_map:
            return power_map[obj.id] or {}

        cache_key = '_power_cache'
        if not hasattr(self, cache_key):
            setattr(self, cache_key, {})
        cache = getattr(self, cache_key)
        if obj.id in cache:
            return cache[obj.id]
        power = cache_service.get_onu_power(obj.olt_id, obj.id) or {}
        cache[obj.id] = power
        return power

    def get_onu_rx_power(self, obj):
        return self._get_power(obj).get('onu_rx_power')

    def get_olt_rx_power(self, obj):
        return self._get_power(obj).get('olt_rx_power')

    def get_power_read_at(self, obj):
        return self._get_power(obj).get('power_read_at')


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
            'onus',
            'onu_count',
            'online_count',
            'offline_count',
            'is_active',
        ]
        read_only_fields = fields

    def get_onu_count(self, obj):
        return int(getattr(obj, 'onu_count', obj.onus.filter(is_active=True).count()))

    def get_online_count(self, obj):
        return int(getattr(obj, 'online_count', obj.onus.filter(is_active=True, status=ONU.STATUS_ONLINE).count()))

    def get_offline_count(self, obj):
        return int(getattr(obj, 'offline_count', obj.onus.filter(is_active=True).exclude(status=ONU.STATUS_ONLINE).count()))


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
        return int(getattr(obj, 'pon_count', obj.pons.filter(is_active=True).count()))

    def get_onu_count(self, obj):
        if hasattr(obj, 'onu_count'):
            return int(obj.onu_count)
        return ONU.objects.filter(pon_ref__slot=obj, is_active=True).count()

    def get_online_count(self, obj):
        if hasattr(obj, 'online_count'):
            return int(obj.online_count)
        return ONU.objects.filter(pon_ref__slot=obj, is_active=True, status=ONU.STATUS_ONLINE).count()

    def get_offline_count(self, obj):
        if hasattr(obj, 'offline_count'):
            return int(obj.offline_count)
        return ONU.objects.filter(pon_ref__slot=obj, is_active=True).exclude(status=ONU.STATUS_ONLINE).count()


class OLTTopologySerializer(serializers.ModelSerializer):
    """
    Serializer for OLT with full topology tree (slots → PONs → ONUs)
    """

    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    vendor_display = serializers.SerializerMethodField()
    slots = SlotNestedSerializer(many=True, read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

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
            'snmp_reachable',
            'last_snmp_check_at',
            'discovery_enabled',
            'discovery_interval_minutes',
            'polling_enabled',
            'polling_interval_seconds',
            'power_interval_seconds',
            'last_discovery_at',
            'last_poll_at',
            'slots',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
            'is_active',
        ]
        read_only_fields = [
            'id',
            'last_discovery_at',
            'last_poll_at',
            'snmp_reachable',
            'last_snmp_check_at',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
        ]

    def get_vendor_display(self, obj):
        return (obj.vendor_profile.vendor or '').upper()

    def get_slot_count(self, obj):
        return int(getattr(obj, 'slot_count', obj.slots.filter(is_active=True).count()))

    def get_pon_count(self, obj):
        return int(getattr(obj, 'pon_count', OLTPON.objects.filter(olt=obj, is_active=True).count()))

    def get_onu_count(self, obj):
        return int(getattr(obj, 'onu_count', ONU.objects.filter(olt=obj, is_active=True).count()))

    def get_online_count(self, obj):
        return int(getattr(obj, 'online_count', ONU.objects.filter(olt=obj, is_active=True, status=ONU.STATUS_ONLINE).count()))

    def get_offline_count(self, obj):
        if hasattr(obj, 'offline_count'):
            return int(obj.offline_count)
        return ONU.objects.filter(olt=obj, is_active=True).exclude(status=ONU.STATUS_ONLINE).count()


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
    vendor_profile = serializers.PrimaryKeyRelatedField(
        queryset=VendorProfile.objects.filter(is_active=True)
    )
    vendor_display = serializers.SerializerMethodField()
    model_display = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

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
            'last_poll_at',
            'next_poll_at',
            'is_active',
            'created_at',
            'updated_at',
            'slot_count',
            'pon_count',
            'onu_count',
            'online_count',
            'offline_count',
        ]
        read_only_fields = [
            'id',
            'last_discovery_at',
            'next_discovery_at',
            'discovery_healthy',
            'last_poll_at',
            'next_poll_at',
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
        return int(getattr(obj, 'slot_count', OLTSlot.objects.filter(olt=obj, is_active=True).count()))

    def get_pon_count(self, obj):
        return int(getattr(obj, 'pon_count', OLTPON.objects.filter(olt=obj, is_active=True).count()))

    def get_onu_count(self, obj):
        return int(getattr(obj, 'onu_count', ONU.objects.filter(olt=obj, is_active=True).count()))

    def get_online_count(self, obj):
        return int(getattr(obj, 'online_count', ONU.objects.filter(olt=obj, is_active=True, status=ONU.STATUS_ONLINE).count()))

    def get_offline_count(self, obj):
        if hasattr(obj, 'offline_count'):
            return int(obj.offline_count)
        return ONU.objects.filter(olt=obj, is_active=True).exclude(status=ONU.STATUS_ONLINE).count()

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
        if value != OLT.PROTOCOL_SNMP:
            raise serializers.ValidationError('Only SNMP protocol is supported.')
        return value

    def validate_snmp_community(self, value):
        community = str(value or '').strip()
        if not community:
            raise serializers.ValidationError('SNMP community cannot be empty.')
        return community

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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not self.instance:
            attrs = self._apply_vendor_defaults(attrs)

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
        return validated_data

    def _reset_runtime_state(self, olt):
        olt.snmp_reachable = None
        olt.last_snmp_check_at = None
        olt.last_snmp_error = ''
        olt.snmp_failure_count = 0
        olt.next_discovery_at = None
        olt.discovery_healthy = True
        olt.next_poll_at = None
        return {
            'snmp_reachable',
            'last_snmp_check_at',
            'last_snmp_error',
            'snmp_failure_count',
            'next_discovery_at',
            'discovery_healthy',
            'next_poll_at',
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
        }
        tracked_interval_fields = {
            'discovery_interval_minutes',
            'polling_interval_seconds',
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
            'rack_id',
            'shelf_id',
            'port_id',
            'is_active',
            'last_discovered_at',
            'created_at',
        ]
        read_only_fields = ['id', 'last_discovered_at', 'created_at']


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
        fields = ['id', 'onu', 'offline_since', 'offline_until', 'disconnect_reason']


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
