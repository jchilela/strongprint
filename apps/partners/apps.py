from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PartnersConfig(AppConfig):
    name = "apps.partners"
    label = "partners"
    verbose_name = _("Partners and kiosks")
