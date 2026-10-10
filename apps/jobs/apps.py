from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class JobsConfig(AppConfig):
    name = "apps.jobs"
    label = "jobs"
    verbose_name = _("Print jobs")
