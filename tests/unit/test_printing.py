import io
import socket
import struct
import threading

import httpx
import pytest
from PIL import Image

from domain.jobs.state_machine import JobState
from domain.printing import ipp, snmp
from domain.printing.fake import FakePrinterGateway
from domain.printing.gateway import (
    JobOutcome,
    PrinterJobLost,
    PrinterState,
    PrinterUnavailable,
    PrintRequest,
)
from domain.printing.ipp_gateway import IppPrinterGateway, map_status
from domain.printing.verification import decide

# --- IPP ----------------------------------------------------------------------


def ipp_response(status: int, groups: list[tuple[int, list[tuple[int, str, list]]]]) -> bytes:
    out = struct.pack(">BBHI", 2, 0, status, 7)
    for group_tag, attrs in groups:
        out += bytes([group_tag])
        for tag, name, values in attrs:
            out += ipp._attr_set(tag, name, values)
    return out + bytes([ipp.END_OF_ATTRIBUTES])


PRINTER_OK = ipp_response(
    0,
    [
        (ipp.OPERATION_ATTRIBUTES, [(ipp.CHARSET, "attributes-charset", ["utf-8"])]),
        (
            ipp.PRINTER_ATTRIBUTES,
            [
                (ipp.ENUM, "printer-state", [3]),
                (ipp.KEYWORD, "printer-state-reasons", ["none"]),
                (ipp.BOOLEAN, "printer-is-accepting-jobs", [True]),
                (ipp.NAME, "marker-names", ["Black", "Cyan", "Magenta", "Yellow"]),
                (ipp.INTEGER, "marker-levels", [55, 8, 40, -3]),
                (ipp.MIME_MEDIA_TYPE, "document-format-supported", ["application/pdf"]),
            ],
        ),
    ],
)


def test_encode_request_layout():
    body = ipp.encode_request(
        ipp.PRINT_JOB,
        [(ipp.URI, "printer-uri", "ipp://p/ipp/print")],
        [(ipp.INTEGER, "copies", 2)],
        data=b"%PDF",
        request_id=42,
    )
    assert body[:8] == struct.pack(">BBHI", 2, 0, ipp.PRINT_JOB, 42)
    assert b"attributes-charset" in body and b"copies" in body
    assert body.endswith(bytes([ipp.END_OF_ATTRIBUTES]) + b"%PDF")


def test_decode_response_with_sets_and_collections():
    raw = PRINTER_OK[:-1] + ipp._attr(ipp.BEG_COLLECTION, "media-col", "")
    raw += ipp._attr(ipp.MEMBER_ATTR_NAME, "", "media-size") + ipp._attr(ipp.END_COLLECTION, "", "")
    raw += struct.pack(">BH", ipp.RANGE_OF_INTEGER, 16) + b"copies-supported"
    raw += struct.pack(">Hii", 8, 1, 99)
    raw += bytes([ipp.END_OF_ATTRIBUTES])
    response = ipp.decode_response(raw)
    assert response.ok
    assert response.get("printer", "printer-state") == 3
    assert response.get_all("printer", "marker-levels") == [55, 8, 40, -3]
    assert "media-col" not in response.first("printer")
    assert response.get("printer", "copies-supported") == (1, 99)
    with pytest.raises(ipp.IppError):
        ipp.decode_response(b"\x02\x00")


@pytest.mark.parametrize(
    "uri,url",
    [
        ("ipp://10.0.0.5/ipp/print", "http://10.0.0.5:631/ipp/print"),
        ("ipps://10.0.0.5:443/ipp/print", "https://10.0.0.5:443/ipp/print"),
        ("ipp://printer", "http://printer:631/ipp/print"),
    ],
)
def test_http_url(uri, url):
    assert ipp.http_url(uri) == url


def test_map_status_variants():
    ok = ipp.decode_response(PRINTER_OK).first("printer")
    status = map_status(ok)
    assert (status.state, status.can_print, status.paper_out) == (PrinterState.READY, True, False)
    assert status.toner == {"Black": 55, "Cyan": 8, "Magenta": 40, "Yellow": -3}
    assert status.low_toner == {"Cyan": 8}

    paper = map_status({"printer-state": [5], "printer-state-reasons": ["media-empty-error"]})
    assert (paper.state, paper.paper_out, paper.can_print) == (PrinterState.ERROR, True, False)
    warn = map_status({"printer-state": [3], "printer-state-reasons": ["toner-low-warning"]})
    assert (warn.state, warn.can_print) == (PrinterState.WARNING, True)
    busy = map_status({"printer-state": [4], "printer-is-accepting-jobs": [True]})
    assert busy.state == PrinterState.BUSY
    refusing = map_status({"printer-state": [3], "printer-is-accepting-jobs": [False]})
    assert (refusing.state, refusing.can_print) == (PrinterState.BUSY, False)
    no_pdf = map_status({"printer-state": [3], "document-format-supported": ["image/urf"]})
    assert not no_pdf.accepts_pdf


