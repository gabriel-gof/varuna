from django.db import migrations


ZTE_C600_TEMPLATES = {
    "indexing": {
        "format": "pon_onu",
        "pon_encoding": "0x11rrsspp",
        "slot_from": "shelf",
        "pon_from": "port",
    },
    "discovery": {
        "onu_name_oid": "1.3.6.1.4.1.3902.1082.500.10.2.3.3.1.2",
        "onu_serial_oid": "1.3.6.1.4.1.3902.1082.500.10.2.3.3.1.18",
        "onu_status_oid": "1.3.6.1.4.1.3902.1082.500.10.2.3.8.1.4",
        "deactivate_missing": True,
        "disable_lost_after_minutes": 0,
        "delete_lost_after_minutes": 10080,
    },
    "status": {
        "onu_status_oid": "1.3.6.1.4.1.3902.1082.500.10.2.3.8.1.4",
        "status_map": {
            "1": {"status": "offline", "reason": "link_loss"},
            "2": {"status": "offline", "reason": "unknown"},
            "3": {"status": "online"},
            "4": {"status": "online"},
            "5": {"status": "offline", "reason": "unknown"},
            "6": {"status": "offline", "reason": "unknown"},
            "7": {"status": "offline", "reason": "unknown"},
        },
    },
    "power": {
        "olt_rx_oid": "1.3.6.1.4.1.3902.1082.500.1.2.4.2.1.2",
        "onu_rx_oid": "1.3.6.1.4.1.3902.1082.500.20.2.2.2.1.10",
        "onu_rx_suffix": "1",
    },
    "pon_interfaces": {
        "name_oid": "1.3.6.1.2.1.31.1.1.1.1",
        "status_oid": "1.3.6.1.2.1.2.2.1.8",
        "name_regex": r"^gpon_(\d+)/(\d+)/(\d+)$",
        "status_up": "1",
    },
    "zabbix": {
        "host_template_name": "OLT ZTE C600",
        "host_template_names": ["OLT ZTE C600"],
        "discovery_item_key": "onuDiscovery",
        "availability_item_key": "varunaSnmpAvailability",
        "status_collection_key": "",
        "status_item_key_pattern": "onuStatusValue[{index}]",
        "reason_item_key_pattern": "",
        "onu_rx_item_key_pattern": "onuRxPower[{index}]",
        "olt_rx_item_key_pattern": "oltRxPower[{index}]",
    },
}


def seed_zte_c600_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    default_thresholds = {
        "discovery_interval_minutes": 240,
        "polling_interval_seconds": 300,
        "power_interval_seconds": 300,
    }

    VendorProfile.objects.update_or_create(
        vendor="zte",
        model_name="C600",
        defaults={
            "description": "ZTE C600/C620 profile with Zabbix-native discovery/status/power templates",
            "oid_templates": ZTE_C600_TEMPLATES,
            "supports_onu_discovery": True,
            "supports_onu_status": True,
            "supports_power_monitoring": True,
            "supports_disconnect_reason": True,
            "default_thresholds": default_thresholds,
            "is_active": True,
        },
    )


def remove_zte_c600_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    VendorProfile.objects.filter(vendor__iexact="zte", model_name__iexact="C600").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0026_standardize_zabbix_template_names"),
    ]

    operations = [
        migrations.RunPython(seed_zte_c600_profile, remove_zte_c600_profile),
    ]
