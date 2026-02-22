from django.db import migrations


UPDATED_STATUS_MAP = {
    "1": {"status": "offline", "reason": "link_loss"},
    "2": {"status": "offline", "reason": "link_loss"},
    "3": {"status": "online"},
    "4": {"status": "offline", "reason": "dying_gasp"},
    "5": {"status": "offline", "reason": "dying_gasp"},
    "6": {"status": "offline", "reason": "unknown"},
    "7": {"status": "offline", "reason": "unknown"},
}


def fix_vsol_like_status_map(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    profiles = VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P")

    for profile in profiles:
        templates = profile.oid_templates or {}
        status_cfg = templates.get("status") if isinstance(templates.get("status"), dict) else {}
        status_cfg["status_map"] = dict(UPDATED_STATUS_MAP)
        templates["status"] = status_cfg
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0008_tune_vsol_like_collection_settings"),
    ]

    operations = [
        migrations.RunPython(fix_vsol_like_status_map, noop_reverse),
    ]
