from django.db import migrations


def forward_align_fiberhome_keys(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        vendor = str(profile.vendor or "").strip().lower()
        if vendor != "fiberhome":
            continue

        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        zabbix = dict(templates.get("zabbix") or {})

        zabbix["discovery_item_key"] = "onuDiscovery"
        zabbix["status_item_key_pattern"] = "onuStatusValue[{index}]"
        zabbix["reason_item_key_pattern"] = ""
        zabbix["status_collection_key"] = ""
        zabbix["onu_rx_item_key_pattern"] = "onuRxPower[{index}]"
        zabbix["olt_rx_item_key_pattern"] = "oltRxPower[{index}]"

        templates = dict(templates)
        templates["zabbix"] = zabbix
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def backward_align_fiberhome_keys(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        vendor = str(profile.vendor or "").strip().lower()
        if vendor != "fiberhome":
            continue

        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        zabbix = dict(templates.get("zabbix") or {})

        zabbix["discovery_item_key"] = "onuDiscovery"
        zabbix["status_item_key_pattern"] = "onuStatusValue[{index}]"
        zabbix["reason_item_key_pattern"] = "onuDisconnectReason[{index}]"
        zabbix["status_collection_key"] = "onuStatus"
        zabbix["onu_rx_item_key_pattern"] = "onuPonRxOpticalPower[{index}]"
        zabbix["olt_rx_item_key_pattern"] = "onuPonRxOpticalPowerInOlt[{index}]"

        templates = dict(templates)
        templates["zabbix"] = zabbix
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


class Migration(migrations.Migration):
    dependencies = [
        ("topology", "0019_add_zabbix_key_templates"),
    ]

    operations = [
        migrations.RunPython(forward_align_fiberhome_keys, backward_align_fiberhome_keys),
    ]
