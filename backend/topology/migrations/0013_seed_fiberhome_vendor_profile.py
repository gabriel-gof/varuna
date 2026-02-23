from django.db import migrations


def seed_fiberhome_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    oid_templates = {
        "indexing": {
            "index_from": "oid_columns",
            "onu_id_extract": "byte2",
        },
        "discovery": {
            "onu_name_oid": "",
            "onu_serial_oid": "1.3.6.1.4.1.5875.800.3.10.1.1.10",
            "onu_status_oid": "1.3.6.1.4.1.5875.800.3.10.1.1.11",
            "onu_slot_oid": "1.3.6.1.4.1.5875.800.3.10.1.1.2",
            "onu_pon_oid": "1.3.6.1.4.1.5875.800.3.10.1.1.3",
            "deactivate_missing": True,
            "disable_lost_after_minutes": 0,
            "delete_lost_after_minutes": 10080,
        },
        "status": {
            "onu_status_oid": "1.3.6.1.4.1.5875.800.3.10.1.1.11",
            "status_map": {
                "0": {"status": "offline", "reason": "link_loss"},
                "1": {"status": "online"},
                "2": {"status": "offline", "reason": "dying_gasp"},
                "3": {"status": "unknown", "reason": "unknown"},
            },
        },
        "power": {
            "onu_rx_oid": "1.3.6.1.4.1.5875.800.3.9.3.3.1.6",
            "olt_rx_oid": "1.3.6.1.4.1.5875.800.3.9.3.7.1.2",
            "onu_rx_formula": "hundredths_dbm",
            "olt_rx_formula": "hundredths_dbm",
            "olt_rx_index_formula": "fiberhome_pon_onu",
        },
    }

    default_thresholds = {
        "discovery_interval_minutes": 240,
        "polling_interval_seconds": 300,
        "power_interval_seconds": 300,
    }

    VendorProfile.objects.update_or_create(
        vendor="Fiberhome",
        model_name="AN5516",
        defaults={
            "description": "Fiberhome AN5516 OID templates for ONU discovery, status, and power",
            "oid_templates": oid_templates,
            "supports_onu_discovery": True,
            "supports_onu_status": True,
            "supports_power_monitoring": True,
            "supports_disconnect_reason": False,
            "default_thresholds": default_thresholds,
            "is_active": True,
        },
    )


def remove_fiberhome_vendor_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    VendorProfile.objects.filter(vendor__iexact="Fiberhome", model_name__iexact="AN5516").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0012_seed_huawei_vendor_profile"),
    ]

    operations = [
        migrations.RunPython(seed_fiberhome_vendor_profile, remove_fiberhome_vendor_profile),
    ]
