"""Move telnet_port into per-blade objects and remove global telnet_port field.

Old blade_ips format: ["192.168.1.1", "192.168.1.2"]  (or null)
New blade_ips format: [{"ip": "192.168.1.1", "port": 23}, ...]  (or null)

The global telnet_port value is distributed to each blade entry during migration.
"""
from django.db import migrations, models


def migrate_blade_ips_forward(apps, schema_editor):
    OLT = apps.get_model("topology", "OLT")
    for olt in OLT.objects.all():
        port = getattr(olt, "telnet_port", 23) or 23
        if olt.blade_ips and isinstance(olt.blade_ips, list):
            new_blades = []
            for entry in olt.blade_ips:
                if isinstance(entry, dict):
                    # Already migrated
                    new_blades.append(entry)
                elif isinstance(entry, str) and entry.strip():
                    new_blades.append({"ip": entry.strip(), "port": port})
            olt.blade_ips = new_blades if new_blades else None
            olt.save(update_fields=["blade_ips"])


def migrate_blade_ips_backward(apps, schema_editor):
    OLT = apps.get_model("topology", "OLT")
    for olt in OLT.objects.all():
        if olt.blade_ips and isinstance(olt.blade_ips, list):
            first_port = 23
            flat_ips = []
            for entry in olt.blade_ips:
                if isinstance(entry, dict):
                    flat_ips.append(entry.get("ip", ""))
                    if not flat_ips or len(flat_ips) == 1:
                        first_port = entry.get("port", 23)
                elif isinstance(entry, str):
                    flat_ips.append(entry)
            olt.blade_ips = flat_ips if flat_ips else None
            olt.telnet_port = first_port
            olt.save(update_fields=["blade_ips", "telnet_port"])


class Migration(migrations.Migration):

    dependencies = [
        ("topology", "0035_enforce_fit_discovery_disable_lost_zero"),
    ]

    operations = [
        migrations.RunPython(migrate_blade_ips_forward, migrate_blade_ips_backward),
        migrations.RemoveField(
            model_name="olt",
            name="telnet_port",
        ),
    ]
