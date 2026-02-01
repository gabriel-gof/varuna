from rest_framework import serializers
from django.db.models import Count
from dashboard.models import VendorProfile, OLT, OLTSlot, OLTPON, ONU, ONULog, UserProfile


class VendorProfileSerializer(serializers.ModelSerializer):
    """
    Serializer para VendorProfile
    """
    class Meta:
        model = VendorProfile
        fields = ['id', 'vendor', 'model_name', 'description', 'supports_onu_discovery',
                  'supports_onu_status', 'supports_power_monitoring', 'supports_disconnect_reason']
        read_only_fields = ['id']


class OLTSerializer(serializers.ModelSerializer):
    """
    Serializer para OLT
    """
    vendor_display = serializers.CharField(source='vendor_profile.get_vendor_display', read_only=True)
    model_display = serializers.CharField(source='vendor_profile.model_name', read_only=True)
    slot_count = serializers.SerializerMethodField()
    pon_count = serializers.SerializerMethodField()
    onu_count = serializers.SerializerMethodField()
    online_count = serializers.SerializerMethodField()
    offline_count = serializers.SerializerMethodField()

    class Meta:
        model = OLT
        fields = ['id', 'name', 'vendor_profile', 'vendor_display', 'model_display',
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
        return ONU.objects.filter(olt=obj, status='offline').count()


class OLTSlotSerializer(serializers.ModelSerializer):
    """
    Serializer para OLTSlot
    """
    olt_name = serializers.CharField(source='olt.name', read_only=True)

    class Meta:
        model = OLTSlot
        fields = ['id', 'olt', 'olt_name', 'slot_id', 'slot_key', 'name', 'rack_id', 'shelf_id',
                  'is_active', 'last_discovered_at', 'created_at']
        read_only_fields = ['id', 'last_discovered_at', 'created_at']


class OLTPONSerializer(serializers.ModelSerializer):
    """
    Serializer para OLTPON
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
    Serializer para ONU
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
                  'snmp_index', 'name', 'serial', 'status', 'last_discovered_at']
        read_only_fields = ['id', 'last_discovered_at']


class ONULogSerializer(serializers.ModelSerializer):
    """
    Serializer para ONULog
    """
    class Meta:
        model = ONULog
        fields = ['id', 'onu', 'offline_since', 'offline_until', 'disconnect_reason']


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer para UserProfile
    """
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = UserProfile
        fields = ['id', 'user', 'username', 'email', 'role', 'last_login_ip', 'created_at']
        read_only_fields = ['id', 'created_at']
