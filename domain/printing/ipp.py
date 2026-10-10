"""Cliente IPP mínimo (RFC 8010/8011) sobre HTTP, sem CUPS.

Operações usadas: Print-Job, Get-Job-Attributes, Get-Printer-Attributes, Cancel-Job.
"""

import itertools
import struct
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

# Operações
PRINT_JOB = 0x0002
CANCEL_JOB = 0x0008
GET_JOB_ATTRIBUTES = 0x0009
GET_PRINTER_ATTRIBUTES = 0x000B

# Delimitadores de grupo
OPERATION_ATTRIBUTES = 0x01
JOB_ATTRIBUTES = 0x02
END_OF_ATTRIBUTES = 0x03
PRINTER_ATTRIBUTES = 0x04
UNSUPPORTED_ATTRIBUTES = 0x05

# Tipos de valor
INTEGER = 0x21
BOOLEAN = 0x22
ENUM = 0x23
OCTET_STRING = 0x30
DATETIME = 0x31
RESOLUTION = 0x32
RANGE_OF_INTEGER = 0x33
BEG_COLLECTION = 0x34
END_COLLECTION = 0x37
TEXT = 0x41
NAME = 0x42
KEYWORD = 0x44
URI = 0x45
CHARSET = 0x47
NATURAL_LANGUAGE = 0x48
MIME_MEDIA_TYPE = 0x49
MEMBER_ATTR_NAME = 0x4A

GROUP_NAMES = {
    OPERATION_ATTRIBUTES: "operation",
    JOB_ATTRIBUTES: "job",
    PRINTER_ATTRIBUTES: "printer",
    UNSUPPORTED_ATTRIBUTES: "unsupported",
}

_request_ids = itertools.count(1)


class IppError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


@dataclass
class IppResponse:
    status_code: int
    request_id: int
    groups: dict[str, list[dict[str, list]]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status_code < 0x0100

    def first(self, group: str) -> dict[str, list]:
        return (self.groups.get(group) or [{}])[0]

    def get(self, group: str, name: str, default=None):
        values = self.first(group).get(name)
        return values[0] if values else default

    def get_all(self, group: str, name: str) -> list:
        return self.first(group).get(name, [])


# --- codificação ------------------------------------------------------------


def _attr(tag: int, name: str, value) -> bytes:
    name_b = name.encode()
    if tag in (INTEGER, ENUM):
        value_b = struct.pack(">i", int(value))
    elif tag == BOOLEAN:
        value_b = b"\x01" if value else b"\x00"
    else:
        value_b = str(value).encode()
    return struct.pack(">BH", tag, len(name_b)) + name_b + struct.pack(">H", len(value_b)) + value_b


def _attr_set(tag: int, name: str, values: list) -> bytes:
    out = _attr(tag, name, values[0])
    for value in values[1:]:
        out += _attr(tag, "", value)  # valores adicionais: nome vazio
    return out


def encode_request(
    operation: int,
    operation_attrs: list[tuple[int, str, object]],
    job_attrs: list[tuple[int, str, object]] | None = None,
    data: bytes = b"",
    request_id: int | None = None,
) -> bytes:
    request_id = request_id or next(_request_ids)
    out = struct.pack(">BBHI", 2, 0, operation, request_id)
    out += bytes([OPERATION_ATTRIBUTES])
    out += _attr(CHARSET, "attributes-charset", "utf-8")
    out += _attr(NATURAL_LANGUAGE, "attributes-natural-language", "en")
    for tag, name, value in operation_attrs:
        out += _attr_set(tag, name, value if isinstance(value, list) else [value])
    if job_attrs:
        out += bytes([JOB_ATTRIBUTES])
        for tag, name, value in job_attrs:
            out += _attr_set(tag, name, value if isinstance(value, list) else [value])
    out += bytes([END_OF_ATTRIBUTES])
    return out + data


# --- descodificação ---------------------------------------------------------


def _decode_value(tag: int, raw: bytes):
    if tag in (INTEGER, ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN and len(raw) == 1:
        return raw != b"\x00"
    if tag == RANGE_OF_INTEGER and len(raw) == 8:
        return struct.unpack(">ii", raw)
    if tag == RESOLUTION and len(raw) == 9:
        return struct.unpack(">iib", raw)
    if tag in (OCTET_STRING, DATETIME) or tag < 0x20:
        return raw if tag >= 0x20 else None
    return raw.decode("utf-8", errors="replace")


def decode_response(payload: bytes) -> IppResponse:
    if len(payload) < 9:
        raise IppError("IPP response too short")
    _, _, status_code, request_id = struct.unpack(">BBHI", payload[:8])
    response = IppResponse(status_code=status_code, request_id=request_id)
    pos = 8
    current: dict[str, list] | None = None
    last_name = ""
    depth = 0  # coleções são ignoradas (não precisamos delas)
    while pos < len(payload):
        tag = payload[pos]
        pos += 1
        if tag == END_OF_ATTRIBUTES:
            break
        if tag < 0x10:  # novo grupo
            current = {}
            response.groups.setdefault(GROUP_NAMES.get(tag, str(tag)), []).append(current)
            continue
        if pos + 2 > len(payload):
            raise IppError("Truncated IPP attribute")
        (name_len,) = struct.unpack(">H", payload[pos : pos + 2])
        pos += 2
        name = payload[pos : pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        (value_len,) = struct.unpack(">H", payload[pos : pos + 2])
        pos += 2
        raw = payload[pos : pos + value_len]
        pos += value_len

        if tag == BEG_COLLECTION:
            depth += 1
            if depth == 1 and name:
                last_name = name
            continue
        if tag == END_COLLECTION:
            depth = max(0, depth - 1)
            continue
        if depth or current is None:
            continue
        if name:
            last_name = name
            current[name] = []
        value = _decode_value(tag, raw)
        if value is not None:
            current.setdefault(last_name, []).append(value)
    return response


# --- transporte ---------------------------------------------------------------


def http_url(printer_uri: str) -> str:
    parts = urlsplit(printer_uri)
    scheme = {"ipp": "http", "ipps": "https"}.get(parts.scheme, parts.scheme)
    netloc = parts.netloc if parts.port else f"{parts.hostname}:631"
    return urlunsplit((scheme, netloc, parts.path or "/ipp/print", "", ""))


class IppClient:
    def __init__(self, printer_uri: str, *, timeout: float = 10.0, transport=None):
        self.printer_uri = printer_uri
        self.url = http_url(printer_uri)
        self.timeout = timeout
        self.transport = transport

    def call(
        self, operation: int, operation_attrs, job_attrs=None, data: bytes = b""
    ) -> IppResponse:
        body = encode_request(
            operation,
            [(URI, "printer-uri", self.printer_uri), *operation_attrs],
            job_attrs,
            data,
        )
        try:
            # Impressoras usam certificados autoassinados; o tráfego vai pela VPN.
            with httpx.Client(timeout=self.timeout, verify=False, transport=self.transport) as c:
                http = c.post(self.url, content=body, headers={"Content-Type": "application/ipp"})
        except httpx.HTTPError as exc:
            raise IppError(f"Printer unreachable: {exc}") from exc
        if http.status_code != 200:
            raise IppError(f"HTTP {http.status_code} from printer")
        response = decode_response(http.content)
        if not response.ok:
            raise IppError(f"IPP status 0x{response.status_code:04x}", response.status_code)
        return response
