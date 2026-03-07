from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('topology', '0028_update_zte_c600_status_reason_map'),
    ]

    operations = [
        migrations.AddField(
            model_name='olt',
            name='unm_enabled',
            field=models.BooleanField(default=False, verbose_name='Integração UNM Ativada'),
        ),
        migrations.AddField(
            model_name='olt',
            name='unm_host',
            field=models.GenericIPAddressField(blank=True, null=True, verbose_name='Host do UNM'),
        ),
        migrations.AddField(
            model_name='olt',
            name='unm_port',
            field=models.PositiveIntegerField(default=3306, verbose_name='Porta do UNM'),
        ),
        migrations.AddField(
            model_name='olt',
            name='unm_username',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Usuário do UNM'),
        ),
        migrations.AddField(
            model_name='olt',
            name='unm_password',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Senha do UNM'),
        ),
        migrations.AddField(
            model_name='olt',
            name='unm_mneid',
            field=models.BigIntegerField(blank=True, null=True, verbose_name='UNM MNEID'),
        ),
    ]
