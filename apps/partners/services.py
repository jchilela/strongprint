from domain.pricing.service import PriceRules

from .models import PriceTable


class PriceTableMissing(Exception):
    pass


def effective_price_rules(kiosk) -> PriceRules:
    """Tabela efetiva do kiosk: cada campo vem do nível mais específico que o define
    (kiosk → local → parceiro → global)."""
    site = kiosk.site
    by_scope = {
        "kiosk": PriceTable.objects.filter(kiosk=kiosk).first(),
        "site": PriceTable.objects.filter(site=site).first(),
        "partner": PriceTable.objects.filter(partner_id=site.partner_id).first(),
        "global": PriceTable.objects.filter(partner=None, site=None, kiosk=None).first(),
    }
    if by_scope["global"] is None:
        raise PriceTableMissing("Global price table is missing")
    values = {}
    for field in PriceTable.PRICE_FIELDS:
        for scope in ("kiosk", "site", "partner", "global"):
            table = by_scope[scope]
            value = getattr(table, field) if table else None
            if value is not None:
                values[field] = value
                break
        else:
            raise PriceTableMissing(f"No value for {field}")
    return PriceRules(**values)
