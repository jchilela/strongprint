from django.db import migrations

DEFAULTS = {
    "bw_page_kz": 100,
    "color_low_kz": 200,
    "color_mid_kz": 350,
    "color_high_kz": 500,
    "color_low_max_pct": 10,
    "color_mid_max_pct": 30,
    "min_transaction_kz": 100,
}


def create_global(apps, schema_editor):
    PriceTable = apps.get_model("partners", "PriceTable")
    if not PriceTable.objects.filter(partner=None, site=None, kiosk=None).exists():
        PriceTable.objects.create(**DEFAULTS)


class Migration(migrations.Migration):
    dependencies = [("partners", "0001_initial")]
    operations = [migrations.RunPython(create_global, migrations.RunPython.noop)]
