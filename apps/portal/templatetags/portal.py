from django import template

register = template.Library()


@register.filter
def kz(value):
    """1900 → "1 900 Kz" (espaço fino inquebrável como separador de milhares)."""
    if value is None or value == "":
        return "—"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return value
    sign = "−" if number < 0 else ""
    return f"{sign}{abs(number):,}".replace(",", " ") + " Kz"


@register.filter
def signed_kz(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return value
    return ("+" if number > 0 else "") + kz(number)
