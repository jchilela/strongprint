from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PartnerPortalConfig(AppConfig):
    name = "apps.partner_portal"
    label = "partner_portal"
    verbose_name = _("Partner portal")
