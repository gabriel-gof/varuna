from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("topology", "0021_standardize_vendor_models_unificado")]

    operations = [
        migrations.AddField(
            model_name="olt",
            name="history_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text="Janela de histórico de alarmes exibida para ONUs desta OLT (em dias)",
                verbose_name="Janela de Histórico (dias)",
            ),
        ),
    ]
