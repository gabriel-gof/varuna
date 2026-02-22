from django.db import migrations


def seed_vsol_like_gpon8p_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    oid_templates = {
        "indexing": {
            "regex": r"^(?P<pon_id>\d+)\.(?P<onu_id>\d+)$",
            "fixed": {
                "slot_id": 1,
            },
        },
        "discovery": {
            "onu_name_oid": "1.3.6.1.4.1.37950.1.1.6.1.1.4.1.24",
            "onu_serial_oid": "1.3.6.1.4.1.37950.1.1.6.1.1.4.1.5",
            "onu_status_oid": "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.5",
            "deactivate_missing": True,
            "disable_lost_after_minutes": 60,
            "delete_lost_after_minutes": 10080,
        },
        "status": {
            "onu_status_oid": "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.5",
            "status_map": {
                "1": {"status": "offline", "reason": "link_loss"},
                "2": {"status": "offline", "reason": "link_loss"},
                "3": {"status": "online"},
                "4": {"status": "offline", "reason": "dying_gasp"},
                "5": {"status": "offline", "reason": "dying_gasp"},
                "6": {"status": "offline", "reason": "unknown"},
                "7": {"status": "offline", "reason": "unknown"},
            },
        },
        "power": {
            "onu_rx_oid": "1.3.6.1.4.1.37950.1.1.6.1.1.3.1.7",
        },
    }

    default_thresholds = {
        "discovery_interval_minutes": 240,
        "polling_interval_seconds": 300,
        "power_interval_seconds": 300,
    }

    VendorProfile.objects.update_or_create(
        vendor="vsol like",
        model_name="GPON 8P",
        defaults={
            "description": "VSOL-like GPON 8P (white-label/OEM) profile with ONU-only RX power",
            "oid_templates": oid_templates,
            "supports_onu_discovery": True,
            "supports_onu_status": True,
            "supports_power_monitoring": True,
            "supports_disconnect_reason": True,
            "default_thresholds": default_thresholds,
            "is_active": True,
        },
    )


def remove_vsol_like_gpon8p_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0005_onulog_disconnect_window_bounds"),
    ]

    operations = [
        migrations.RunPython(seed_vsol_like_gpon8p_vendor_profile, remove_vsol_like_gpon8p_vendor_profile),
    ]
