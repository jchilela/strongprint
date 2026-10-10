from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ScreenConfig(AppConfig):
    name = "apps.screen"
    label = "screen"
    verbose_name = _("Kiosk screen")
