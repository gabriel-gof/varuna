from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0031_add_blade_ips_to_olt"),
    ]

    operations = [
        migrations.RenameField(
            model_name="olt",
            old_name="snmp_reachable",
            new_name="collector_reachable",
        ),
        migrations.RenameField(
            model_name="olt",
            old_name="last_snmp_check_at",
            new_name="last_collector_check_at",
        ),
        migrations.RenameField(
            model_name="olt",
            old_name="last_snmp_error",
            new_name="last_collector_error",
        ),
        migrations.RenameField(
            model_name="olt",
            old_name="snmp_failure_count",
            new_name="collector_failure_count",
        ),
    ]
