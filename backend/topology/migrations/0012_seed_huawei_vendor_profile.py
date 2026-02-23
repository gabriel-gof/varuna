from django.db import migrations


def seed_huawei_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    oid_templates = {
        "indexing": {
            "format": "pon_onu",
            "pon_resolve": "interface_map",
            "slot_from": "shelf",
            "pon_from": "port",
            "onu_id_position": 1,
        },
        "discovery": {
            "onu_name_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.43.1.9",
            "onu_serial_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.43.1.3",
            "onu_status_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15",
            "deactivate_missing": True,
            "disable_lost_after_minutes": 0,
            "delete_lost_after_minutes": 10080,
        },
        "status": {
            "onu_status_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15",
            "status_map": {
                "1": {"status": "online"},
                "2": {"status": "offline", "reason": "unknown"},
            },
            "disconnect_reason_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.46.1.24",
            "disconnect_reason_map": {
                "13": "dying_gasp",
                "1": "link_loss",
                "2": "link_loss",
                "3": "link_loss",
                "4": "link_loss",
                "5": "link_loss",
                "6": "link_loss",
                "15": "link_loss",
            },
        },
        "power": {
            "onu_rx_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4",
            "olt_rx_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.51.1.6",
            "onu_rx_formula": "hundredths_dbm",
            "olt_rx_formula": "huawei_olt_rx",
        },
        "pon_interfaces": {
            "name_oid": "1.3.6.1.2.1.31.1.1.1.1",
            "name_regex": r"^GPON\s+(\d+)/(\d+)/(\d+)$",
        },
    }

    default_thresholds = {
        "discovery_interval_minutes": 240,
        "polling_interval_seconds": 300,
        "power_interval_seconds": 300,
    }

    VendorProfile.objects.update_or_create(
        vendor="Huawei",
        model_name="MA5680T",
        defaults={
            "description": "Huawei MA5680T OID templates for ONU discovery, status, and power",
            "oid_templates": oid_templates,
            "supports_onu_discovery": True,
            "supports_onu_status": True,
            "supports_power_monitoring": True,
            "supports_disconnect_reason": True,
            "default_thresholds": default_thresholds,
            "is_active": True,
        },
    )


def remove_huawei_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    VendorProfile.objects.filter(vendor__iexact="Huawei", model_name__iexact="MA5680T").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0011_set_global_immediate_discovery_deactivation"),
    ]

    operations = [
        migrations.RunPython(seed_huawei_vendor_profile, remove_huawei_vendor_profile),
    ]
