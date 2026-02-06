from rest_framework import serializers
from django.db.models import Count
from dashboard.models import VendorProfile, OLT, OLTSlot, OLTPON, ONU, ONULog, UserProfile


class VendorProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for VendorProfile
    """
    class Meta:
        model = VendorProfile
        fields = ['id', 'vendor', 'model_name', 'description', 'supports_onu_discovery',
                  'supports_onu_status', 'supports_power_monitoring', 'supports_disconnect_reason']
        read_only_fields = ['id']


# ============================================
# Nested Topology Serializers
# ============================================

class ONUNestedSerializer(serializers.ModelSerializer):
    """
    Nested serializer for ONU within topology tree
    """
    onu_number = serializers.IntegerField(source='onu_id', read_only=True)
    serial_number = serializers.CharField(source='serial', read_only=True)
    disconnect_reason = serializers.SerializerMethodField()
    
    class Meta:
        model = ONU
        fields = ['id', 'onu_number', 'name', 'serial_number', 'status', 'disconnect_reason',
                  'last_discovered_at']
        read_only_fields = fields
    
    def get_disconnect_reason(self, obj):
        # Get the most recent active offline log
        log = ONULog.objects.filter(onu=obj, offline_until__isnull=True).first()
        if log:
            return log.disconnect_reason
        return None


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
        fields = ['id', 'pon_number', 'pon_key', 'name', 'onus', 'onu_count', 
                  'online_count', 'offline_count', 'is_active']
        read_only_fields = fields
    
    def get_onu_count(self, obj):
        return obj.onus.count()
    
    def get_online_count(self, obj):
        return obj.onus.filter(status='online').count()
    
    def get_offline_count(self, obj):
        return obj.onus.exclude(status='online').count()


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
        fields = ['id', 'slot_number', 'slot_key', 'name', 'pons', 'pon_count',
                  'onu_count', 'online_count', 'offline_count', 'is_active']
        read_only_fields = fields
    
    def get_pon_count(self, obj):
        return obj.pons.filter(is_active=True).count()
    
    def get_onu_count(self, obj):
        count = 0
        for pon in obj.pons.all():
            count += pon.onus.count()
        return count
    
    def get_online_count(self, obj):
        count = 0
        for pon in obj.pons.all():
            count += pon.onus.filter(status='online').count()
        return count
    
    def get_offline_count(self, obj):
        count = 0
        for pon in obj.pons.all():
            count += pon.onus.exclude(status='online').count()
        return count


class OLTTopologySerializer(serializers.ModelSerializer):
    """
    Serializer for OLT with full topology tree (slots → PONs → ONUs)
    """
    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    slots = SlotNestedSerializer(many=True, read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

    class Meta:
        model = OLT
        fields = ['id', 'name', 'ip_address', 'vendor_profile', 'vendor_profile_name',
                  'snmp_port', 'snmp_community', 'snmp_version',
                  'discovery_enabled', 'polling_enabled',
                  'last_discovery_at', 'last_poll_at',
                  'slots', 'slot_count', 'pon_count', 'onu_count', 
                  'online_count', 'offline_count', 'is_active']
        read_only_fields = ['id', 'last_discovery_at', 'last_poll_at',
                          'slot_count', 'pon_count', 'onu_count', 
                          'online_count', 'offline_count']

    def get_slot_count(self, obj):
        return obj.slots.filter(is_active=True).count()

    def get_pon_count(self, obj):
        return OLTPON.objects.filter(olt=obj, is_active=True).count()

    def get_onu_count(self, obj):
        return ONU.objects.filter(olt=obj).count()

    def get_online_count(self, obj):
        return ONU.objects.filter(olt=obj, status='online').count()

    def get_offline_count(self, obj):
        return ONU.objects.filter(olt=obj).exclude(status='online').count()


# ============================================
# Standard Serializers
# ============================================

class OLTSerializer(serializers.ModelSerializer):
    """
    Serializer for OLT (standard, without nested topology)
    """
    vendor_display = serializers.CharField(source='vendor_profile.get_vendor_display', read_only=True)
    model_display = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    vendor_profile_name = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

    class Meta:
        model = OLT
        fields = ['id', 'name', 'vendor_profile', 'vendor_display', 'model_display', 'vendor_profile_name',
                  'protocol', 'ip_address', 'snmp_port', 'snmp_community', 'snmp_version',
                  'discovery_enabled', 'discovery_interval_minutes', 'last_discovery_at', 
                  'next_discovery_at', 'discovery_healthy',
                  'polling_enabled', 'polling_interval_seconds', 'last_poll_at', 'next_poll_at',
                  'is_active', 'created_at', 'updated_at',
                  'slot_count', 'pon_count', 'onu_count', 'online_count', 'offline_count']
        read_only_fields = ['id', 'last_discovery_at', 'next_discovery_at', 'discovery_healthy',
                          'last_poll_at', 'next_poll_at', 'created_at', 'updated_at',
                          'slot_count', 'pon_count', 'onu_count', 'online_count', 'offline_count']

    def get_slot_count(self, obj):
        return OLTSlot.objects.filter(olt=obj, is_active=True).count()

    def get_pon_count(self, obj):
        return OLTPON.objects.filter(olt=obj, is_active=True).count()

    def get_onu_count(self, obj):
        return ONU.objects.filter(olt=obj).count()

    def get_online_count(self, obj):
        return ONU.objects.filter(olt=obj, status='online').count()

    def get_offline_count(self, obj):
        return ONU.objects.filter(olt=obj).exclude(status='online').count()


class OLTSlotSerializer(serializers.ModelSerializer):
    """
    Serializer for OLTSlot
    """
    olt_name = serializers.CharField(source='olt.name', read_only=True)

    class Meta:
        model = OLTSlot
        fields = ['id', 'olt', 'olt_name', 'slot_id', 'slot_key', 'name', 'rack_id', 'shelf_id',
                  'is_active', 'last_discovered_at', 'created_at']
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
        fields = ['id', 'olt', 'olt_name', 'slot', 'slot_id', 'slot_key', 'pon_id', 'pon_key',
                  'pon_index', 'name', 'rack_id', 'shelf_id', 'port_id', 'is_active',
                  'last_discovered_at', 'created_at']
        read_only_fields = ['id', 'last_discovered_at', 'created_at']


class ONUSerializer(serializers.ModelSerializer):
    """
    Serializer for ONU
    """
    olt_name = serializers.CharField(source='olt.name', read_only=True)
    slot = serializers.IntegerField(source='slot_ref_id', read_only=True)
    pon = serializers.IntegerField(source='pon_ref_id', read_only=True)
    slot_key = serializers.CharField(source='slot_ref.slot_key', read_only=True)
    pon_key = serializers.CharField(source='pon_ref.pon_key', read_only=True)
    slot_name = serializers.CharField(source='slot_ref.name', read_only=True)
    pon_name = serializers.CharField(source='pon_ref.name', read_only=True)
    
    class Meta:
        model = ONU
        fields = ['id', 'olt', 'olt_name', 'slot_id', 'slot', 'slot_key', 'slot_name',
                  'pon_id', 'pon', 'pon_key', 'pon_name', 'onu_id',
                  'snmp_index', 'name', 'serial_number', 'status', 'last_discovered_at']
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
