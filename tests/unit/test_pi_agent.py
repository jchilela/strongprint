"""Agente do Raspberry Pi (deploy/kiosk/pi/agent.py)."""

import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "deploy/kiosk/pi/agent.py"


@pytest.fixture
def agent(monkeypatch, tmp_path):
    env = tmp_path / "agent.env"
    env.write_text(
        "API_URL=https://api.strongprint.it.ao\n"
        "SCREEN_URL=https://ecra.strongprint.it.ao/ecra/\n"
        "DEVICE_TOKEN=tok\nVPN_IP=10.77.1.1\n"
    )
    monkeypatch.setenv("STRONGPRINT_ENV", str(env))
    monkeypatch.setenv("USB_ROOTS", str(tmp_path / "media"))
    monkeypatch.setenv("USB_ANY_DIR", "1")
    spec = importlib.util.spec_from_file_location("sp_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_usb(tmp_path):
    pen = tmp_path / "media" / "julio" / "PEN"
    (pen / "aulas").mkdir(parents=True)
    (pen / "aulas" / "Relatorio.pdf").write_bytes(b"%PDF-1.4")
    (pen / "foto.JPG").write_bytes(b"x")
    (pen / "virus.exe").write_bytes(b"x")
    (pen / ".oculto.pdf").write_bytes(b"x")
    return pen


def test_env_and_url_rules(agent):
    assert agent.CONFIG["DEVICE_TOKEN"] == "tok"
    ok = "https://api.strongprint.it.ao/api/v1/jobs/123/file"
    assert agent.allowed_upload_url(ok, agent.CONFIG)
    assert agent.allowed_upload_url(
        "https://ecra.strongprint.it.ao/api/v1/jobs/1/file", agent.CONFIG
    )
    for bad in (
        "http://api.strongprint.it.ao/api/v1/jobs/1/file",
        "https://evil.example/api/v1/jobs/1/file",
        "https://api.strongprint.it.ao/admin/",
    ):
        assert not agent.allowed_upload_url(bad, agent.CONFIG)
    assert (
        agent.origin_of("https://ecra.strongprint.it.ao/ecra/") == "https://ecra.strongprint.it.ao"
    )


def test_usb_listing_hides_paths(agent, tmp_path):
    make_usb(tmp_path)
    files = agent.USB.files()
    names = sorted(f["name"] for f in files)
    assert names == ["Relatorio.pdf", "foto.JPG"]
    for f in files:
        assert "/" not in f["path"] and agent.USB.resolve(f["path"]).exists()
    assert agent.USB.resolve("unknown") is None


def test_http_server_cors_and_upload_guard(agent, tmp_path):
    make_usb(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    origin = "https://ecra.strongprint.it.ao"
    try:
        req = urllib.request.Request(base + "/health", headers={"Origin": origin})
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["ok"] is True
            assert r.headers["Access-Control-Allow-Origin"] == origin
            assert r.headers["Access-Control-Allow-Private-Network"] == "true"

        evil = urllib.request.Request(base + "/files", headers={"Origin": "https://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(evil)
        assert exc.value.code == 403

        with urllib.request.urlopen(
            urllib.request.Request(base + "/files", headers={"Origin": origin})
        ) as r:
            file_id = json.loads(r.read())["files"][0]["path"]
        body = json.dumps(
            {
                "path": file_id,
                "url": "https://evil.example/api/v1/jobs/1/file",
                "authorization": "Kiosk x",
            }
        ).encode()
        bad = urllib.request.Request(base + "/upload", data=body, headers={"Origin": origin})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad)
        assert exc.value.code == 400
    finally:
        server.shutdown()


def test_heartbeat_payload(agent, monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"commands": ["reload"]}).encode()

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    assert agent.heartbeat_once(agent.CONFIG) == ["reload"]
    assert captured["url"] == "https://api.strongprint.it.ao/api/v1/edge/heartbeat"
    assert captured["auth"] == "Device tok" and captured["body"]["vpn_ip"] == "10.77.1.1"
