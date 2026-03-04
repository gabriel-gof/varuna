from django.db import migrations


AVAILABILITY_ITEM_KEY = "varunaSnmpAvailability"


def add_availability_item_key(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        zabbix = dict(templates.get("zabbix") or {})

        if zabbix.get("availability_item_key"):
            continue

        zabbix["availability_item_key"] = AVAILABILITY_ITEM_KEY
        templates["zabbix"] = zabbix
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def remove_availability_item_key(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        zabbix = dict(templates.get("zabbix") or {})
        if "availability_item_key" not in zabbix:
            continue
        zabbix.pop("availability_item_key", None)
        templates["zabbix"] = zabbix
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


class Migration(migrations.Migration):
    dependencies = [
        ("topology", "0023_rename_topology_on_olt_id_3344e0_idx_topology_on_olt_id_53cb05_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(add_availability_item_key, remove_availability_item_key),
    ]
