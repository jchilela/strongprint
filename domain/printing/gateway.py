"""Interface da impressora vista pelo orquestrador."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class PrinterState(StrEnum):
    READY = "READY"
    BUSY = "BUSY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


class JobOutcome(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    CANCELED = "canceled"
    UNKNOWN = "unknown"

    @property
    def is_final(self) -> bool:
        return self in (JobOutcome.COMPLETED, JobOutcome.ABORTED, JobOutcome.CANCELED)


class PrinterUnavailable(Exception):
    """Impressora inalcançável (VPN, desligada) ou a recusar trabalhos."""


@dataclass(frozen=True)
class PrinterStatus:
    state: PrinterState
    can_print: bool
    paper_out: bool = False
    reasons: list[str] = field(default_factory=list)
    toner: dict[str, int] = field(default_factory=dict)  # cor → % (−1 = desconhecido)
    accepts_pdf: bool = True

    @property
    def low_toner(self) -> dict[str, int]:
        return {color: level for color, level in self.toner.items() if 0 <= level < 10}

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "can_print": self.can_print,
            "paper_out": self.paper_out,
            "reasons": self.reasons,
            "toner": self.toner,
        }


@dataclass(frozen=True)
class PrintRequest:
    pdf: bytes
    job_name: str
    color: bool
    duplex: bool
    copies: int


@dataclass(frozen=True)
class JobProgress:
    outcome: JobOutcome
    impressions: int | None = None
    reasons: list[str] = field(default_factory=list)


class PrinterGateway(ABC):
    @abstractmethod
    def get_status(self) -> PrinterStatus: ...

    @abstractmethod
    def get_page_counter(self) -> int | None:
        """Contador total de impressões (SNMP prtMarkerLifeCount). None se indisponível."""

    @abstractmethod
    def submit(self, request: PrintRequest) -> str:
        """Envia o documento e devolve o id do trabalho na impressora."""

    @abstractmethod
    def get_job_state(self, printer_job_id: str) -> JobProgress: ...

    def cancel(self, printer_job_id: str) -> None:  # pragma: no cover - opcional
        return None
