from django.db import migrations


DEFAULT_INTERVALS = {
    "discovery_interval_minutes": 240,
    "polling_interval_seconds": 300,
}


def add_default_intervals_to_vendor_profiles(apps, schema_editor):
    VendorProfile = apps.get_model("dashboard", "VendorProfile")
    for profile in VendorProfile.objects.all():
        thresholds = profile.default_thresholds or {}
        merged = {**DEFAULT_INTERVALS, **thresholds}
        if merged != thresholds:
            profile.default_thresholds = merged
            profile.save(update_fields=["default_thresholds"])


def remove_default_intervals_from_vendor_profiles(apps, schema_editor):
    VendorProfile = apps.get_model("dashboard", "VendorProfile")
    for profile in VendorProfile.objects.all():
        thresholds = profile.default_thresholds or {}
        changed = False
        for key in DEFAULT_INTERVALS.keys():
            if key in thresholds:
                thresholds.pop(key, None)
                changed = True
        if changed:
            profile.default_thresholds = thresholds
            profile.save(update_fields=["default_thresholds"])


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0005_onu_topology_links"),
    ]

    operations = [
        migrations.RunPython(
            add_default_intervals_to_vendor_profiles,
            remove_default_intervals_from_vendor_profiles,
        ),
    ]

