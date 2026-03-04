from django.db import migrations


def _set_template_names(profile, preferred, legacy):
    templates = profile.oid_templates if isinstance(profile.oid_templates, dict) else {}
    zabbix_cfg = templates.get("zabbix") if isinstance(templates.get("zabbix"), dict) else {}

    names = [preferred, legacy]
    deduped = []
    for value in names:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        if normalized not in deduped:
            deduped.append(normalized)

    zabbix_cfg["host_template_name"] = preferred
    zabbix_cfg["host_template_names"] = deduped

    templates["zabbix"] = zabbix_cfg
    profile.oid_templates = templates
    profile.save(update_fields=["oid_templates"])


def forward(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.filter(vendor__iexact="huawei"):
        _set_template_names(profile, "OLT Huawei Unified", "Template OLT Huawei")

    for profile in VendorProfile.objects.filter(vendor__iexact="fiberhome"):
        _set_template_names(profile, "OLT Fiberhome Unified", "Template OLT Fiberhome")

    for profile in VendorProfile.objects.filter(vendor__iexact="zte", model_name__iexact="C300"):
        _set_template_names(profile, "OLT ZTE C300", "Template OLT ZTE")

    for profile in VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P"):
        _set_template_names(profile, "OLT VSOL GPON 8P", "Template OLT VSOL Like")


def reverse(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.filter(vendor__iexact="huawei"):
        _set_template_names(profile, "Template OLT Huawei", "OLT Huawei Unified")

    for profile in VendorProfile.objects.filter(vendor__iexact="fiberhome"):
        _set_template_names(profile, "Template OLT Fiberhome", "OLT Fiberhome Unified")

    for profile in VendorProfile.objects.filter(vendor__iexact="zte", model_name__iexact="C300"):
        _set_template_names(profile, "Template OLT ZTE", "OLT ZTE C300")

    for profile in VendorProfile.objects.filter(vendor__iexact="vsol like", model_name__iexact="GPON 8P"):
        _set_template_names(profile, "Template OLT VSOL Like", "OLT VSOL GPON 8P")


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0025_seed_zte_vsol_zabbix_profiles"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
