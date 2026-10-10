import pytest

from domain.jobs.state_machine import (
    EDITABLE_STATES,
    FUNDS_HELD_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    InvalidTransition,
    JobState,
    can_transition,
    is_terminal,
    validate_transition,
)

S = JobState

HAPPY_PATH = [
    S.CREATED,
    S.FILE_RECEIVED,
    S.ANALYZED,
    S.RESERVED,
    S.QUEUED,
    S.PRINTING,
    S.COMPLETED,
]


def test_every_state_has_a_transition_entry():
    assert set(TRANSITIONS) == set(JobState)


def test_happy_path_is_valid():
    for current, target in zip(HAPPY_PATH, HAPPY_PATH[1:], strict=False):
        validate_transition(current, target)


@pytest.mark.parametrize("outcome", [S.COMPLETED, S.PARTIAL, S.FAILED])
def test_printing_outcomes(outcome):
    assert can_transition(S.PRINTING, outcome)


def test_terminal_states():
    assert TERMINAL_STATES == {
        S.COMPLETED,
        S.PARTIAL,
        S.FAILED,
        S.REJECTED,
        S.CANCELLED,
        S.EXPIRED,
    }
    for state in TERMINAL_STATES:
        assert is_terminal(state)
        for target in JobState:
            assert not can_transition(state, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (S.CREATED, S.PRINTING),
        (S.ANALYZED, S.QUEUED),  # não pode imprimir sem cativar fundos
        (S.RESERVED, S.EXPIRED),  # com fundos cativos não expira; cancela-se e liberta
        (S.PRINTING, S.CANCELLED),  # já está a imprimir
        (S.QUEUED, S.COMPLETED),
        (S.COMPLETED, S.FAILED),
    ],
)
def test_invalid_transitions_raise(current, target):
    with pytest.raises(InvalidTransition) as exc:
        validate_transition(current, target)
    assert exc.value.current == current
    assert exc.value.target == target


def test_unknown_state_raises_invalid_transition():
    with pytest.raises(InvalidTransition):
        validate_transition("CREATED", "BOGUS")


def test_only_pre_reservation_states_expire():
    expirable = {s for s in JobState if can_transition(s, S.EXPIRED)}
    assert expirable == {S.CREATED, S.FILE_RECEIVED, S.ANALYZED}


def test_funds_held_states_can_all_reach_a_terminal_state():
    for state in FUNDS_HELD_STATES:
        assert TRANSITIONS[state] & (TERMINAL_STATES | FUNDS_HELD_STATES)


def test_editable_states():
    assert EDITABLE_STATES == {S.ANALYZED}
