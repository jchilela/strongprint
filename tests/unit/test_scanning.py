import io

import httpx
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from domain.scanning.gateway import (
    EsclScannerGateway,
    FakeScannerGateway,
    ScannerUnavailable,
    ScanSource,
    images_to_pdf,
    scan_settings_xml,
)

STATUS = (
    '<scan:ScannerStatus xmlns:scan="x" xmlns:pwg="y"><pwg:State>Idle</pwg:State>'
    "<scan:AdfState>ScannerAdfLoaded</scan:AdfState></scan:ScannerStatus>"
)
CAPS = "<scan:ScannerCapabilities><scan:Platen/><scan:Adf><scan:AdfDuplexInputCaps/></scan:Adf>"


def pdf_bytes(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_scan_settings_xml():
    xml = scan_settings_xml(source=ScanSource.ADF, duplex=True, color=False, dpi=200)
    assert "<pwg:InputSource>Feeder</pwg:InputSource>" in xml
    assert "<scan:Duplex>true</scan:Duplex>" in xml and "Grayscale8" in xml
    platen = scan_settings_xml(source=ScanSource.PLATEN, duplex=True, color=True, dpi=300)
    assert "Platen" in platen and "Duplex" not in platen and "RGB24" in platen


def escl(handler, **kw):
    return EsclScannerGateway(
        address="10.77.1.2",
        host_header="192.168.1.59",
        poll_seconds=0,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def test_status_sends_printer_host_header():
    seen = []

    def handler(request):
        seen.append(request.headers["host"])
        body = STATUS if request.url.path.endswith("Status") else CAPS
        return httpx.Response(200, text=body)

    status = escl(handler).status()
    assert (status.ready, status.adf_loaded, status.has_adf, status.adf_duplex) == (
        True,
        True,
        True,
        True,
    )
    assert set(seen) == {"192.168.1.59"}
    assert status.to_dict()["state"] == "Idle"


def test_status_forbidden_or_offline():
    with pytest.raises(ScannerUnavailable):
        escl(lambda r: httpx.Response(403)).status()

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ScannerUnavailable) as exc:
        escl(boom).status()
    assert exc.value.code == "scanner_offline"


def test_scan_collects_documents_until_404():
    calls = {"next": 0}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                201, headers={"Location": "http://192.168.1.59/eSCL/ScanJobs/abc"}
            )
        calls["next"] += 1
        if calls["next"] == 1:
            return httpx.Response(503)  # ainda a digitalizar
        if calls["next"] in (2, 3):
            return httpx.Response(
                200, content=pdf_bytes(1), headers={"Content-Type": "application/pdf"}
            )
        return httpx.Response(404)

    pdf = escl(handler).scan(source=ScanSource.ADF, duplex=False, color=True, dpi=200)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 2


def test_scan_jpeg_documents_become_pdf():
    img = io.BytesIO()
    Image.new("RGB", (50, 70), "white").save(img, "JPEG")
    state = {"n": 0}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/eSCL/ScanJobs/x"})
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(
                200, content=img.getvalue(), headers={"Content-Type": "image/jpeg"}
            )
        return httpx.Response(404)

    pdf = escl(handler).scan(source=ScanSource.PLATEN, duplex=False, color=True, dpi=200)
    assert pdf.startswith(b"%PDF")
    assert images_to_pdf([img.getvalue()]).startswith(b"%PDF")


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(409), "adf_empty"),
        (httpx.Response(503), "scanner_busy"),
        (httpx.Response(500), "scan_failed"),
    ],
)
def test_scan_start_errors(response, code):
    with pytest.raises(ScannerUnavailable) as exc:
        escl(lambda r: response).scan(source=ScanSource.ADF, duplex=False, color=True, dpi=200)
    assert exc.value.code == code


def test_scan_empty_and_timeout():
    def empty(request):
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/eSCL/ScanJobs/x"})
        return httpx.Response(404)

    with pytest.raises(ScannerUnavailable) as exc:
        escl(empty).scan(source=ScanSource.ADF, duplex=False, color=True, dpi=200)
    assert exc.value.code == "scan_empty"

    def busy(request):
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/eSCL/ScanJobs/x"})
        return httpx.Response(503)

    with pytest.raises(ScannerUnavailable) as exc:
        escl(busy, scan_timeout=0).scan(source=ScanSource.ADF, duplex=False, color=True, dpi=200)
    assert exc.value.code == "scan_timeout"


def test_fake_scanner():
    FakeScannerGateway.reset()
    fake = FakeScannerGateway("k")
    assert fake.status().adf_loaded
    pdf = fake.scan(source=ScanSource.ADF, duplex=True, color=True, dpi=200)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 4
    with pytest.raises(ScannerUnavailable) as exc:
        fake.scan(source=ScanSource.ADF, duplex=False, color=True, dpi=200)
    assert exc.value.code == "adf_empty"
    assert (
        len(
            PdfReader(
                io.BytesIO(fake.scan(source=ScanSource.PLATEN, duplex=False, color=False, dpi=200))
            ).pages
        )
        == 1
    )
    FakeScannerGateway.configure("k", offline=True)
    with pytest.raises(ScannerUnavailable):
        fake.status()
    FakeScannerGateway.configure("k", offline=False, busy=True)
    with pytest.raises(ScannerUnavailable):
        fake.scan(source=ScanSource.PLATEN, duplex=False, color=False, dpi=200)
    FakeScannerGateway.reset()
