from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_latest_power_snapshot(apps, schema_editor):
    ONU = apps.get_model("topology", "ONU")
    ONUPowerSample = apps.get_model("topology", "ONUPowerSample")

    latest_sample_qs = ONUPowerSample.objects.filter(onu_id=OuterRef("pk")).order_by("-read_at")
    ONU.objects.update(
        latest_onu_rx_power=Subquery(latest_sample_qs.values("onu_rx_power")[:1]),
        latest_olt_rx_power=Subquery(latest_sample_qs.values("olt_rx_power")[:1]),
        latest_power_read_at=Subquery(latest_sample_qs.values("read_at")[:1]),
    )


def clear_latest_power_snapshot(apps, schema_editor):
    ONU = apps.get_model("topology", "ONU")
    ONU.objects.update(
        latest_onu_rx_power=None,
        latest_olt_rx_power=None,
        latest_power_read_at=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0036_blade_port_remove_telnet_port"),
    ]

    operations = [
        migrations.AddField(
            model_name="onu",
            name="latest_onu_rx_power",
            field=models.FloatField(blank=True, null=True, verbose_name="Última ONU RX (dBm)"),
        ),
        migrations.AddField(
            model_name="onu",
            name="latest_olt_rx_power",
            field=models.FloatField(blank=True, null=True, verbose_name="Última OLT RX (dBm)"),
        ),
        migrations.AddField(
            model_name="onu",
            name="latest_power_read_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Última Leitura de Potência"),
        ),
        migrations.RunPython(backfill_latest_power_snapshot, clear_latest_power_snapshot),
    ]
