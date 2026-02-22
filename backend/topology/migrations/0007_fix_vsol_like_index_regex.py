from django.db import migrations


BAD_REGEX = r"^(?P<pon_id>\\d+)\\.(?P<onu_id>\\d+)$"
GOOD_REGEX = r"^(?P<pon_id>\d+)\.(?P<onu_id>\d+)$"


def fix_vsol_like_index_regex(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    profiles = VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P")

    for profile in profiles:
        templates = profile.oid_templates or {}
        indexing = templates.get("indexing") if isinstance(templates.get("indexing"), dict) else {}
        regex = indexing.get("regex")
        if regex in (BAD_REGEX, None, ""):
            indexing["regex"] = GOOD_REGEX
            templates["indexing"] = indexing
            profile.oid_templates = templates
            profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0006_seed_vsol_like_gpon8p_vendor_profile"),
    ]

    operations = [
        migrations.RunPython(fix_vsol_like_index_regex, noop_reverse),
    ]
