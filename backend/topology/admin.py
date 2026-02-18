from django.contrib import admin
from topology.models.models import UserProfile, VendorProfile, OLT, OLTSlot, OLTPON, ONU, ONULog

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'last_login_ip', 'created_at')
    list_filter = ('role',)


@admin.register(VendorProfile)
class VendorProfileAdmin(admin.ModelAdmin):
    list_display = ('vendor', 'model_name', 'description', 'is_active')
    list_filter = ('vendor', 'is_active')


@admin.register(OLT)
class OLTAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'vendor_profile',
        'ip_address',
        'snmp_reachable',
        'snmp_failure_count',
        'is_active',
        'discovery_enabled',
        'polling_enabled',
    )
    list_filter = ('vendor_profile', 'protocol', 'is_active', 'discovery_enabled', 'polling_enabled')


@admin.register(OLTSlot)
class OLTSlotAdmin(admin.ModelAdmin):
    list_display = ('olt', 'slot_id', 'rack_id', 'shelf_id', 'slot_key', 'is_active', 'last_discovered_at')
    list_filter = ('olt', 'is_active')


@admin.register(OLTPON)
class OLTPONAdmin(admin.ModelAdmin):
    list_display = ('olt', 'slot', 'pon_id', 'pon_key', 'pon_index', 'description', 'is_active', 'last_discovered_at')
    list_filter = ('olt', 'slot', 'is_active')


@admin.register(ONU)
class ONUAdmin(admin.ModelAdmin):
    list_display = ('olt', 'slot_id', 'pon_id', 'onu_id', 'name', 'serial', 'status', 'is_active')
    list_filter = ('status', 'is_active', 'olt', 'slot_id', 'pon_id')


@admin.register(ONULog)
class ONULogAdmin(admin.ModelAdmin):
    list_display = ('onu', 'offline_since', 'offline_until', 'disconnect_reason')
    list_filter = ('disconnect_reason',)
    date_hierarchy = 'offline_since'
