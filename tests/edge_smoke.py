"""Run python tests/edge_smoke.py on Windows with Edge and the project requirements.

The optional first argument selects an Edge executable installed elsewhere.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import ExitStack
from unittest.mock import AsyncMock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_member_login import MemoryCollection, ROOM_PASSWORD, LEGACY_KEY, PASSWORD, room
import uvicorn
from websockets.sync.client import connect

local_http = build_opener(ProxyHandler({}))

def edge_executable():
    if len(sys.argv) > 1:
        return str(Path(sys.argv[1]).resolve(strict=True))
    for folder in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if folder in os.environ:
            candidate = Path(os.environ[folder]) / "Microsoft/Edge/Application/msedge.exe"
            if candidate.is_file():
                return str(candidate)
    raise SystemExit("Edge is unavailable. Pass its executable path as an argument.")


class Browser:
    def __init__(self, profile, url, blocked_proxy, stack):
        # The proxy port is bound without listening. Only the local application is reachable.
        args = [edge_executable(), "--headless=new", "--disable-gpu", "--disable-background-networking",
                "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-component-update",
                "--disable-domain-reliability", "--disable-extensions", "--disable-quic", "--disable-breakpad",
                "--proxy-server=" + blocked_proxy, "--proxy-bypass-list=127.0.0.1;localhost",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + str(profile), "about:blank"]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        def close_process():
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(10)
        stack.callback(close_process)
        portfile = profile / "DevToolsActivePort"
        for _ in range(150):
            if portfile.is_file():
                break
            if self.proc.poll() is not None:
                raise RuntimeError("headless Edge exited with " + str(self.proc.returncode))
            time.sleep(.1)
        port, _ = portfile.read_text().splitlines()
        with local_http.open("http://127.0.0.1:" + port + "/json/list", timeout=3) as resp:
            tab = next(t for t in json.load(resp) if t["type"] == "page")
        self.ws = connect(tab["webSocketDebuggerUrl"], proxy=None, open_timeout=5, close_timeout=2)
        stack.callback(self.ws.close)
        def close_browser():
            try:
                self.ws.send(json.dumps({"id": 0, "method": "Browser.close"}))
            except OSError:
                pass
        stack.callback(close_browser)
        self.sequence = 0
        self.room_connections = 0
        self.room_ws_url = url.replace("http://", "ws://") + "/ws"
        self.call("Runtime.enable")
        self.call("Page.enable")
        self.call("Network.enable")
        self.call("Network.setBlockedURLs", {"urls": ["https://*", "http://*.com/*", "wss://*"]})
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": "window.alert = m => window.lastTestAlert = m;"})
        navigation = self.call("Page.navigate", {"url": url})
        assert not navigation.get("errorText"), navigation.get("errorText")
        self.until("document.getElementById('loginMode')?.value === 'login' && document.getElementById('loginHelp').textContent.length > 0", "initial login screen")

    def call(self, method, params=None):
        self.sequence += 1
        self.ws.send(json.dumps({"id": self.sequence, "method": method, "params": params or {}}))
        while True:
            event = json.loads(self.ws.recv(timeout=8))
            if event.get("method") == "Network.webSocketCreated" and event["params"]["url"] == self.room_ws_url:
                self.room_connections += 1
            if event.get("id") == self.sequence:
                if "error" in event:
                    raise RuntimeError(str(event["error"]))
                return event["result"]

    def js(self, expr):
        result = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result.get("result", {}).get("description", "script exception"))
        return result["result"].get("value")

    def until(self, expr, label):
        for _ in range(150):
            if self.js("typeof sessionToken !== 'undefined' && (" + expr + ")"):
                print("PASS " + label, flush=True)
                return
            time.sleep(.1)
        summary = self.js("[location.href, document.title, document.body?.innerText.slice(0,160), document.getElementById('loginStatus')?.textContent]")
        raise AssertionError(label + " / " + str(summary))

    def submit(self, mode, pw, confirmation=None):
        # Trigger the normal change handler, native form validation and submit event.
        if self.js("byId('loginMode').value") != mode:
            self.js("byId('loginMode').value=" + json.dumps(mode) + ";byId('loginMode').dispatchEvent(new Event('change'))")
        self.js("byId('nickInput').value='SmokeWriter';byId('pwInput').value=" + json.dumps(pw))
        if mode in ("signup", "recover", "migrate"):
            self.js("byId('confirmInput').value=" + json.dumps(pw if confirmation is None else confirmation))
        self.js("byId('loginSubmit').click()")

    def assert_no_retry(self, expected, label):
        # The production client retries a lost connection after three seconds.
        time.sleep(4)
        self.js("document.readyState")
        assert self.room_connections == expected, label
        print("PASS " + label, flush=True)


def run():
    initial = room.load_data()
    initial["global_notice"] = "private synthetic notice"
    initial["identity_keys"] = {room.name_id("SmokeWriter"): room.identity_hash(LEGACY_KEY)}
    room.server_state = copy.deepcopy(initial)
    room.collection = MemoryCollection(initial)
    # This test exercises account durability through MemoryCollection. Room content persistence uses other Mongo operators.
    room.persist_state = AsyncMock()
    room.read_card_background = lambda _index: None
    room.load_pomo_url = lambda: ""
    config = uvicorn.Config(room.app, log_level="critical", access_log=False, lifespan="off")
    server = uvicorn.Server(config)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server_loop = None
    async def serve():
        nonlocal server_loop
        server_loop = asyncio.get_running_loop()
        await server.serve(sockets=[sock])

    async def disconnect_test_transport():
        connections = [conn for conn in server.server_state.connections
                       if (getattr(conn, "scope", None) or {}).get("type") == "websocket"]
        assert len(connections) == 2
        for conn in connections:
            conn.transport.abort()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    def check_public_count(expected):
        with local_http.open(url + "/user_count", timeout=8) as response:
            payload = json.load(response)
            assert response.status == 200 and set(payload) == {"count"}
            assert type(payload["count"]) is int and payload["count"] == expected

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.1)
        assert server.started
        with local_http.open(url, timeout=8) as resp:
            assert b'id="loginMode"' in resp.read()
        print("PASS loopback server serves real application", flush=True)
        with local_http.open(url + "/healthz", timeout=8) as resp:
            assert resp.status == 200 and json.load(resp) == {"ok": True}
        check_public_count(0)
        for resource in ("/pomo-config", "/card-background/0"):
            try:
                with local_http.open(url + resource, timeout=8):
                    raise AssertionError("Anonymous request disclosed " + resource)
            except HTTPError as error:
                assert error.code == 403
                error.close()
        print("PASS anonymous probes return health and the integer count; private room resources require authentication", flush=True)
        root = Path(tempfile.mkdtemp(prefix="gogogo-edge-smoke-")).resolve()
        with ExitStack() as stack:
            def remove_profiles():
                if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("gogogo-edge-smoke-"):
                    raise AssertionError("Unexpected temporary profile location")
                for _ in range(10):
                    try:
                        shutil.rmtree(root)
                        return
                    except PermissionError:
                        time.sleep(.3)
                print("Temporary Edge profile still in use:", root, file=sys.stderr)
            stack.callback(remove_profiles)
            proxy_socket = stack.enter_context(socket.socket())
            proxy_socket.bind(("127.0.0.1", 0))
            blocked_proxy = "http://127.0.0.1:" + str(proxy_socket.getsockname()[1])
            a = Browser(root / "a", url, blocked_proxy, stack)
            b = Browser(root / "b", url, blocked_proxy, stack)
            count_label = "byId('loginUserCount').textContent === '🔥 현재 달리고 있는 작가님: "
            for browser in (a, b):
                browser.until(count_label + "0명'", "fresh login screen shows zero participants")
            assert a.js("document.body.innerText.includes('private synthetic notice')") is False
            visible_fields = "Array.from(document.querySelectorAll('.login-box input')).filter(el => el.getClientRects().length).map(el => el.id).join(',')"
            assert b.js(visible_fields) == "nickInput,pwInput"
            print("PASS fresh profile's default form requires only nickname and personal password", flush=True)
            a.js("localStorage.setItem('gogogoIdentity:SmokeWriter'," + json.dumps(LEGACY_KEY) + ")")
            a.submit("legacy", ROOM_PASSWORD)
            a.until("byId('loginMode').value === 'migrate'", "first profile proves its previous device identity")
            personal = "  한글 " + PASSWORD + "  "
            a.submit("migrate", unicodedata.normalize("NFD", personal), personal)
            a.until("window.rawNotice === 'private synthetic notice' && !!sessionToken", "migration preserves access to the existing room")
            check_public_count(1)
            b.until(count_label + "1명' && !sessionToken && !window.rawNotice", "second login screen polls and shows one participant before authentication")
            b.until("!localStorage.getItem('gogogoIdentity:SmokeWriter') && !sessionToken", "second real browser profile has no saved device identity")
            b.submit("login", "  틀린 " + PASSWORD + "  ")
            b.until("!byId('loginSubmit').disabled && byId('loginStatus').textContent === '닉네임 또는 비밀번호가 맞지 않습니다.'", "wrong password is rejected")
            b.submit("login", personal)
            b.until("window.rawNotice === 'private synthetic notice' && !!sessionToken", "fresh profile logs in with nickname and personal password (NFC after an NFD setup)")
            a.until("!!sessionToken && window.rawNotice === 'private synthetic notice' && byId('loginOverlay').style.display === 'none'", "both devices stay signed in at the same time")
            check_public_count(2)
            print("PASS public count shows both open devices", flush=True)
            for browser in (a, b):
                browser.js("window.testPreviousSession = sessionToken")
            before = (a.room_connections, b.room_connections)
            asyncio.run_coroutine_threadsafe(disconnect_test_transport(), server_loop).result(5)
            for browser, label in ((a, "first"), (b, "second")):
                browser.until("sessionToken && sessionToken !== window.testPreviousSession", f"network loss automatically reconnects the {label} device")
            assert (a.room_connections, b.room_connections) == (before[0] + 1, before[1] + 1)
            a.until("!!sessionToken && byId('loginOverlay').style.display === 'none'", "after reconnecting, the first device is still signed in")
            b.until("!!sessionToken && byId('loginOverlay').style.display === 'none'", "after reconnecting, the second device is still signed in")
            # Self-service change from a signed-in device: no current password, no admin code.
            changed = "내가 새로 정한 비밀번호"
            b.js("byId('changePasswordBtn').click()")
            b.until("byId('passwordDialog').open && !byId('changePasswordBtn').hidden", "signed-in member opens the password change dialog")
            b.js("byId('newPassword').value=" + json.dumps(changed) + ";byId('confirmPassword').value=" + json.dumps(changed) + ";byId('changeSubmit').click()")
            b.until("byId('changeStatus').textContent.startsWith('바꿨습니다')", "member changes the password without the current one")
            a.until("!!sessionToken && byId('loginOverlay').style.display === 'none'", "the other device stays connected after the change")
            a.js("byId('changePasswordBtn').click();byId('newPassword').value='다른 기기가 되돌리려는 비밀번호';byId('confirmPassword').value='다른 기기가 되돌리려는 비밀번호';byId('changeSubmit').click()")
            a.until("byId('changeStatus').textContent.startsWith('다른 기기에서 비밀번호가 이미 바뀌었습니다')", "a device signed in before the change cannot change it again")
            a.js("byId('passwordDialog').close()")
            b.js("location.reload()")
            time.sleep(1)
            b.until("!sessionToken && byId('loginOverlay').style.display === 'flex'", "reloaded device shows the login screen")
            b.submit("login", changed)
            b.until("window.rawNotice === 'private synthetic notice' && !!sessionToken", "the new password signs in")
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


if __name__ == "__main__":
    run()
