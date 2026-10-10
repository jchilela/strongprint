import factory

from apps.accounts.models import User
from apps.devices.models import EdgeDevice, Printer
from apps.jobs.models import Job
from apps.partners.models import Kiosk, Partner, Site


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("phone",)

    phone = factory.Sequence(lambda n: f"+24492{n:07d}")
    full_name = factory.Faker("name")


class PartnerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Partner

    name = factory.Sequence(lambda n: f"Universidade {n}")
    code = factory.Sequence(lambda n: f"UNI{n}")


class SiteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Site

    partner = factory.SubFactory(PartnerFactory)
    city = "Luanda"


class KioskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Kiosk

    site = factory.SubFactory(SiteFactory)


class EdgeDeviceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EdgeDevice

    printer = factory.SubFactory("tests.factories.PrinterFactory")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        device = model_class(*args, **kwargs)
        device.plain_token = device.issue_token()
        device.save()
        return device


class PrinterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Printer

    kiosk = factory.SubFactory(KioskFactory)
    vpn_ip = factory.Sequence(lambda n: f"10.147.17.{n % 250 + 2}")


class JobFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Job

    kiosk = factory.SubFactory(KioskFactory)
    customer = factory.SubFactory(UserFactory)
