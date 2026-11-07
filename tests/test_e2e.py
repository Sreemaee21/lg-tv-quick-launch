"""End-to-end test: fake TV  <->  tvremote server  <->  scripted phone.

Run: .venv/bin/python tests/test_e2e.py
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SRV = "http://127.0.0.1:8090"


async def wait_port(url: str) -> None:
    async with aiohttp.ClientSession() as s:
        for _ in range(50):
            try:
                async with s.get(url):
                    return
            except aiohttp.ClientError:
                await asyncio.sleep(0.1)
    raise RuntimeError(f"{url} never came up")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    state_file = tmp / "state.json"
    state_file.write_text(json.dumps({"tv_host": "127.0.0.1", "port": 8090}))
    env = {**os.environ, "TVREMOTE_STATE": str(state_file)}
    tv = subprocess.Popen([PY, str(ROOT / "tests/fake_tv.py")], stdout=subprocess.PIPE, text=True)
    srv = subprocess.Popen([PY, "-m", "tvremote"], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    failures = []
    tv_logs: list[str] = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    try:
        await wait_port(SRV + "/api/launcher")
        await asyncio.sleep(1.0)
        async with aiohttp.ClientSession() as s:
            # before a phone connects, nothing pairs
            check("no pairing without a phone", json.loads(state_file.read_text()).get("client_key") is None)

            ws = await s.ws_connect(SRV + "/ws")

            async def next_state(pred, timeout=5):
                end = asyncio.get_running_loop().time() + timeout
                while True:
                    left = end - asyncio.get_running_loop().time()
                    if left <= 0:
                        return None
                    try:
                        m = await ws.receive_json(timeout=left)
                    except TimeoutError:
                        return None
                    if m.get("type") == "state" and pred(m):
                        return m

            st = await next_state(lambda m: m["status"] == "connected" and m["apps"])
            check("phone connect triggers pairing and connects", st is not None)
            check("client key saved", json.loads(state_file.read_text()).get("client_key") == "FAKEKEY")
            check("TV MACs learned for power-on",
                  json.loads(state_file.read_text()).get("tv_mac") == "aa:bb:cc:00:11:22,aa:bb:cc:00:11:33")
            check("apps listed", st and {a["title"] for a in st["apps"]} >= {"Netflix", "YouTube"})
            check("current app title", st and st["currentTitle"] == "Netflix")
            check("volume reported", st and st["volume"] == 12)
            check("inputs listed", st and len(st["inputs"]) == 2)

            for msg in [
                {"a": "btn", "name": "UP"}, {"a": "btn", "name": "ENTER"},
                {"a": "move", "dx": 5, "dy": -3}, {"a": "click"}, {"a": "scroll", "dx": 0, "dy": 4},
                {"a": "text", "text": "hello"}, {"a": "backspace", "n": 2}, {"a": "enter"},
                {"a": "launch", "id": "youtube.leanback.v4"}, {"a": "input", "id": "HDMI_1"},
                {"a": "vol", "v": 30}, {"a": "mute", "on": True},
                {"a": "fav", "id": "netflix", "on": True}, {"a": "fav", "id": "youtube.leanback.v4", "on": True},
                {"a": "original"},
            ]:
                await ws.send_json(msg)
            st = await next_state(lambda m: m["currentTitle"] == "YouTube" and m["muted"] and m["volume"] == 30
                                  and len(m["favorites"]) == 2)
            check("launch/volume/mute/favourites reflected in state", st is not None)

            await ws.send_json({"a": "btn", "name": "NOT_A_KEY"})
            err = None
            for _ in range(10):
                m = await ws.receive_json(timeout=3)
                if m.get("type") == "error":
                    err = m
                    break
            check("bad button rejected with error", err is not None and "unknown button" in err["msg"])

            # TV focuses a text field -> phone is told to show the keyboard bar
            async with s.get("http://127.0.0.1:3000/focus/1"):
                pass
            check("keyboard focus pushed to phone", await next_state(lambda m: m["keyboard"]) is not None)
            async with s.get("http://127.0.0.1:3000/focus/0"):
                pass
            check("keyboard unfocus pushed", await next_state(lambda m: not m["keyboard"]) is not None)

            # Fast UI on + launcher not installed -> Home opens /tv in the browser
            await ws.send_json({"a": "home"})
            await ws.send_json({"a": "set_fast_ui", "on": False})
            check("fast UI switch off", await next_state(lambda m: m["fastUi"] is False) is not None)
            await ws.send_json({"a": "home"})

            async with s.get(SRV + "/api/launcher") as r:
                lj = await r.json()
            check("TV launcher gets favourites in order", [f["title"] for f in lj["favorites"]] == ["Netflix", "YouTube"])
            async with s.get(SRV + "/icon/netflix") as r:
                check("icon proxied from TV", r.status == 200 and r.content_type == "image/png")
            async with s.post(SRV + "/api/action", json={"a": "launch", "id": "amazon"}) as r:
                check("launcher HTTP action works", (await r.json())["ok"])
            await asyncio.sleep(0.5)
            async with s.get(SRV + "/api/launcher") as r:
                rec = (await r.json()).get("recent", [])
            check("recently used tracked, newest first", [x["id"] for x in rec[:2]] == ["amazon", "youtube.leanback.v4"])
            check("recent entries carry titles", bool(rec) and rec[0].get("title") == "Prime Video")
            # ---- power-on opens Quick Launch; server start and Wi-Fi drops don't ----
            await ws.send_json({"a": "set_fast_ui", "on": True})
            await asyncio.sleep(0.3)
            async with s.get("http://127.0.0.1:3000/drop"):
                pass
            await asyncio.sleep(2.5)                      # reconnects while TV stays on
            async with s.get("http://127.0.0.1:3000/power/Suspend"):
                pass
            await asyncio.sleep(0.5)
            async with s.get("http://127.0.0.1:3000/power/Active"):
                pass
            await asyncio.sleep(1.0)
            await ws.close()
            await asyncio.sleep(0.8)
            # ---- TV disappears from the network (standby) and comes back ----
            async def outage(app_in_front: str) -> str:
                nonlocal tv
                tv.kill()                               # vanishes like a TV going to standby
                tv_logs.append(tv.communicate(timeout=5)[0])
                await asyncio.sleep(6)                  # "off" long enough to count
                tv = subprocess.Popen([PY, str(ROOT / "tests/fake_tv.py")], stdout=subprocess.PIPE, text=True,
                                      env={**os.environ, "FAKE_APP": app_in_front})
                await asyncio.sleep(4)
                tv.kill()
                return tv.communicate(timeout=5)[0]
            nonlocal_out = await outage("com.webos.app.hdmi2")
            fast = [l for l in nonlocal_out.splitlines() if "system.launcher/launch" in l and "quicklaunch" in l]
            check("TV back from standby on an input: Quick Launch opened once", len(fast) == 1)
            busy = await outage("netflix")
            check("TV back with Netflix in front: launcher not forced", not any(
                "system.launcher/launch" in l and "quicklaunch" in l for l in busy.splitlines()))
            tv = subprocess.Popen([PY, str(ROOT / "tests/fake_tv.py")], stdout=subprocess.PIPE, text=True)
    finally:
        srv.terminate(); srv.wait()
        tv.terminate()
        last = tv.communicate(timeout=5)[0]
        out = tv_logs[0] if tv_logs else last

    def saw(fragment):
        return any(fragment in line for line in out.splitlines())

    check("input socket got UP", saw("input type:button name:UP"))
    check("input socket got pointer move", saw("input type:move dx:5 dy:-3"))
    check("input socket got click", saw("input type:click"))
    check("text inserted via IME", saw("ime/insertText {'text': 'hello'"))
    check("backspace via IME", saw("deleteCharacters {'count': 2}"))
    check("enter via IME", saw("sendEnterKey"))
    check("original LG home = HOME key", saw("input type:button name:HOME"))
    check("fast home opened the installed launcher", saw("system.launcher/launch {'id': 'com.tvupgrade.quicklaunch'"))
    check("keyboard subscription released when phone leaves", saw("unsubscribe com.webos.service.ime/registerRemoteKeyboard"))
    lines = out.splitlines()
    def launches_between(start, end):
        i = next(n for n, l in enumerate(lines) if start in l)
        j = next((n for n, l in enumerate(lines) if end in l and n > i), len(lines))
        return [l for l in lines[i:j] if "system.launcher/launch" in l and "quicklaunch" in l]
    check("Wi-Fi drop + reconnect did not pop the launcher", not launches_between("event drop", "event power Suspend"))
    after = launches_between("event power Active", "\0")
    check("power-on opened Quick Launch exactly once", len(after) == 1)
    if len(after) != 1:
        print("   launches after power-on:", after)
    check("launcher told the server address", bool(after) and "'server': 'http://" in after[0])
    print()
    print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
    sys.exit(1 if failures else 0)


asyncio.run(main())
