from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import User


class UserCreationForm(BaseUserCreationForm):
    """Clientes e parceiros entram por código SMS: a password é opcional
    (só faz falta para quem entra no superadmin)."""

    class Meta:
        model = User
        fields = ("phone", "full_name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("password1", "password2"):
            self.fields[name].required = False
        self.fields["password1"].help_text = _(
            "Leave empty for customers and partners (they sign in with an SMS code)."
        )
        if "usable_password" in self.fields:
            del self.fields["usable_password"]

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if (p1 or p2) and p1 != p2:
            raise forms.ValidationError(_("The two passwords do not match."))
        return p2

    def _post_clean(self):
        if self.cleaned_data.get("password1"):
            super()._post_clean()
        else:
            forms.ModelForm._post_clean(self)

    def save(self, commit=True):
        user = forms.ModelForm.save(self, commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class UserEditForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    add_form = UserCreationForm
    ordering = ("-date_joined",)
    list_display = ("phone", "full_name", "language", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active", "language")
    search_fields = ("phone", "full_name", "email")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (_("Profile"), {"fields": ("full_name", "email", "language", "phone_verified_at")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "full_name", "password1", "password2")}),
    )
