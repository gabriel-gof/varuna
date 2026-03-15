from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0038_set_fit_transport_http"),
    ]

    operations = [
        migrations.AlterField(
            model_name="olt",
            name="history_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text="Janela de histórico de alarmes exibida para ONUs desta OLT (em dias)",
                validators=[
                    django.core.validators.MinValueValidator(7),
                    django.core.validators.MaxValueValidator(30),
                ],
                verbose_name="Janela de Histórico (dias)",
            ),
        ),
        migrations.AlterField(
            model_name="olt",
            name="snmp_version",
            field=models.CharField(
                choices=[("v2c", "v2c")],
                default="v2c",
                max_length=10,
                verbose_name="Versão SNMP",
            ),
        ),
    ]
