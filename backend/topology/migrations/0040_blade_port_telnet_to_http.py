"""Migrate FIT blade ports from Telnet default (23) to HTTP default (80).

FIT FNCS4000 is now HTTP-only. Existing blade entries with port 23 are
updated to port 80 so the HTTP collector uses the correct port.
"""
from django.db import migrations


def migrate_blade_ports_to_http(apps, schema_editor):
    OLT = apps.get_model("topology", "OLT")
    for olt in OLT.objects.all():
        if not olt.blade_ips or not isinstance(olt.blade_ips, list):
            continue
        changed = False
        new_blades = []
        for entry in olt.blade_ips:
            if not isinstance(entry, dict):
                new_blades.append(entry)
                continue
            if entry.get("port") == 23:
                entry = {**entry, "port": 80}
                changed = True
            new_blades.append(entry)
        if changed:
            olt.blade_ips = new_blades
            olt.save(update_fields=["blade_ips"])


def revert_blade_ports_to_telnet(apps, schema_editor):
    OLT = apps.get_model("topology", "OLT")
    for olt in OLT.objects.all():
        if not olt.blade_ips or not isinstance(olt.blade_ips, list):
            continue
        changed = False
        new_blades = []
        for entry in olt.blade_ips:
            if not isinstance(entry, dict):
                new_blades.append(entry)
                continue
            if entry.get("port") == 80:
                entry = {**entry, "port": 23}
                changed = True
            new_blades.append(entry)
        if changed:
            olt.blade_ips = new_blades
            olt.save(update_fields=["blade_ips"])


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0039_enforce_olt_validation_contracts"),
    ]

    operations = [
        migrations.RunPython(migrate_blade_ports_to_http, revert_blade_ports_to_telnet),
    ]