def make_gateway(handler, counter=1234):
    return IppPrinterGateway(
        printer_uri="ipp://10.147.17.20/ipp/print",
        host="10.147.17.20",
        transport=httpx.MockTransport(handler),
        snmp_get=lambda host, oid, community: counter,
    )


def test_ipp_gateway_print_and_poll():
    seen = []

    def handler(request):
        operation = struct.unpack(">H", request.content[2:4])[0]
        seen.append(operation)
        if operation == ipp.GET_PRINTER_ATTRIBUTES:
            return httpx.Response(200, content=PRINTER_OK)
        if operation == ipp.PRINT_JOB:
            assert request.content.endswith(b"%PDF-data")
            assert b"two-sided-long-edge" in request.content and b"monochrome" in request.content
            body = ipp_response(0, [(ipp.JOB_ATTRIBUTES, [(ipp.INTEGER, "job-id", [77])])])
            return httpx.Response(200, content=body)
        if operation == ipp.GET_JOB_ATTRIBUTES:
            body = ipp_response(
                0,
                [
                    (
                        ipp.JOB_ATTRIBUTES,
                        [
                            (ipp.ENUM, "job-state", [9]),
                            (ipp.INTEGER, "job-impressions-completed", [6]),
                            (ipp.KEYWORD, "job-state-reasons", ["job-completed-successfully"]),
                        ],
                    )
                ],
            )
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=ipp_response(0, []))

    gateway = make_gateway(handler)
    job_id = gateway.submit(
        PrintRequest(pdf=b"%PDF-data", job_name="SP-1", color=False, duplex=True, copies=2)
    )
    assert job_id == "77"
    progress = gateway.get_job_state(job_id)
    assert (progress.outcome, progress.impressions) == (JobOutcome.COMPLETED, 6)
    assert progress.reasons == ["job-completed-successfully"]
    assert gateway.get_page_counter() == 1234
    gateway.cancel("77")
    assert seen[:3] == [ipp.GET_PRINTER_ATTRIBUTES, ipp.PRINT_JOB, ipp.GET_JOB_ATTRIBUTES]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, content=ipp_response(0x0400, [])),  # client-error-bad-request
    ],
)
def test_ipp_gateway_errors_become_unavailable(response):
    gateway = make_gateway(lambda request: response)
    with pytest.raises(PrinterUnavailable):
        gateway.get_status()


def test_ipp_gateway_network_error():
    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(PrinterUnavailable):
        make_gateway(boom).get_status()


def test_ipp_gateway_counter_unavailable():
    def failing(*args, **kwargs):
        raise snmp.SnmpError("timeout")

    gateway = make_gateway(lambda r: httpx.Response(200, content=PRINTER_OK))
    gateway.snmp_get = failing
    assert gateway.get_page_counter() is None


# --- SNMP ---------------------------------------------------------------------


def test_snmp_roundtrip_encoding():
    packet = snmp.encode_get("public", snmp.PAGE_COUNTER_OID, 99)
    assert packet[0] == 0x30 and b"public" in packet
    response = snmp.encode_response("public", snmp.PAGE_COUNTER_OID, 99, 123456)
    assert snmp.decode_get_response(response, 99) == 123456
    with pytest.raises(snmp.SnmpError):
        snmp.decode_get_response(response, 100)
    with pytest.raises(snmp.SnmpError):
        snmp.decode_get_response(b"\x30\x01", 1)
    octets = snmp.encode_response("public", snmp.PAGE_COUNTER_OID, 1, 5, tag=0x04)
    with pytest.raises(snmp.SnmpError):
        snmp.decode_get_response(octets, 1)


def test_snmp_get_against_udp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        data, addr = server.recvfrom(4096)
        # o request-id é o 1.º INTEGER dentro do PDU GetRequest
        pdu = data[data.index(b"\xa0") :]
        rid_len = pdu[3]
        rid = int.from_bytes(pdu[4 : 4 + rid_len], "big", signed=True)
        server.sendto(snmp.encode_response("public", snmp.PAGE_COUNTER_OID, rid, 98765), addr)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert snmp.snmp_get_int("127.0.0.1", port=port, timeout=2) == 98765
    finally:
        thread.join(timeout=2)
        server.close()


def test_snmp_timeout():
    with pytest.raises(snmp.SnmpError):
        snmp.snmp_get_int("127.0.0.1", port=9, timeout=0.2, retries=0)


