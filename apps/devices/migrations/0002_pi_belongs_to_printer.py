import django.db.models.deletion
from django.db import migrations, models


def ensure_no_devices(apps, schema_editor):
    # O Pi passa a pertencer a uma impressora (obrigatório). Não havia Pi registados.
    if apps.get_model("devices", "EdgeDevice").objects.exists():
        raise RuntimeError("Associe manualmente cada Pi a uma impressora antes de migrar.")


class Migration(migrations.Migration):
    dependencies = [("devices", "0001_initial")]

    operations = [
        migrations.RunPython(ensure_no_devices, migrations.RunPython.noop),
        migrations.RemoveField(model_name="edgedevice", name="kiosk"),
        migrations.AddField(
            model_name="edgedevice",
            name="printer",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="edge_device",
                to="devices.printer",
                verbose_name="printer",
            ),
            preserve_default=False,
        ),
    ]
