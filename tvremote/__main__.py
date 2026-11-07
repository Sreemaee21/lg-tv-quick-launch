"""Run: uv run python -m tvremote   then open http://<this-pc>:8080 on your phone."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import io
import socket
import tarfile
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from .config import Config
from .tv_link import TvLink

HERE = Path(__file__).resolve().parent
log = logging.getLogger("tvremote")

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type"}


def discover_tv(timeout: float = 3.0) -> str | None:
    """Find an LG webOS TV on the local network (SSDP)."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: urn:lge-com:service:webos-second-screen:1\r\n\r\n").encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(msg, ("239.255.255.250", 1900))
        try:
            _data, (ip, _port) = s.recvfrom(4096)
            return ip
        except OSError:
            return None


def lan_ip(tv_host: str) -> str:
    """IP of the interface that routes to the TV (what the TV/phone should use)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((tv_host, 9))
        return s.getsockname()[0]


def server_url(app: web.Application) -> str:
    return f"http://{app['lan_ip']}:{app['cfg']['port']}"


# ---------- phone websocket ----------

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    tv: TvLink = app["tv"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)

    async def push(snap: dict[str, Any]) -> None:
        if not ws.closed:
            await ws.send_json(snap)

    tv.add_listener(push)
    await push(tv.snapshot())
    try:
        async for m in ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(m.data)
                await tv.handle(msg, server_url(app))
            except Exception as err:  # report, never drop the socket
                log.debug("action failed: %r", err)
                await ws.send_json({"type": "error", "msg": str(err)})
    finally:
        tv.remove_listener(push)
    return ws


# ---------- TV launcher API ----------

async def api_launcher(request: web.Request) -> web.Response:
    tv: TvLink = request.app["tv"]
    apps = tv.apps()
    snap = tv.snapshot()
    favs = [
        {"id": a, "title": apps[a].get("title", a), "bg": apps[a].get("bgColor", "")}
        for a in tv.cfg["favorites"] if a in apps
    ]
    all_apps = [{"id": a["id"], "title": a["title"], "bgColor": a["bg"]} for a in snap["apps"]]
    return web.json_response(
        {"favorites": favs, "inputs": snap["inputs"], "apps": all_apps, "fastUi": tv.cfg["fast_ui"],
         "recent": tv.recent(), "now": int(time.time())},
        headers=CORS,
    )


async def api_action(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(headers=CORS)
    tv: TvLink = request.app["tv"]
    try:
        await tv.handle(await request.json(), server_url(request.app))
    except Exception as err:
        return web.json_response({"ok": False, "error": str(err)}, status=400, headers=CORS)
    return web.json_response({"ok": True}, headers=CORS)


async def icon(request: web.Request) -> web.Response:
    """Proxy an app icon from the TV. Only URLs the TV itself reported are fetched."""
    tv: TvLink = request.app["tv"]
    app_id = request.match_info["app_id"]
    cache: dict[str, tuple[bytes, str]] = request.app["icon_cache"]
    if app_id in cache:
        body, ctype = cache[app_id]
        return web.Response(body=body, content_type=ctype, headers={**CORS, "Cache-Control": "max-age=86400"})
    info = tv.apps().get(app_id)
    url = info and (info.get("largeIcon") or info.get("icon"))
    if not url:
        raise web.HTTPNotFound()
    try:
        async with request.app["session"].get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status != 200:
                raise web.HTTPNotFound()
            body = await r.read()
            ctype = r.content_type or "image/png"
    except aiohttp.ClientError:
        raise web.HTTPNotFound()
    cache[app_id] = (body, ctype)
    return web.Response(body=body, content_type=ctype, headers={**CORS, "Cache-Control": "max-age=86400"})


# ---------- one-time phone setup (Termux) ----------

async def setup_script(request: web.Request) -> web.Response:
    text = (HERE.parent / "contrib" / "termux_setup.sh").read_text()
    return web.Response(text=text.replace("__SERVER__", server_url(request.app)),
                        content_type="text/x-shellscript")


async def tv_background(_req: web.Request) -> web.FileResponse:
    """The launcher's background: your own bg.jpg if present, else the default."""
    own = HERE / "tv" / "bg.jpg"
    return web.FileResponse(own if own.exists() else HERE / "tv" / "bg.default.jpg")


