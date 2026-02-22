from django.db import migrations
from django.db.models import Q


TARGET_PROFILES = (
    Q(vendor__iexact="zte", model_name__iexact="C300")
    | Q(vendor__iexact="vsol like", model_name__iexact="GPON 8P")
)


def set_immediate_discovery_deactivation(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    profiles = VendorProfile.objects.filter(TARGET_PROFILES)

    for profile in profiles:
        templates = profile.oid_templates or {}
        discovery_cfg = templates.get("discovery") if isinstance(templates.get("discovery"), dict) else {}

        discovery_cfg["deactivate_missing"] = True
        # If an ONU is not seen in discovery, remove it from active topology immediately.
        discovery_cfg["disable_lost_after_minutes"] = 0
        discovery_cfg.setdefault("delete_lost_after_minutes", 10080)

        templates["discovery"] = discovery_cfg
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0009_fix_vsol_like_status_map_phase_state"),
    ]

    operations = [
        migrations.RunPython(set_immediate_discovery_deactivation, noop_reverse),
    ]
