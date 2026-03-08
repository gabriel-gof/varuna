from django.db import migrations, models


FIT_FNCS4000_TEMPLATES = {
    "collector": {
        "type": "fit_telnet",
        "transport": "telnet",
        "interfaces": ["0/1", "0/2", "0/3", "0/4"],
    },
    "power": {
        "supports_olt_rx_power": False,
        "onu_rx_source": "optical_ddm",
        "onu_id_limit": 64,
    },
}


def seed_fit_fncs4000_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")

    default_thresholds = {
        "discovery_interval_minutes": 240,
        "polling_interval_seconds": 300,
        "power_interval_seconds": 1800,
    }

    VendorProfile.objects.update_or_create(
        vendor="FIT",
        model_name="FNCS4000",
        defaults={
            "description": (
                "FIT FNCS4000 profile with direct Telnet collector for fixed EPON 0/1-0/4 "
                "topology, CLI status discovery, and ONU RX-only power polling."
            ),
            "oid_templates": FIT_FNCS4000_TEMPLATES,
            "supports_onu_discovery": True,
            "supports_onu_status": True,
            "supports_power_monitoring": True,
            "supports_disconnect_reason": False,
            "default_thresholds": default_thresholds,
            "is_active": True,
        },
    )


def remove_fit_fncs4000_profile(apps, schema_editor):
    VendorProfile = apps.get_model("topology", "VendorProfile")
    VendorProfile.objects.filter(vendor__iexact="FIT", model_name__iexact="FNCS4000").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0029_add_unm_integration_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="olt",
            name="protocol",
            field=models.CharField(
                choices=[("snmp", "SNMP"), ("telnet", "Telnet")],
                default="snmp",
                max_length=20,
                verbose_name="Protocolo",
            ),
        ),
        migrations.AddField(
            model_name="olt",
            name="telnet_port",
            field=models.IntegerField(default=23, verbose_name="Porta Telnet"),
        ),
        migrations.AddField(
            model_name="olt",
            name="telnet_username",
            field=models.CharField(blank=True, default="", max_length=100, verbose_name="Usuário Telnet"),
        ),
        migrations.AddField(
            model_name="olt",
            name="telnet_password",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Senha Telnet"),
        ),
        migrations.RunPython(seed_fit_fncs4000_profile, remove_fit_fncs4000_profile),
    ]
