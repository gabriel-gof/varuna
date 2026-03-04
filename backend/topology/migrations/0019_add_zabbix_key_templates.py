from django.db import migrations


def add_zabbix_templates(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        zabbix = dict(templates.get("zabbix") or {})

        vendor = str(profile.vendor or "").strip().lower()
        model = str(profile.model_name or "").strip().lower()

        zabbix.setdefault("discovery_item_key", "onuDiscovery")
        zabbix.setdefault("status_item_key_pattern", "onuStatusValue[{index}]")

        if vendor == "fiberhome" and model == "an5516":
            zabbix.setdefault("status_collection_key", "")
            zabbix.setdefault("reason_item_key_pattern", "")
            zabbix.setdefault("onu_rx_item_key_pattern", "onuRxPower[{index}]")
            zabbix.setdefault("olt_rx_item_key_pattern", "oltRxPower[{index}]")
        elif vendor == "huawei":
            zabbix.setdefault("status_collection_key", "onuStatusCollection")
            zabbix.setdefault("reason_item_key_pattern", "onuDisconnectReason[{index}]")
            zabbix.setdefault("onu_rx_item_key_pattern", "onuRxPower[{index}]")
            zabbix.setdefault("olt_rx_item_key_pattern", "oltRxPower[{index}]")
        else:
            zabbix.setdefault("status_collection_key", "onuStatusCollection")
            zabbix.setdefault("reason_item_key_pattern", "onuDisconnectReason[{index}]")
            zabbix.setdefault("onu_rx_item_key_pattern", "onuRxPower[{index}]")
            zabbix.setdefault("olt_rx_item_key_pattern", "oltRxPower[{index}]")

        templates["zabbix"] = zabbix
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def remove_zabbix_templates(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        if "zabbix" not in templates:
            continue
        templates = dict(templates)
        templates.pop("zabbix", None)
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


class Migration(migrations.Migration):
    dependencies = [
        ("topology", "0018_onupowersample"),
    ]

    operations = [
        migrations.RunPython(add_zabbix_templates, remove_zabbix_templates),
    ]
