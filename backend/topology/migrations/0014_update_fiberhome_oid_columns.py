from django.db import migrations


def update_fiberhome_to_oid_columns(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    try:
        vp = VendorProfile.objects.get(vendor__iexact="Fiberhome", model_name__iexact="AN5516")
    except VendorProfile.DoesNotExist:
        return

    oid_templates = vp.oid_templates or {}

    oid_templates["indexing"] = {
        "index_from": "oid_columns",
        "onu_id_extract": "byte2",
    }

    discovery = oid_templates.get("discovery", {})
    discovery["onu_slot_oid"] = "1.3.6.1.4.1.5875.800.3.10.1.1.2"
    discovery["onu_pon_oid"] = "1.3.6.1.4.1.5875.800.3.10.1.1.3"
    oid_templates["discovery"] = discovery

    power = oid_templates.get("power", {})
    power["olt_rx_index_formula"] = "fiberhome_pon_onu"
    oid_templates["power"] = power

    # pon_interfaces no longer needed — slot/pon come from OID columns
    oid_templates.pop("pon_interfaces", None)

    vp.oid_templates = oid_templates
    vp.save(update_fields=["oid_templates"])


def revert_fiberhome_to_regex(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    try:
        vp = VendorProfile.objects.get(vendor__iexact="Fiberhome", model_name__iexact="AN5516")
    except VendorProfile.DoesNotExist:
        return

    oid_templates = vp.oid_templates or {}

    oid_templates["indexing"] = {
        "regex": r"^(?P<slot_id>\d+)\.(?P<pon_id>\d+)\.(?P<onu_id>\d+)$",
    }

    discovery = oid_templates.get("discovery", {})
    discovery.pop("onu_slot_oid", None)
    discovery.pop("onu_pon_oid", None)
    oid_templates["discovery"] = discovery

    power = oid_templates.get("power", {})
    power.pop("olt_rx_index_formula", None)
    oid_templates["power"] = power

    oid_templates["pon_interfaces"] = {
        "name_oid": "1.3.6.1.4.1.5875.800.3.9.3.4.1.2",
        "name_regex": r"^(?:GPON|PON)\s*(\d+)/(\d+)/(\d+)$",
    }

    vp.oid_templates = oid_templates
    vp.save(update_fields=["oid_templates"])


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0013_seed_fiberhome_vendor_profile"),
    ]

    operations = [
        migrations.RunPython(update_fiberhome_to_oid_columns, revert_fiberhome_to_regex),
    ]
