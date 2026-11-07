"""A minimal fake LG webOS TV (SSAP over WebSocket) for testing without the real TV.

Run: .venv/bin/python tests/fake_tv.py   (listens on 127.0.0.1:3000)
Every message it receives is printed, so you can see what the remote sends.
"""

import asyncio
import json
import os
import sys

from aiohttp import web

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
APPS = [
    {"id": "netflix", "title": "Netflix", "icon": f"http://127.0.0.1:{PORT}/res/icon.png"},
    {"id": "youtube.leanback.v4", "title": "YouTube", "icon": f"http://127.0.0.1:{PORT}/res/icon.png"},
    {"id": "com.webos.app.hdmi1", "title": "HDMI 1"},
    {"id": "amazon", "title": "Prime Video"},
    {"id": "com.tvupgrade.quicklaunch", "title": "Quick Launch"},
]
INPUTS = [{"id": "HDMI_1", "label": "Set-top box", "appId": "com.webos.app.hdmi1", "connected": True},
          {"id": "HDMI_2", "label": "HDMI 2", "appId": "com.webos.app.hdmi2", "connected": False}]
state = {"volume": 12, "muted": False, "app": os.environ.get("FAKE_APP", "netflix"), "power": "Active"}
log: list = []

PAYLOADS = {
    "com.webos.service.tvpower/power/getPowerState": lambda: {"state": state["power"]},
    "com.webos.applicationManager/getForegroundAppInfo": lambda: {"appId": state["app"]},
    "audio/getStatus": lambda: {"mute": state["muted"], "volume": state["volume"]},
    "audio/getVolume": lambda: {"volume": state["volume"], "muted": state["muted"]},
    "com.webos.applicationManager/listLaunchPoints": lambda: {"launchPoints": APPS},
    "tv/getExternalInputList": lambda: {"devices": INPUTS},
    "com.webos.service.apiadapter/audio/getSoundOutput": lambda: {"soundOutput": "tv_speaker"},
    "com.webos.media/getForegroundAppInfo": lambda: {"foregroundAppInfo": []},
    "com.webos.service.ime/registerRemoteKeyboard": lambda: {"currentWidget": {"focus": False}},
    "system/getSystemInfo": lambda: {"modelName": "FAKE"},
    "com.webos.service.connectionmanager/getinfo":
        lambda: {"wifiInfo": {"macAddress": "AA:BB:CC:00:11:22"}, "wiredInfo": {"macAddress": "AA:BB:CC:00:11:33"}},
    "com.webos.service.update/getCurrentSWInformation": lambda: {"product_name": "fake"},
    "com.webos.service.networkinput/getPointerInputSocket":
        lambda: {"socketPath": f"ws://127.0.0.1:{PORT}/input"},
}
subs: dict[str, list[tuple[web.WebSocketResponse, object]]] = {}


async def push(uri: str) -> None:
    """Notify every open connection subscribed to uri, like a real TV."""
    live = []
    for ws, uid in subs.get(uri, []):
        if ws.closed:
            continue
        try:
            await ws.send_json({"type": "response", "id": uid,
                                "payload": {"returnValue": True, **PAYLOADS[uri]()}})
            live.append((ws, uid))
        except Exception:
            pass
    subs[uri] = live


async def main_ws(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    async for m in ws:
        msg = json.loads(m.data)
        t, uid = msg.get("type"), msg.get("id")
        if t == "hello":
            await ws.send_json({"type": "hello", "payload": {"deviceOSVersion": "4.1.0"}})
            continue
        if t == "register":
            if not msg["payload"].get("client-key"):
                # real TVs show an Accept prompt first, then send "registered"
                await ws.send_json({"type": "response", "id": uid, "payload": {"pairingType": "PROMPT"}})
            await ws.send_json({"type": "registered", "id": uid, "payload": {"client-key": "FAKEKEY"}})
            continue
        uri = msg.get("uri", "").removeprefix("ssap://")
        log.append(("main", uri, msg.get("payload")))
        print("main ", t, uri, msg.get("payload") or "", flush=True)
        if t == "subscribe":
            subs.setdefault(uri, []).append((ws, uid))
        if uri == "audio/setVolume":
            state["volume"] = msg["payload"]["volume"]
            await push("audio/getVolume"); await push("audio/getStatus")
        elif uri == "audio/setMute":
            state["muted"] = msg["payload"]["mute"]
            await push("audio/getVolume"); await push("audio/getStatus")
        elif uri == "system.launcher/launch":
            state["app"] = msg["payload"]["id"]
            await push("com.webos.applicationManager/getForegroundAppInfo")
        payload = PAYLOADS.get(uri, dict)()
        await ws.send_json({"type": "response", "id": uid, "payload": {"returnValue": True, **payload}})
    return ws


async def input_ws(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    async for m in ws:
        line = m.data.strip().replace("\n", " ")
        log.append(("input", line))
        print("input", line, flush=True)
    return ws


async def focus_text(_req: web.Request) -> web.Response:
    """GET /focus/1 or /focus/0 simulates the TV focusing a text field."""
    uri = "com.webos.service.ime/registerRemoteKeyboard"
    PAYLOADS[uri] = lambda f=_req.match_info["on"] == "1": {"currentWidget": {"focus": f}}
    await push(uri)
    return web.Response(text="ok")


async def power(req: web.Request) -> web.Response:
    """GET /power/Suspend or /power/Active simulates the TV sleeping / waking."""
    state["power"] = req.match_info["state"]
    print("event power", state["power"], flush=True)
    if state["power"] != "Active":
        state["app"] = ""          # real TVs close every app on the way into standby
        await push("com.webos.applicationManager/getForegroundAppInfo")
    await push("com.webos.service.tvpower/power/getPowerState")
    return web.Response(text="ok")


async def drop(_req: web.Request) -> web.Response:
    """GET /drop closes the remote's connection, like a Wi-Fi hiccup."""
    print("event drop", flush=True)
    for ws in {ws for lst in subs.values() for ws, _ in lst}:
        await ws.close()
    subs.clear()
    return web.Response(text="ok")


async def icon(_req: web.Request) -> web.Response:
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360f8cff01f0005fe02fea7d1a3e90000000049454e44ae426082")
    return web.Response(body=png, content_type="image/png")


app = web.Application()
app.router.add_get("/", main_ws)
app.router.add_get("/input", input_ws)
app.router.add_get("/focus/{on}", focus_text)
app.router.add_get("/res/icon.png", icon)
app.router.add_get("/power/{state}", power)
app.router.add_get("/drop", drop)
web.run_app(app, host="127.0.0.1", port=PORT, print=lambda *_: print(f"fake TV on :{PORT}", flush=True))
