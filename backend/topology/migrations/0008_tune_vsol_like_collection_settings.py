from django.db import migrations


STATUS_DEFAULTS = {
    "get_chunk_size": 12,
    "chunk_retry_attempts": 2,
    "single_oid_retry_attempts": 2,
    "retry_backoff_seconds": 0.25,
    "snmp_timeout_seconds": 2.0,
    "snmp_retries": 0,
    "max_get_call_multiplier": 24,
    "pause_between_pon_batches_seconds": 0.12,
}

POWER_DEFAULTS = {
    "get_chunk_size": 8,
    "chunk_retry_attempts": 2,
    "single_oid_retry_attempts": 2,
    "retry_backoff_seconds": 0.25,
    "snmp_timeout_seconds": 2.0,
    "snmp_retries": 0,
    "max_get_call_multiplier": 24,
    "pause_between_pon_batches_seconds": 0.12,
    "max_online_retry_onus": 512,
    "pause_between_single_retries_seconds": 0.03,
}


def tune_vsol_like_collection_settings(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    profiles = VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P")

    for profile in profiles:
        templates = profile.oid_templates or {}

        status_cfg = templates.get("status") if isinstance(templates.get("status"), dict) else {}
        for key, value in STATUS_DEFAULTS.items():
            status_cfg.setdefault(key, value)

        power_cfg = templates.get("power") if isinstance(templates.get("power"), dict) else {}
        for key, value in POWER_DEFAULTS.items():
            power_cfg.setdefault(key, value)

        templates["status"] = status_cfg
        templates["power"] = power_cfg
        profile.oid_templates = templates
        profile.save(update_fields=["oid_templates"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0007_fix_vsol_like_index_regex"),
    ]

    operations = [
        migrations.RunPython(tune_vsol_like_collection_settings, noop_reverse),
    ]
