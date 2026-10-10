"""Digitalização (para cópias): interface, cliente eSCL e digitalizador simulado."""

import io
import itertools
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

import httpx
from PIL import Image, ImageDraw


class ScanSource(StrEnum):
    ADF = "adf"  # alimentador automático
    PLATEN = "platen"  # vidro


class ScannerUnavailable(Exception):
    """Digitalizador inalcançável, ocupado ou sem papel no alimentador."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class ScannerStatus:
    ready: bool
    adf_loaded: bool
    has_adf: bool = True
    adf_duplex: bool = True
    state: str = "Idle"

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "adf_loaded": self.adf_loaded,
            "has_adf": self.has_adf,
            "adf_duplex": self.adf_duplex,
            "state": self.state,
        }


class ScannerGateway(ABC):
    @abstractmethod
    def status(self) -> ScannerStatus: ...

    @abstractmethod
    def scan(self, *, source: ScanSource, duplex: bool, color: bool, dpi: int) -> bytes:
        """Digitaliza e devolve um PDF (todas as folhas do alimentador, ou 1 do vidro)."""


# ------------------------------------------------------------------ eSCL (HP)

ESCL_NS = (
    'xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" '
    'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"'
)


def scan_settings_xml(*, source: ScanSource, duplex: bool, color: bool, dpi: int) -> str:
    feeder = source == ScanSource.ADF
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<scan:ScanSettings {ESCL_NS}>"
        "<pwg:Version>2.6</pwg:Version>"
        "<pwg:ScanRegions><pwg:ScanRegion>"
        "<pwg:Height>3508</pwg:Height><pwg:Width>2480</pwg:Width>"
        "<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>"
        "</pwg:ScanRegion></pwg:ScanRegions>"
        f"<pwg:InputSource>{'Feeder' if feeder else 'Platen'}</pwg:InputSource>"
        + ("<scan:Duplex>true</scan:Duplex>" if feeder and duplex else "")
        + f"<scan:ColorMode>{'RGB24' if color else 'Grayscale8'}</scan:ColorMode>"
        f"<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>"
        "<pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>"
        "</scan:ScanSettings>"
    )


def _tag(xml: str, name: str) -> str | None:
    match = re.search(rf"<[^>]*:{name}>([^<]+)<", xml)
    return match.group(1) if match else None


def images_to_pdf(images: list[bytes]) -> bytes:
    pages = [Image.open(io.BytesIO(raw)).convert("RGB") for raw in images]
    out = io.BytesIO()
    pages[0].save(out, "PDF", resolution=200, save_all=True, append_images=pages[1:])
    return out.getvalue()


class EsclScannerGateway(ScannerGateway):
    """eSCL/AirScan por HTTP.

    A HP recusa (403) pedidos cujo cabeçalho Host não seja o IP da própria
    impressora; como chegamos pela VPN (10.77.N.2), enviamos ``host_header``
    com o IP dela na rede local.
    """

    def __init__(
        self,
        *,
        address: str,
        host_header: str | None = None,
        timeout: float = 30.0,
        scan_timeout: float = 180.0,
        poll_seconds: float = 2.0,
        transport=None,
    ):
        self.base = f"http://{address}"
        self.headers = {"Host": host_header} if host_header else {}
        self.timeout = timeout
        self.scan_timeout = scan_timeout
        self.poll_seconds = poll_seconds
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout, headers=self.headers, transport=self.transport, verify=False
        )

    def status(self) -> ScannerStatus:
        try:
            with self._client() as client:
                status = client.get(self.base + "/eSCL/ScannerStatus")
                caps = client.get(self.base + "/eSCL/ScannerCapabilities")
        except httpx.HTTPError as exc:
            raise ScannerUnavailable("scanner_offline", str(exc)) from exc
        if status.status_code != 200:
            raise ScannerUnavailable("scanner_offline", f"HTTP {status.status_code}")
        state = _tag(status.text, "State") or "Unknown"
        adf_state = _tag(status.text, "AdfState") or ""
        return ScannerStatus(
            ready=state == "Idle",
            adf_loaded=adf_state == "ScannerAdfLoaded",
            has_adf="<scan:Adf>" in caps.text,
            adf_duplex="AdfDuplexInputCaps" in caps.text,
            state=state,
        )

    def scan(self, *, source: ScanSource, duplex: bool, color: bool, dpi: int) -> bytes:
        xml = scan_settings_xml(source=source, duplex=duplex, color=color, dpi=dpi)
        try:
            with self._client() as client:
                created = client.post(
                    self.base + "/eSCL/ScanJobs",
                    content=xml,
                    headers={"Content-Type": "text/xml"},
                )
                if created.status_code == 409:
                    raise ScannerUnavailable("adf_empty" if source == ScanSource.ADF else "busy")
                if created.status_code == 503:
                    raise ScannerUnavailable("scanner_busy")
                if created.status_code != 201 or "location" not in created.headers:
                    raise ScannerUnavailable("scan_failed", f"HTTP {created.status_code}")
                job = re.sub(r"^https?://[^/]+", "", created.headers["location"])
                return self._collect(client, job)
        except httpx.HTTPError as exc:
            raise ScannerUnavailable("scanner_offline", str(exc)) from exc

    def _collect(self, client: httpx.Client, job: str) -> bytes:
        """Vai buscando NextDocument até não haver mais (404). 503 = ainda a digitalizar."""
        documents: list[tuple[str, bytes]] = []
        deadline = time.monotonic() + self.scan_timeout
        while time.monotonic() < deadline:
            response = client.get(self.base + job + "/NextDocument")
            if response.status_code == 200:
                documents.append((response.headers.get("content-type", ""), response.content))
                continue
            if response.status_code == 404:
                break
            time.sleep(self.poll_seconds)
        else:
            raise ScannerUnavailable("scan_timeout")
        if not documents:
            raise ScannerUnavailable("scan_empty")
        if all("pdf" in ctype for ctype, _ in documents):
            if len(documents) == 1:
                return documents[0][1]
            from pypdf import PdfReader, PdfWriter

            writer = PdfWriter()
            for _, data in documents:
                for page in PdfReader(io.BytesIO(data)).pages:
                    writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            return out.getvalue()
        return images_to_pdf([data for _, data in documents])


# ------------------------------------------------------------------ simulado


class FakeScannerGateway(ScannerGateway):
    """Digitalizador simulado: cada digitalização devolve ``pages`` folhas (ADF) ou 1 (vidro)."""

    _state: ClassVar[dict[str, dict]] = {}
    _ids = itertools.count(1)

    def __init__(self, key: str = "default"):
        self.key = str(key)
        self.state = self._state.setdefault(self.key, self._fresh())

    @staticmethod
    def _fresh() -> dict:
        return {"adf_loaded": True, "pages": 2, "offline": False, "busy": False, "scans": []}

    @classmethod
    def configure(cls, key, **values):
        cls._state.setdefault(str(key), cls._fresh()).update(values)

    @classmethod
    def reset(cls):
        cls._state.clear()

    def status(self) -> ScannerStatus:
        if self.state["offline"]:
            raise ScannerUnavailable("scanner_offline")
        return ScannerStatus(
            ready=not self.state["busy"],
            adf_loaded=self.state["adf_loaded"],
            state="Processing" if self.state["busy"] else "Idle",
        )

    def scan(self, *, source: ScanSource, duplex: bool, color: bool, dpi: int) -> bytes:
        self.status()
        if self.state["busy"]:
            raise ScannerUnavailable("scanner_busy")
        if source == ScanSource.ADF and not self.state["adf_loaded"]:
            raise ScannerUnavailable("adf_empty")
        count = (self.state["pages"] * (2 if duplex else 1)) if source == ScanSource.ADF else 1
        n = next(self._ids)
        self.state["scans"].append({"source": source.value, "duplex": duplex, "pages": count})
        pages = []
        for i in range(count):
            img = Image.new("RGB", (595, 842), "white")
            fill = (200, 30, 30) if color and i % 2 else "black"
            ImageDraw.Draw(img).rectangle((60, 60 + 10 * (n % 5), 400, 300), fill=fill)
            pages.append(img)
        out = io.BytesIO()
        pages[0].save(out, "PDF", resolution=72, save_all=True, append_images=pages[1:])
        if source == ScanSource.ADF:
            self.state["adf_loaded"] = False  # o alimentador fica vazio depois de digitalizar
        return out.getvalue()
