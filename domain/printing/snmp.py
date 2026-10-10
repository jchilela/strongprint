"""SNMP v2c GET mínimo (BER) para ler o contador de páginas, sem dependências."""

import random
import socket

PAGE_COUNTER_OID = "1.3.6.1.2.1.43.10.2.1.4.1.1"  # Printer-MIB prtMarkerLifeCount

_INTEGER, _OCTET_STRING, _NULL, _OID, _SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
_GET_REQUEST, _GET_RESPONSE = 0xA0, 0xA2
_NUMERIC_TYPES = {0x02, 0x41, 0x42, 0x43, 0x46}  # Integer, Counter32, Gauge32, TimeTicks, Counter64


class SnmpError(Exception):
    pass


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(out)]) + out


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _len(len(value)) + value


def _int(value: int, tag: int = _INTEGER) -> bytes:
    size = max(1, (value.bit_length() + 8) // 8)
    return _tlv(tag, value.to_bytes(size, "big", signed=True))


def _oid(oid: str) -> bytes:
    parts = [int(p) for p in oid.strip(".").split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7F))
            part >>= 7
        out.extend(reversed(chunk))
    return _tlv(_OID, bytes(out))


def encode_get(community: str, oid: str, request_id: int) -> bytes:
    varbind = _tlv(_SEQUENCE, _oid(oid) + _tlv(_NULL, b""))
    pdu = _tlv(_GET_REQUEST, _int(request_id) + _int(0) + _int(0) + _tlv(_SEQUENCE, varbind))
    return _tlv(_SEQUENCE, _int(1) + _tlv(_OCTET_STRING, community.encode()) + pdu)


def encode_response(community: str, oid: str, request_id: int, value: int, tag: int = 0x41):
    """Usado nos testes para simular a impressora."""
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    varbind = _tlv(_SEQUENCE, _oid(oid) + _tlv(tag, raw))
    pdu = _tlv(_GET_RESPONSE, _int(request_id) + _int(0) + _int(0) + _tlv(_SEQUENCE, varbind))
    return _tlv(_SEQUENCE, _int(1) + _tlv(_OCTET_STRING, community.encode()) + pdu)


def _read(data: bytes, pos: int) -> tuple[int, bytes, int]:
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[pos : pos + n], "big")
        pos += n
    return tag, data[pos : pos + length], pos + length


def decode_get_response(data: bytes, request_id: int) -> int:
    try:
        _, message, _ = _read(data, 0)
        pos = 0
        for _ in range(2):  # versão, community
            _, _, pos = _read(message, pos)
        tag, pdu, _ = _read(message, pos)
        if tag != _GET_RESPONSE:
            raise SnmpError("Not a GetResponse")
        _, rid, pos = _read(pdu, 0)
        _, err, pos = _read(pdu, pos)
        _, _, pos = _read(pdu, pos)
        _, varbinds, _ = _read(pdu, pos)
        _, varbind, _ = _read(varbinds, 0)
        _, _, vpos = _read(varbind, 0)
        value_tag, value, _ = _read(varbind, vpos)
    except (IndexError, ValueError) as exc:
        raise SnmpError("Malformed SNMP response") from exc
    if int.from_bytes(rid, "big", signed=True) != request_id:
        raise SnmpError("Request id mismatch")
    if int.from_bytes(err, "big"):
        raise SnmpError(f"SNMP error-status {int.from_bytes(err, 'big')}")
    if value_tag not in _NUMERIC_TYPES:
        raise SnmpError(f"Unexpected value type 0x{value_tag:02x}")
    return int.from_bytes(value, "big", signed=value_tag == _INTEGER)


def snmp_get_int(
    host: str,
    oid: str = PAGE_COUNTER_OID,
    *,
    community: str = "public",
    port: int = 161,
    timeout: float = 2.0,
    retries: int = 2,
) -> int:
    request_id = random.randint(1, 2**31 - 1)
    packet = encode_get(community, oid, request_id)
    last: Exception | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(retries + 1):
            try:
                sock.sendto(packet, (host, port))
                data, _ = sock.recvfrom(4096)
                return decode_get_response(data, request_id)
            except (TimeoutError, OSError) as exc:
                last = exc
    raise SnmpError(f"No SNMP answer from {host}: {last}")
