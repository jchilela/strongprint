"""Interface dos fornecedores de pagamento (carregamento da carteira)."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentProviderError(Exception):
    """O fornecedor recusou ou não respondeu. ``retryable`` indica falha transitória."""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


class InvalidWebhook(Exception):
    """Assinatura inválida ou corpo malformado."""


@dataclass(frozen=True)
class Order:
    provider_payment_id: str
    status: PaymentStatus
    entity: str | None = None
    reference: str | None = None
    expires_at: datetime | None = None
    transaction_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentUpdate:
    provider_payment_id: str
    status: PaymentStatus
    amount_kz: int | None = None
    paid_at: datetime | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def create_order(self, *, amount_kz: int, description: str, merchant_ref: str) -> Order:
        """Cria uma cobrança por referência e devolve entidade/referência a mostrar."""

    @abstractmethod
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentUpdate:
        """Valida a assinatura e normaliza o evento. Lança ``InvalidWebhook``."""

    @abstractmethod
    def fetch(self, provider_payment_id: str) -> PaymentUpdate:
        """Consulta o estado atual (reconciliação quando o webhook não chega)."""

    def refund(self, provider_payment_id: str, amount_kz: int) -> None:
        # Com a carteira pré-paga os reembolsos de impressão são internos (livro-razão).
        raise NotImplementedError(f"{self.name} does not support refunds")


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_amount_kz(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def parse_status(value) -> PaymentStatus:
    try:
        return PaymentStatus(str(value).lower())
    except ValueError:
        return PaymentStatus.PENDING
