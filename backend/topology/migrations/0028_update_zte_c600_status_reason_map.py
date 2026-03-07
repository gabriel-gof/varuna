from django.db import migrations


PREVIOUS_STATUS_MAP = {
    "1": {"status": "offline", "reason": "link_loss"},
    "2": {"status": "offline", "reason": "unknown"},
    "3": {"status": "online"},
    "4": {"status": "online"},
    "5": {"status": "offline", "reason": "unknown"},
    "6": {"status": "offline", "reason": "unknown"},
    "7": {"status": "offline", "reason": "unknown"},
}

UPDATED_STATUS_MAP = {
    "1": {"status": "offline", "reason": "link_loss"},
    "2": {"status": "offline", "reason": "link_loss"},
    "3": {"status": "online"},
    "4": {"status": "online"},
    "5": {"status": "offline", "reason": "dying_gasp"},
    "6": {"status": "offline", "reason": "unknown"},
    "7": {"status": "offline", "reason": "unknown"},
}


def _update_status_map(apps, status_map):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    profile = VendorProfile.objects.filter(
        vendor__iexact="zte",
        model_name__iexact="C600",
    ).first()
    if not profile:
        return
    templates = dict(profile.oid_templates or {})
    status_cfg = dict(templates.get("status", {}) or {})
    status_cfg["status_map"] = dict(status_map)
    templates["status"] = status_cfg
    profile.oid_templates = templates
    profile.save(update_fields=["oid_templates"])


def apply_status_map_update(apps, schema_editor):
    _update_status_map(apps, UPDATED_STATUS_MAP)


def revert_status_map_update(apps, schema_editor):
    _update_status_map(apps, PREVIOUS_STATUS_MAP)


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0027_seed_zte_c600_profile"),
    ]

    operations = [
        migrations.RunPython(apply_status_map_update, revert_status_map_update),
    ]
