from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.phone import normalize_phone


class PhoneForm(forms.Form):
    phone = forms.CharField(
        label=_("Phone number"),
        max_length=20,
        widget=forms.TextInput(
            attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "923 456 789"}
        ),
    )

    def clean_phone(self):
        try:
            return normalize_phone(self.cleaned_data["phone"])
        except ValidationError as exc:
            raise ValidationError(_("Enter a valid phone number.")) from exc


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_("Code"),
        min_length=6,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "pattern": "[0-9]{6}",
                "placeholder": "000000",
            }
        ),
    )


class NameForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), min_length=2, max_length=150)

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 2:
            raise forms.ValidationError(_("Please enter your name."))
        return name


class TopUpForm(forms.Form):
    PRESETS = [500, 1000, 2000, 5000]

    amount_kz = forms.IntegerField(
        label=_("Amount (Kz)"),
        min_value=settings.TOPUP_MIN_KZ,
        max_value=settings.TOPUP_MAX_KZ,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "step": 50}),
    )


class UploadForm(forms.Form):
    file = forms.FileField(label=_("Document"))
