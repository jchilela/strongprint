#!/usr/bin/env python3
"""Agente StrongPrint do Raspberry Pi (só biblioteca padrão do Python).

1. Heartbeat a cada 30 s para a API (token do Pi); executa os comandos devolvidos:
   ``reload`` (reinicia o ecrã), ``reboot``.
2. Servidor local em 127.0.0.1:8800 para o ecrã do kiosk ler a pen USB:
   GET /health · GET /files · POST /upload {path, url, authorization}
   Só aceita pedidos vindos do ecrã (CORS) e só envia ficheiros para a API StrongPrint.

Configuração: /etc/strongprint/agent.env (API_URL, DEVICE_TOKEN, SCREEN_URL, VPN_IP).
"""

import json
import mimetypes
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.0.0"
ENV_FILE = Path(os.environ.get("STRONGPRINT_ENV", "/etc/strongprint/agent.env"))
USB_ROOTS = [Path(p) for p in os.environ.get("USB_ROOTS", "/media").split(":")]
EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".ppt",
    ".pptx",
    ".odp",
    ".jpg",
    ".jpeg",
    ".png",
}
MAX_FILES = 300
MAX_DEPTH = 4
MAX_BYTES = 50 * 1024 * 1024
STARTED = time.monotonic()


def load_env(path: Path = ENV_FILE) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


CONFIG = load_env()


def origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def allowed_upload_url(url: str, config: dict) -> bool:
    """Os ficheiros da pen só podem ir para a API StrongPrint (nunca para outro sítio)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.path.startswith("/api/v1/jobs/"):
        return False
    host = parts.hostname or ""
    trusted = {urllib.parse.urlsplit(config.get(k, "")).hostname for k in ("API_URL", "SCREEN_URL")}
    return host in trusted - {None, ""}


# ------------------------------------------------------------------ pen USB


class UsbIndex:
    """Lista os ficheiros imprimíveis das pens montadas. Os caminhos nunca saem do Pi:
    o ecrã recebe identificadores aleatórios."""

    def __init__(self, roots=USB_ROOTS):
        self.roots = roots
        self.ids: dict[str, Path] = {}

    def mounts(self) -> list[Path]:
        found = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                # /media/<utilizador>/<pen> (Raspberry Pi OS) ou /media/<pen>
                candidates = [p for p in entry.iterdir() if p.is_dir()] if entry.is_dir() else []
                for mount in candidates or [entry]:
                    if os.path.ismount(mount) or os.environ.get("USB_ANY_DIR"):
                        found.append(mount)
        return found

    def files(self) -> list[dict]:
        self.ids = {}
        result = []
        for mount in self.mounts():
            for path in self._walk(mount, 0):
                if len(result) >= MAX_FILES:
                    return result
                file_id = secrets.token_urlsafe(9)
                self.ids[file_id] = path
                rel = path.relative_to(mount)
                result.append(
                    {
                        "path": file_id,
                        "name": path.name,
                        "dir": str(rel.parent) if str(rel.parent) != "." else mount.name,
                        "size": path.stat().st_size,
                    }
                )
        return result

    def _walk(self, folder: Path, depth: int):
        if depth > MAX_DEPTH:
            return
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                yield from self._walk(entry, depth + 1)
            elif entry.suffix.lower() in EXTENSIONS and entry.stat().st_size <= MAX_BYTES:
                yield entry

    def resolve(self, file_id: str) -> Path | None:
        return self.ids.get(file_id)


USB = UsbIndex()


def multipart_upload(url: str, authorization: str, path: Path, timeout: int = 120) -> int:
    boundary = "----sp" + secrets.token_hex(12)
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{path.name.replace(chr(34), "")}"\r\nContent-Type: {ctype}\r\n\r\n'
    ).encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": f"strongprint-agent/{VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class Handler(BaseHTTPRequestHandler):
    server_version = f"strongprint-agent/{VERSION}"

    def log_message(self, fmt, *args):  # sem logs por pedido (o ecrã consulta muito)
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin and origin == origin_of(CONFIG.get("SCREEN_URL", "")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Chrome: pedidos de um site público para 127.0.0.1 (Private Network Access).
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == origin_of(CONFIG.get("SCREEN_URL", ""))

    def do_OPTIONS(self):
        self.send_response(204 if self._origin_ok() else 403)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            return self._json(403, {"error": "origin"})
        if self.path == "/health":
            return self._json(200, {"ok": True, "version": VERSION, "usb": bool(USB.mounts())})
        if self.path == "/files":
            return self._json(200, {"files": USB.files()})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._origin_ok() or self.path != "/upload":
            return self._json(403, {"error": "forbidden"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 10_000)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad_json"})
        path = USB.resolve(str(data.get("path", "")))
        url = str(data.get("url", ""))
        auth = str(data.get("authorization", ""))
        if path is None or not path.is_file():
            return self._json(404, {"error": "file_not_found"})
        if not allowed_upload_url(url, CONFIG) or not auth.startswith("Kiosk "):
            return self._json(400, {"error": "upload_not_allowed"})
        status = multipart_upload(url, auth, path)
        return self._json(200 if status < 300 else 502, {"status": status})


# ------------------------------------------------------------------ heartbeat


def run_command(command: str):
    if command == "reload":
        subprocess.run(["pkill", "-f", "chromium"], check=False)
    elif command == "reboot":
        subprocess.run(["systemctl", "reboot"], check=False)


def heartbeat_once(config: dict) -> list[str]:
    body = json.dumps(
        {
            "version": VERSION,
            "vpn_ip": config.get("VPN_IP") or None,
            "uptime_seconds": int(time.monotonic() - STARTED),
            "usb_present": bool(USB.mounts()),
        }
    ).encode()
    request = urllib.request.Request(
        config["API_URL"].rstrip("/") + "/api/v1/edge/heartbeat",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Device {config['DEVICE_TOKEN']}",
            "Content-Type": "application/json",
            "User-Agent": f"strongprint-agent/{VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read() or b"{}").get("commands", [])


def heartbeat_loop(config: dict, interval: int = 30):
    while True:
        try:
            for command in heartbeat_once(config):
                if command in ("reload", "reboot"):
                    run_command(command)
        except Exception as exc:  # rede em baixo: tenta no próximo ciclo
            print(f"heartbeat failed: {exc}", flush=True)
        time.sleep(interval)


def main():
    if not CONFIG.get("DEVICE_TOKEN") or not CONFIG.get("API_URL"):
        raise SystemExit(f"Missing DEVICE_TOKEN/API_URL in {ENV_FILE}")
    threading.Thread(target=heartbeat_loop, args=(CONFIG,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 8800), Handler)
    print(f"strongprint-agent {VERSION} on 127.0.0.1:8800", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