# --- verificação ----------------------------------------------------------------

BREAKDOWN = {
    "pages": [
        {"page": 1, "unit_kz": 100, "coverage_pct": 20},
        {"page": 2, "unit_kz": 500, "coverage_pct": 60},
        {"page": 3, "unit_kz": 100, "coverage_pct": 0},
    ],
    "copies": 2,
    "faces": 6,
    "total_kz": 1400,
}


@pytest.mark.parametrize(
    "before,after,outcome,impressions,state,printed,capture,source",
    [
        (100, 106, JobOutcome.COMPLETED, None, JobState.COMPLETED, 6, 1400, "counter"),
        (100, 104, JobOutcome.ABORTED, None, JobState.PARTIAL, 4, 800, "counter"),
        (100, 100, JobOutcome.ABORTED, None, JobState.FAILED, 0, 0, "counter"),
        # páginas em branco podem não contar: tolerância só com job COMPLETED
        (100, 104, JobOutcome.COMPLETED, None, JobState.COMPLETED, 4, 1400, "counter"),
        (None, None, JobOutcome.COMPLETED, 3, JobState.PARTIAL, 3, 700, "ipp"),
        (None, None, JobOutcome.COMPLETED, None, JobState.COMPLETED, 6, 1400, "ipp"),
        (None, None, JobOutcome.UNKNOWN, None, JobState.FAILED, 0, 0, "none"),
    ],
)
def test_decide(before, after, outcome, impressions, state, printed, capture, source):
    verdict = decide(
        BREAKDOWN,
        counter_before=before,
        counter_after=after,
        outcome=outcome,
        impressions=impressions,
    )
    assert (verdict.state, verdict.faces_printed, verdict.capture_kz, verdict.source) == (
        state,
        printed,
        capture,
        source,
    )


def test_decide_overflow_is_capped():
    verdict = decide(
        BREAKDOWN, counter_before=0, counter_after=9, outcome=JobOutcome.COMPLETED, impressions=None
    )
    assert (verdict.state, verdict.faces_printed, verdict.overflow) == (JobState.COMPLETED, 6, 3)
    assert verdict.to_dict()["overflow"] == 3


# --- impressora simulada --------------------------------------------------------


def tiny_pdf(pages=2) -> bytes:
    imgs = [Image.new("RGB", (100, 100), "white") for _ in range(pages)]
    out = io.BytesIO()
    imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:])
    return out.getvalue()


@pytest.fixture(autouse=True)
def reset_fake():
    FakePrinterGateway.reset()
    yield
    FakePrinterGateway.reset()


@pytest.mark.parametrize(
    "behaviour,extra,outcome,printed",
    [
        ("complete", {}, JobOutcome.COMPLETED, 6),
        ("partial", {"partial_faces": 2}, JobOutcome.ABORTED, 2),
        ("fail", {}, JobOutcome.ABORTED, 0),
        ("stuck", {}, JobOutcome.PROCESSING, 0),
    ],
)
def test_fake_gateway_behaviours(behaviour, extra, outcome, printed):
    FakePrinterGateway.configure("p1", behaviour=behaviour, **extra)
    gateway = FakePrinterGateway("p1")
    before = gateway.get_page_counter()
    job_id = gateway.submit(
        PrintRequest(pdf=tiny_pdf(2), job_name="x", color=True, duplex=False, copies=3)
    )
    progress = gateway.get_job_state(job_id)
    assert progress.outcome == outcome
    assert gateway.get_page_counter() - before == printed
    assert gateway.state["submitted"][0]["pages"] == 2
    with pytest.raises(PrinterJobLost):  # trabalho que a impressora não conhece
        gateway.get_job_state("missing")


def test_fake_gateway_offline_and_status():
    gateway = FakePrinterGateway("p2")
    assert gateway.get_status().can_print
    FakePrinterGateway.configure("p2", state=PrinterState.ERROR, paper_out=True)
    status = gateway.get_status()
    assert (status.can_print, status.paper_out) == (False, True)
    FakePrinterGateway.configure("p2", behaviour="offline")
    with pytest.raises(PrinterUnavailable):
        gateway.get_status()
    assert gateway.get_page_counter() is None


def test_ipp_gateway_job_not_found_is_job_lost():
    """Depois de reiniciar, a HP responde client-error-not-found (0x0406) ao trabalho."""
    import struct

    body = struct.pack(">BBHI", 2, 0, 0x0406, 1) + bytes([0x03])
    gateway = make_gateway(lambda r: httpx.Response(200, content=body))
    with pytest.raises(PrinterJobLost):
        gateway.get_job_state("3")
