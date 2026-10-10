"""Máquina de estados do Job — Python puro, sem Django.

Fluxo com carteira pré-paga:

    CREATED → FILE_RECEIVED → ANALYZED → RESERVED → QUEUED → PRINTING
        → COMPLETED | PARTIAL | FAILED

RESERVED significa que o valor está cativo na carteira do cliente. No fim, só
as faces efetivamente impressas são debitadas e o resto é libertado.
"""

from enum import StrEnum


class JobState(StrEnum):
    CREATED = "CREATED"
    FILE_RECEIVED = "FILE_RECEIVED"
    ANALYZED = "ANALYZED"
    RESERVED = "RESERVED"
    QUEUED = "QUEUED"
    PRINTING = "PRINTING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


S = JobState

TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    S.CREATED: frozenset({S.FILE_RECEIVED, S.CANCELLED, S.EXPIRED}),
    S.FILE_RECEIVED: frozenset({S.ANALYZED, S.REJECTED, S.CANCELLED, S.EXPIRED}),
    S.ANALYZED: frozenset({S.RESERVED, S.CANCELLED, S.EXPIRED}),
    S.RESERVED: frozenset({S.QUEUED, S.CANCELLED}),
    # QUEUED → FAILED: a impressora não ficou pronta dentro do prazo.
    S.QUEUED: frozenset({S.PRINTING, S.FAILED, S.CANCELLED}),
    S.PRINTING: frozenset({S.COMPLETED, S.PARTIAL, S.FAILED}),
    S.COMPLETED: frozenset(),
    S.PARTIAL: frozenset(),
    S.FAILED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
    S.EXPIRED: frozenset(),
}

TERMINAL_STATES = frozenset(s for s, targets in TRANSITIONS.items() if not targets)

# Estados em que existe valor cativo na carteira e que exigem liquidação ao terminar.
FUNDS_HELD_STATES = frozenset({S.RESERVED, S.QUEUED, S.PRINTING})

# Estados em que o cliente ainda pode alterar opções (recalcula o preço).
EDITABLE_STATES = frozenset({S.ANALYZED})


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Invalid job transition {current} → {target}")


def can_transition(current: str, target: str) -> bool:
    return JobState(target) in TRANSITIONS[JobState(current)]


def validate_transition(current: str, target: str) -> None:
    try:
        allowed = can_transition(current, target)
    except ValueError as exc:
        raise InvalidTransition(current, target) from exc
    if not allowed:
        raise InvalidTransition(current, target)


def is_terminal(state: str) -> bool:
    return JobState(state) in TERMINAL_STATES
