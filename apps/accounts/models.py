from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .phone import normalize_phone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra):
        user = self.model(phone=normalize_phone(phone), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create_user(phone, password, **extra)

    def get_by_natural_key(self, phone):
        return self.get(phone=normalize_phone(phone))


class User(AbstractBaseUser, PermissionsMixin):
    """Qualquer pessoa: cliente, utilizador de parceiro ou equipa StrongPrint.

    - Equipa StrongPrint (superadmin): is_staff / is_superuser.
    - Utilizadores de parceiro: PartnerMembership.
    - Clientes: qualquer utilizador com carteira (apps.wallets).
    """

    phone = models.CharField(_("phone number"), max_length=20, unique=True)
    full_name = models.CharField(_("full name"), max_length=150, blank=True)
    email = models.EmailField(_("email"), blank=True)
    language = models.CharField(
        _("language"), max_length=5, choices=settings.LANGUAGES, default="pt"
    )
    phone_verified_at = models.DateTimeField(_("phone verified at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("StrongPrint staff"), default=False)
    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return f"{self.full_name} ({self.phone})" if self.full_name else self.phone

    def clean(self):
        super().clean()
        if self.phone:
            self.phone = normalize_phone(self.phone)


class PhoneOTP(models.Model):
    """Código de acesso enviado por SMS/WhatsApp. Só o hash fica guardado."""

    phone = models.CharField(_("phone number"), max_length=20, db_index=True)
    code_hash = models.CharField(max_length=64)
    channel = models.CharField(_("channel"), max_length=12, default="sms")
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("login code")
        verbose_name_plural = _("login codes")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} @ {self.created_at:%Y-%m-%d %H:%M}"
