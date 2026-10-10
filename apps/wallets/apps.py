from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class WalletsConfig(AppConfig):
    name = "apps.wallets"
    label = "wallets"
    verbose_name = _("Wallets")
