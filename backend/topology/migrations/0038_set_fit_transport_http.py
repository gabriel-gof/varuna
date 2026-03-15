from django.db import migrations


HTTP_DESCRIPTION = (
    "FIT FNCS4000 profile with direct HTTP collector for fixed EPON 0/1-0/4 "
    "topology, web UI status discovery, and ONU RX-only power polling."
)

TELNET_DESCRIPTION = (
    "FIT FNCS4000 profile with direct Telnet collector for fixed EPON 0/1-0/4 "
    "topology, CLI status discovery, and ONU RX-only power polling."
)


def set_fit_transport_http(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.filter(vendor__iexact="FIT", model_name__iexact="FNCS4000"):
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        collector_cfg = templates.get("collector") if isinstance(templates.get("collector"), dict) else {}

        changed = False
        if collector_cfg.get("transport") != "http":
            collector_cfg["transport"] = "http"
            changed = True

        if changed:
            templates["collector"] = collector_cfg
            profile.oid_templates = templates

        update_fields = []
        if changed:
            update_fields.append("oid_templates")
        if str(profile.description or "").strip() != HTTP_DESCRIPTION:
            profile.description = HTTP_DESCRIPTION
            update_fields.append("description")

        if update_fields:
            profile.save(update_fields=update_fields)


def revert_fit_transport_http(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.filter(vendor__iexact="FIT", model_name__iexact="FNCS4000"):
        templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
        collector_cfg = templates.get("collector") if isinstance(templates.get("collector"), dict) else {}

        changed = False
        if collector_cfg.get("transport") != "telnet":
            collector_cfg["transport"] = "telnet"
            changed = True

        if changed:
            templates["collector"] = collector_cfg
            profile.oid_templates = templates

        update_fields = []
        if changed:
            update_fields.append("oid_templates")
        if str(profile.description or "").strip() != TELNET_DESCRIPTION:
            profile.description = TELNET_DESCRIPTION
            update_fields.append("description")

        if update_fields:
            profile.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0037_onu_latest_power_snapshot"),
    ]

    operations = [
        migrations.RunPython(set_fit_transport_http, revert_fit_transport_http),
    ]
