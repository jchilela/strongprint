import pytest

from domain.printing.blocking import block_reason
from domain.printing.ipp_gateway import map_status


@pytest.mark.parametrize(
    "status, detail, expected",
    [
        ("READY", {"reasons": []}, None),
        ("WARNING", {"reasons": ["toner-low-warning", "media-low-report"]}, None),
        ("READY", {"reasons": ["media-empty-warning"]}, "paper_out"),
        ("ERROR", {"reasons": ["media-needed-error"]}, "paper_out"),
        ("READY", {"paper_out": True}, "paper_out"),
        ("ERROR", {"reasons": ["media-jam-error", "media-empty-error"]}, "paper_jam"),
        ("ERROR", {"reasons": ["door-open-error"]}, "door_open"),
        ("ERROR", {"reasons": ["toner-empty-error"]}, "toner_empty"),
        ("READY", {"reasons": ["toner-empty-warning"]}, None),
        ("OFFLINE", {}, "offline"),
        ("UNKNOWN", None, "offline"),
        ("ERROR", {"reasons": ["other-error"]}, "error"),
        ("READY", {"reasons": ["spool-area-full-error"]}, "error"),
    ],
)
def test_block_reason(status, detail, expected):
    assert block_reason(status, detail) == expected


def test_map_status_paper_out_cannot_print():
    status = map_status(
        {"printer-state": [3], "printer-state-reasons": ["media-empty-warning"]}
    )
    assert status.paper_out and not status.can_print
    assert block_reason(status.state.value, status.to_dict()) == "paper_out"


def test_map_status_ready():
    status = map_status({"printer-state": [3], "printer-state-reasons": ["none"]})
    assert status.can_print and block_reason(status.state.value, status.to_dict()) is None


def test_busy_spool_full_is_not_an_error():
    """A HP, enquanto imprime: spool-area-full-report e não aceita trabalhos."""
    status = map_status(
        {
            "printer-state": [4],
            "printer-state-reasons": ["spool-area-full-report"],
            "printer-is-accepting-jobs": [False],
        }
    )
    assert status.state.value == "BUSY" and not status.can_print
    assert block_reason(status.state.value, status.to_dict()) is None


def test_not_accepting_with_error_is_error():
    status = map_status(
        {
            "printer-state": [5],
            "printer-state-reasons": ["other-error"],
            "printer-is-accepting-jobs": [False],
        }
    )
    assert block_reason(status.state.value, status.to_dict()) == "error"
