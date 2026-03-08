from django.db import migrations


def enforce_fit_discovery_disable_lost_zero(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.filter(vendor__iexact="FIT", model_name__iexact="FNCS4000"):
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        discovery_cfg = templates.get("discovery") if isinstance(templates.get("discovery"), dict) else {}

        changed = False
        if discovery_cfg.get("deactivate_missing") is not True:
            discovery_cfg["deactivate_missing"] = True
            changed = True

        if "disable_lost_after_minutes" not in discovery_cfg or int(discovery_cfg.get("disable_lost_after_minutes") or 0) != 0:
            discovery_cfg["disable_lost_after_minutes"] = 0
            changed = True

        if "delete_lost_after_minutes" not in discovery_cfg or discovery_cfg.get("delete_lost_after_minutes") in (None, ""):
            discovery_cfg["delete_lost_after_minutes"] = 10080
            changed = True

        if not changed:
            continue

        templates["discovery"] = discovery_cfg
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0034_set_fit_discovery_immediate_policy"),
    ]

    operations = [
        migrations.RunPython(enforce_fit_discovery_disable_lost_zero, noop_reverse),
    ]

