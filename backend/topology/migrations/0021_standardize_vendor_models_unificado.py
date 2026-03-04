from django.db import migrations


def standardize_models(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    for profile in VendorProfile.objects.all():
        vendor = str(profile.vendor or "").strip().lower()
        if vendor in {"fiberhome", "huawei"} and str(profile.model_name or "").strip().upper() != "UNIFICADO":
            profile.model_name = "UNIFICADO"
            profile.save(update_fields=["model_name"])


def restore_models(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    VendorProfile.objects.filter(vendor__iexact="Fiberhome", model_name__iexact="UNIFICADO").update(model_name="AN5516")
    VendorProfile.objects.filter(vendor__iexact="Huawei", model_name__iexact="UNIFICADO").update(model_name="MA5680T")


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0020_align_fiberhome_zabbix_keys"),
    ]

    operations = [
        migrations.RunPython(standardize_models, restore_models),
    ]
