from django.db import migrations


def set_global_immediate_discovery_deactivation(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        templates = profile.oid_templates or {}
        discovery_cfg = templates.get("discovery") if isinstance(templates.get("discovery"), dict) else {}

        discovery_cfg["deactivate_missing"] = True
        discovery_cfg["disable_lost_after_minutes"] = 0
        discovery_cfg.setdefault("delete_lost_after_minutes", 10080)

        templates["discovery"] = discovery_cfg
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0010_set_immediate_discovery_deactivation"),
    ]

    operations = [
        migrations.RunPython(set_global_immediate_discovery_deactivation, noop_reverse),
    ]
