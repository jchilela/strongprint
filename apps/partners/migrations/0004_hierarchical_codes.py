import django.core.validators
from django.db import migrations, models


def fill_codes(apps, schema_editor):
    Partner = apps.get_model("partners", "Partner")
    Site = apps.get_model("partners", "Site")
    Kiosk = apps.get_model("partners", "Kiosk")
    for partner in Partner.objects.all():
        partner.code = partner.code.upper().replace("-", "")
        partner.save(update_fields=["code"])
        for n, site in enumerate(Site.objects.filter(partner=partner).order_by("id"), start=1):
            site.number = n
            site.save(update_fields=["number"])
            for k, kiosk in enumerate(Kiosk.objects.filter(site=site).order_by("id"), start=1):
                kiosk.number = k
                kiosk.code = f"{partner.code}-S{n:02d}-K{k:02d}"
                kiosk.save(update_fields=["number", "code"])


class Migration(migrations.Migration):
    dependencies = [("partners", "0003_site_coordinates")]

    operations = [
        migrations.RenameField(model_name="partner", old_name="slug", new_name="code"),
        migrations.AlterField(
            model_name="partner",
            name="code",
            field=models.CharField(max_length=50, unique=True),
        ),
        migrations.AddField(
            model_name="site",
            name="number",
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="kiosk",
            name="number",
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.RunPython(fill_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="partner",
            name="code",
            field=models.CharField(
                help_text="Short code used in equipment names, e.g. IMETRO.",
                max_length=12,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        "^[A-Z0-9]+$", "Use uppercase letters and digits only."
                    )
                ],
                verbose_name="code",
            ),
        ),
        migrations.AlterField(
            model_name="site",
            name="number",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Empty = next available (S01, S02, ...).",
                verbose_name="number",
            ),
        ),
        migrations.AlterField(
            model_name="site",
            name="name",
            field=models.CharField(
                blank=True, help_text="Empty = Site-NN.", max_length=150, verbose_name="name"
            ),
        ),
        migrations.AlterField(
            model_name="kiosk",
            name="number",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Empty = next available in the site (K01, K02, ...).",
                verbose_name="number",
            ),
        ),
        migrations.AlterField(
            model_name="kiosk",
            name="code",
            field=models.CharField(
                editable=False,
                help_text="Generated: <PARTNER>-S<nn>-K<nn>, e.g. IMETRO-S01-K01.",
                max_length=40,
                unique=True,
                verbose_name="code",
            ),
        ),
        migrations.AlterField(
            model_name="kiosk",
            name="name",
            field=models.CharField(
                blank=True, help_text="Empty = Kiosk-NN.", max_length=150, verbose_name="name"
            ),
        ),
        migrations.AlterModelOptions(
            name="site",
            options={
                "ordering": ["partner__code", "number"],
                "verbose_name": "site",
                "verbose_name_plural": "sites",
            },
        ),
        migrations.AddConstraint(
            model_name="site",
            constraint=models.UniqueConstraint(
                fields=("partner", "number"), name="uniq_site_number"
            ),
        ),
        migrations.AddConstraint(
            model_name="kiosk",
            constraint=models.UniqueConstraint(fields=("site", "number"), name="uniq_kiosk_number"),
        ),
    ]