async def setup_bundle(request: web.Request) -> web.Response:
    """The tvremote package plus settings (favourites etc.), minus the pairing key.

    The phone pairs on its own, so this laptop's key never leaves it.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(HERE, arcname="tvremote",
                filter=lambda ti: None if "__pycache__" in ti.name else ti)
        settings = {k: v for k, v in request.app["cfg"].data.items() if k != "client_key"}
        raw = json.dumps(settings, indent=2).encode()
        info = tarfile.TarInfo("data/state.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    return web.Response(body=buf.getvalue(), content_type="application/gzip")


async def index(_req: web.Request) -> web.FileResponse:
    return web.FileResponse(HERE / "web" / "index.html")


async def tv_index(_req: web.Request) -> web.Response:
    raise web.HTTPFound("/tv/index.html")


# ---------- Developer Mode keep-alive ----------

DEVMODE_RESET = "https://developer.lge.com/secure/ResetDevModeSession.dev?sessionToken={}"


async def devmode_keepalive(app: web.Application) -> None:
    """Do what the 'Extend' button in LG's Developer Mode app does, once a day."""
    while True:
        token = app["cfg"]["dev_token"]
        if token:
            try:
                url = DEVMODE_RESET.format(token)
                async with app["session"].get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    log.info("dev mode session extended: %s", (await r.text())[:120])
            except Exception as err:  # offline etc: try again later
                log.info("dev mode extend failed: %r", err)
                await asyncio.sleep(3600)
                continue
        await asyncio.sleep(24 * 3600)


# ---------- app ----------

async def on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession()
    app["tv"] = TvLink(app["cfg"], app["session"])
    app["tv"].server_url = server_url(app)
    app["tv"].start()
    app["keepalive"] = asyncio.create_task(devmode_keepalive(app))


async def on_cleanup(app: web.Application) -> None:
    app["keepalive"].cancel()
    await app["tv"].stop()
    await app["session"].close()


def build_app(cfg: Config) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["icon_cache"] = {}
    app["lan_ip"] = lan_ip(cfg["tv_host"])
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/tv", tv_index)
    app.router.add_get("/tv/bg.jpg", tv_background)
    app.router.add_get("/api/launcher", api_launcher)
    app.router.add_route("*", "/api/action", api_action)
    app.router.add_get("/icon/{app_id}", icon)
    app.router.add_get("/setup/termux.sh", setup_script)
    app.router.add_get("/setup/bundle.tar.gz", setup_bundle)
    app.router.add_static("/static/", HERE / "web")
    app.router.add_static("/tv/", HERE / "tv")
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description="LG webOS phone remote")
    p.add_argument("--tv", help="TV IP address (saved for next time)")
    p.add_argument("--port", type=int, help="port to serve on (saved)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s.%(msecs)03d %(name)s %(message)s", datefmt="%H:%M:%S")
    if not args.verbose:
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = Config()
    if args.tv:
        cfg["tv_host"] = args.tv
    if not cfg["tv_host"]:
        print("Looking for an LG TV on the network...")
        found = discover_tv()
        if not found:
            raise SystemExit("No LG webOS TV found. Turn it on, or pass its address: --tv 192.168.x.y")
        print(f"Found TV at {found}")
        cfg["tv_host"] = found
    if args.port:
        cfg["port"] = args.port
    cfg.save()
    app = build_app(cfg)
    print(f"\n  Phone remote:  http://{app['lan_ip']}:{cfg['port']}\n")
    web.run_app(app, host="0.0.0.0", port=cfg["port"], print=None)


if __name__ == "__main__":
    main()
