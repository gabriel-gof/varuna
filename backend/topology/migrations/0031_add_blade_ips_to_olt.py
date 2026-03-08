from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0030_add_fit_telnet_support"),
    ]

    operations = [
        migrations.AddField(
            model_name="olt",
            name="blade_ips",
            field=models.JSONField(
                blank=True,
                default=None,
                null=True,
                verbose_name="IPs das Blades",
            ),
        ),
    ]
