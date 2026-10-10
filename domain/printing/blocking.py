"""Quando é que um kiosk deixa de atender clientes por causa da impressora.

Uma só regra para o ecrã do kiosk (bloquear e explicar) e para a impressão
(não aceitar trabalhos). Avisos leves (toner baixo, papel a acabar) não bloqueiam.
"""

# Ordem = prioridade da mensagem mostrada ao cliente.
REASON_RULES = [
    ("paper_jam", ("media-jam", "jam")),
    ("door_open", ("door-open", "cover-open", "interlock-open")),
    ("paper_out", ("media-empty", "media-needed", "input-tray-missing")),
    ("toner_empty", ("toner-empty", "marker-supply-empty", "marker-waste-full")),
]

BLOCKING_STATES = {"ERROR", "OFFLINE", "UNKNOWN"}


def block_reason(status: str, detail: dict | None) -> str | None:
    """Código do motivo de bloqueio, ou None se a impressora pode atender.

    Códigos: offline, paper_jam, door_open, paper_out, toner_empty, error.
    """
    detail = detail or {}
    reasons = [str(r) for r in detail.get("reasons", [])]
    for code, keywords in REASON_RULES:
        hits = [r for r in reasons if any(k in r for k in keywords)]
        # "-report"/"-warning" só bloqueiam no caso do papel (a HP indica falta de papel
        # como aviso enquanto está parada); os outros só bloqueiam como erro.
        if any(r.endswith("-error") for r in hits) or (code == "paper_out" and hits):
            return code
    if detail.get("paper_out"):
        return "paper_out"
    if status in ("OFFLINE", "UNKNOWN"):
        return "offline"
    if status in BLOCKING_STATES or any(r.endswith("-error") for r in reasons):
        return "error"
    return None
