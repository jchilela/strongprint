import phonenumbers
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

DEFAULT_REGION = "AO"


def normalize_phone(raw: str) -> str:
    """Normaliza para E.164 (ex.: '923 456 789' → '+244923456789')."""
    try:
        number = phonenumbers.parse(str(raw), DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(_("Invalid phone number."), code="invalid_phone") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValidationError(_("Invalid phone number."), code="invalid_phone")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
