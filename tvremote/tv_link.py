"""Keeps one live connection to the TV and exposes high-level actions."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import copy
import json
import time

import aiohttp
from aiowebostv import WebOsClient
from aiowebostv.handshake import REGISTRATION_MESSAGE
from aiowebostv import webos_client as _wc
from aiowebostv.buttons import BUTTONS
from aiowebostv.exceptions import WebOsTvPairError

from .config import Config

log = logging.getLogger("tvremote.tv")

# The library waits only 10 s for the user to press "Accept" on the TV's
# pairing prompt. That is too short with a bad physical remote.
_wc.RECEIVE_TIMEOUT = 60

StateListener = Callable[[dict[str, Any]], Awaitable[None]]

ALLOWED_BUTTONS = set(BUTTONS)


def wake_on_lan(macs: str, broadcast: str = "255.255.255.255") -> None:
    """Send a magic packet to each MAC in a comma-separated list."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for mac in filter(None, (m.strip() for m in macs.split(","))):
            packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
            for port in (9, 7):
                s.sendto(packet, (broadcast, port))


class TvLink:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession) -> None:
        self.cfg = cfg
        self.session = session
        self.client: WebOsClient | None = None
        self.status = "connecting"  # connecting | pairing | connected | off | error
        self.status_detail = ""
        self.listeners: set[StateListener] = set()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.keyboard_focus = False
        self._kbd_subscribed = False
        # None = unknown (server just started), so we never pop the launcher
        # over something already playing; False -> True is a real power-on.
        self._tv_was_on: bool | None = None
        self.server_url = ""
        self._unreachable_since: float | None = None
        self._fast_launched_at = 0.0
        self._kbd_lock = asyncio.Lock()

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.client:
            await self.client.disconnect()

    def poke(self) -> None:
        """Retry connecting right away (e.g. after Wake-on-LAN)."""
        self._wake.set()

    async def _run(self) -> None:
        delay = 1.0
        while True:
            if not self.cfg["client_key"] and not self.listeners:
                # Pairing shows a prompt on the TV. Only do that while someone
                # has the remote open, so the TV isn't nagged when nobody is there.
                await self._set_status("pairing", "Open the remote on your phone to pair")
                self._wake.clear()
                await self._wake.wait()
            if self.cfg["client_key"] and not await self._reachable():
                # TV asleep / off: a short TCP probe is cheap, so check twice a
                # second and act the moment the TV is back.
                if self._unreachable_since is None:
                    self._unreachable_since = time.monotonic()
                self._tv_was_on = False
                if self.status != "off":
                    await self._set_status("off", "TV is off or unreachable")
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.25)
                except TimeoutError:
                    pass
                continue
            gone = time.monotonic() - self._unreachable_since if self._unreachable_since else 0.0
            if self._unreachable_since:
                log.info("TV reachable again after %.1f s", gone)
            self._unreachable_since = None
            if self.cfg["client_key"] and (self._tv_was_on is False or gone >= 5.0):
                # back after being off: launch first, full handshake second
                await self._fast_launch()
            client = WebOsClient(
                self.cfg["tv_host"], self.cfg["client_key"], client_session=self.session,
                # home Wi-Fi to this TV has 200 ms+ spikes: be patient before
                # declaring the link dead, or the remote keeps reconnecting
                connect_timeout=8, heartbeat=20,
            )
            await client.register_state_update_callback(self._on_state)
            self.client = client
            try:
                if not self.cfg["client_key"]:
                    await self._set_status("pairing", "Press Accept on the TV screen")
                await client.connect()
                if client.client_key and client.client_key != self.cfg["client_key"]:
                    self.cfg["client_key"] = client.client_key
                    log.info("paired with TV, key saved")
                self._kbd_subscribed = False
                self._learn_mac(client)
                await self._set_status("connected")
                await self.sync_keyboard()
                delay = 1.0
                if client.connect_task:
                    # wait() instead of await: a disconnect we trigger ourselves
                    # (TV going to standby) cancels this task, which is fine
                    await asyncio.wait({client.connect_task})
                log.info("TV connection closed")
                # a dropped link is not a power-off: forget, don't assume off
                if self._tv_was_on:
                    self._tv_was_on = None
            except asyncio.CancelledError:
                raise
            except WebOsTvPairError as err:
                await self._set_status("error", f"Pairing rejected: {err}")
                delay = 15.0
            except (TimeoutError, OSError, aiohttp.ClientError) as err:
                if self.cfg["client_key"]:
                    await self._set_status("off", "TV is off or unreachable")
                else:
                    await self._set_status("error", "Pairing timed out. Reopen the remote to try again")
                    delay = 30.0
                log.debug("connect failed: %r", err)
            except Exception as err:  # keep the loop alive no matter what
                log.exception("unexpected TV error")
                await self._set_status("error", repr(err))
            finally:
                self.client = None
                self._kbd_subscribed = False
                self.keyboard_focus = False
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 4.0)

    # ---------- phone keyboard ----------
    # While subscribed, the TV hands text fields to us instead of drawing its
    # own on-screen keyboard. So we only subscribe while a phone is open; with
    # no phone connected the TV keeps its normal keyboard for the IR remote.

    KBD_URI = "com.webos.service.ime/registerRemoteKeyboard"

    def add_listener(self, fn: StateListener) -> None:
        self.listeners.add(fn)
        if not self.cfg["client_key"] and self.status != "connected":
            self.poke()
        asyncio.create_task(self.sync_keyboard())

    def remove_listener(self, fn: StateListener) -> None:
        self.listeners.discard(fn)
        asyncio.create_task(self.sync_keyboard())

    async def sync_keyboard(self) -> None:
        async with self._kbd_lock:
            c = self.client
            if not c or not c.is_connected():
                return
            want = bool(self.listeners)
            try:
                if want and not self._kbd_subscribed:
                    await c.subscribe(self._on_keyboard, self.KBD_URI)
                    self._kbd_subscribed = True
                elif not want and self._kbd_subscribed:
                    await c.unsubscribe(self.KBD_URI)
                    self._kbd_subscribed = False
                    self.keyboard_focus = False
            except Exception as err:
                log.debug("keyboard subscription: %r", err)

    async def _on_keyboard(self, payload: dict[str, Any]) -> None:
        widget = payload.get("currentWidget")
        if widget is None:
            return
        focus = bool(widget.get("focus"))
        if focus != self.keyboard_focus:
            self.keyboard_focus = focus
            await self._broadcast()

    # ---------- state ----------

    async def _set_status(self, status: str, detail: str = "") -> None:
        self.status, self.status_detail = status, detail
        await self._broadcast()

    async def _fast_launch(self) -> None:
        """Open Quick Launch over a bare socket the instant the TV is back.

        The full client handshake (input socket + 8 subscriptions) takes a
        second or two on this Wi-Fi; this path needs one register round trip.
        It only fires if just an input or nothing is in front, so a long
        Wi-Fi outage mid-show never pops the launcher.
        """
        lid = self.cfg["launcher_app_id"]
        if not (self.cfg["fast_ui"] and self.cfg["launch_on_power"] and self.cfg.data.get("launcher_installed")):
            return
        reg = copy.deepcopy(REGISTRATION_MESSAGE)
        reg["payload"]["client-key"] = self.cfg["client_key"]
        try:
            async with asyncio.timeout(5):   # TV still asleep after 5 s: leave it
                async with self.session.ws_connect(f"ws://{self.cfg['tv_host']}:3000") as ws:
                    await ws.send_json(reg)
                    while True:
                        m = await ws.receive_json()
                        if m.get("type") == "registered":
                            break
                        if m.get("type") == "error":
                            return
                    async def req(i: str, uri: str, kind: str = "request") -> None:
                        await ws.send_json({"id": i, "type": kind, "uri": "ssap://" + uri, "payload": {}})
                    await req("fg", "com.webos.applicationManager/getForegroundAppInfo")
                    await req("pw", "com.webos.service.tvpower/power/getPowerState", "subscribe")
                    fg, awake = None, False
                    while fg is None or not awake:
                        m = await ws.receive_json()
                        p = m.get("payload") or {}
                        if m.get("id") == "fg":
                            fg = p.get("appId", "") or ""
                        elif m.get("id") == "pw":
                            awake = p.get("state") == "Active" and not p.get("processing")
                    if not self._quiet_foreground(fg):
                        log.info("TV back on with %s in front: not opening Quick Launch", fg)
                        return
                    await ws.send_json({"id": "ql", "type": "request", "uri": "ssap://system.launcher/launch",
                                        "payload": {"id": lid, "params": {"server": self.server_url}}})
                    await ws.receive_json()
                    self._fast_launched_at = time.monotonic()
                    log.info("TV turned on: opened Quick Launch (fast path)")
        except Exception as err:  # fall back to the normal path below
            log.info("fast launch failed: %r", err)

    async def _reachable(self) -> bool:
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(self.cfg["tv_host"], 3000), 0.5)
            w.close()
            return True
        except (OSError, TimeoutError):
            return False

    def _learn_mac(self, client: WebOsClient) -> None:
        """Remember the TV's MACs (for Wake-on-LAN) the first time we connect.

        The TV reports both its Wi-Fi and wired MAC without saying which one is
        in use, so keep both and wake both.
        """
        if self.cfg["tv_mac"]:
            return
        conn = client.tv_info.connection or {}
        macs = [(conn.get(k) or {}).get("macAddress") for k in ("wifiInfo", "wiredInfo")]
        macs = [m.lower() for m in macs if m]
        if macs:
            self.cfg["tv_mac"] = ",".join(macs)
            log.info("learned TV MAC address(es) for power-on")

    # ---------- recently used (feeds the launcher's Continue panel) ----------

    NOT_RECENT = ("com.webos.app.home", "com.webos.app.livemenu", "com.webos.app.notificationcenter",
                  "com.webos.app.remoteservice", "com.webos.app.connectionwizard", "com.palmdts.devmode",
                  "com.palm.app.settings", "com.webos.app.discovery")

    def _track_recent(self, state: Any) -> None:
        app_id = state.current_app_id
        if not app_id or app_id == self.cfg["launcher_app_id"] or app_id in self.NOT_RECENT:
            return
        playing = None
        for m in state.media_state or []:
            if m.get("appId") == app_id and m.get("playState") in ("playing", "paused"):
                playing = m.get("playState")
        recent = [r for r in self.cfg.data.get("recent", []) if r["id"] != app_id]
        prev = next((r for r in self.cfg.data.get("recent", []) if r["id"] == app_id), None)
        if prev and recent == self.cfg.data.get("recent", [])[1:] and prev.get("state") == playing:
            return  # already on top, nothing new: don't rewrite the file
        recent.insert(0, {"id": app_id, "at": int(time.time()), "state": playing})
        self.cfg["recent"] = recent[:8]

    def recent(self) -> list[dict[str, Any]]:
        apps = self.apps()
        labels = {}
        if self.client:
            for inp in self.client.tv_state.inputs.values():
                labels[inp.get("appId")] = inp.get("label")
        out = []
        for r in self.cfg.data.get("recent", []):
            title = labels.get(r["id"]) or (apps.get(r["id"]) or {}).get("title")
            if title:
                out.append({**r, "title": title})
        return out

    async def _on_state(self, state: Any) -> None:
        if state.power_state and state.is_on:
            self._track_recent(state)
        if not state.power_state and state.current_app_id is None:
            # the library wipes its state when a connection closes; that says
            # nothing about the TV's power, so don't treat it as "off"
            await self._broadcast()
            return
        on = bool(state.is_on)
        going_down = state.power_state.get("processing") in (
            "Request Power Off", "Request Active Standby", "Request Suspend", "Prepare Suspend")
        if (going_down or not on) and self._tv_was_on is not False:
            # TV is going to standby: drop the link now and start probing,
            # instead of waiting ~20 s for the heartbeat to time out
            self._tv_was_on = False
            if self.client:
                asyncio.create_task(self.client.disconnect())
            await self._broadcast()
            return
        if on and self._tv_was_on is False and time.monotonic() - self._fast_launched_at > 10:
            asyncio.create_task(self._launch_on_power())
        self._tv_was_on = on
        await self._broadcast()

    @staticmethod
    def _quiet_foreground(app_id: str | None) -> bool:
        """True if nothing the user chose is on screen (just woke up)."""
        return not app_id or app_id.startswith((
            "com.webos.app.hdmi", "com.webos.app.externalinput", "com.webos.app.livetv",
            "com.webos.app.home", "com.tvupgrade.quicklaunch"))

    async def _launch_on_power(self) -> None:
        """TV just turned on: put Quick Launch on screen instead of LG's home."""
        c = self.client
        lid = self.cfg["launcher_app_id"]
        if not (self.cfg["fast_ui"] and self.cfg["launch_on_power"] and c and lid in c.tv_state.apps):
            return
        if not self._quiet_foreground(c.tv_state.current_app_id):
            log.info("TV on with %s in front: not opening Quick Launch", c.tv_state.current_app_id)
            return
        try:
            await c.launch_app_with_params(lid, {"server": self.server_url})
            log.info("TV turned on: opened Quick Launch")
        except Exception as err:
            log.info("could not open Quick Launch on power-on: %r", err)

    async def _broadcast(self) -> None:
        snap = self.snapshot()
        if self.client and self.client.tv_state.apps:
            installed = self.cfg["launcher_app_id"] in self.client.tv_state.apps
            if self.cfg.data.get("launcher_installed") != installed:
                self.cfg["launcher_installed"] = installed
        await asyncio.gather(*(fn(snap) for fn in list(self.listeners)), return_exceptions=True)

    def apps(self) -> dict[str, dict[str, Any]]:
        return dict(self.client.tv_state.apps) if self.client else {}

    def snapshot(self) -> dict[str, Any]:
        st = self.client.tv_state if self.client else None
        apps = []
        inputs = []
        if st:
            for app in st.apps.values():
                apps.append(
                    {"id": app["id"], "title": app.get("title", app["id"]),
                     "bg": app.get("bgColor") or app.get("iconColor") or ""}
                )
            apps.sort(key=lambda a: a["title"].lower())
            for inp in st.inputs.values():
                inputs.append(
                    {"id": inp.get("id"), "app": inp.get("appId"),
                     "label": inp.get("label") or inp.get("id"),
                     "connected": inp.get("connected", False)}
                )
        current = st.current_app_id if st else None
        current_title = None
        if st and current in st.apps:
            current_title = st.apps[current].get("title")
        return {
            "type": "state",
            "status": self.status,
            "detail": self.status_detail,
            "on": bool(st and st.is_on),
            "volume": st.volume if st else None,
            "muted": st.muted if st else None,
            "current": current,
            "currentTitle": current_title,
            "apps": apps,
            "inputs": inputs,
            "favorites": self.cfg["favorites"],
            "fastUi": self.cfg["fast_ui"],
            "launchOnPower": self.cfg["launch_on_power"],
            "keyboard": self.keyboard_focus,
            "launcherInstalled": bool(st and self.cfg["launcher_app_id"] in st.apps),
        }

    # ---------- actions ----------

    def _need(self) -> WebOsClient:
        if not self.client or not self.client.is_connected():
            raise RuntimeError("TV not connected")
        return self.client

    async def button(self, name: str) -> None:
        if name not in ALLOWED_BUTTONS:
            raise ValueError(f"unknown button {name}")
        await self._need().button(name)

    async def power_on(self) -> None:
        if self.cfg["tv_mac"]:
            wake_on_lan(self.cfg["tv_mac"])
        # TV in "active standby" accepts this over the socket too
        if self.client and self.client.is_connected():
            try:
                await self.client.power_on()
            except Exception:
                pass
        self.poke()

    async def power_off(self) -> None:
        await self._need().power_off()

    async def open_fast_ui(self, server_url: str) -> None:
        c = self._need()
        lid = self.cfg["launcher_app_id"]
        if lid in c.tv_state.apps:
            # tell the launcher where this server is (phone IPs change)
            await c.launch_app_with_params(lid, {"server": server_url})
        else:
            # not sideloaded yet: show the same launcher in the TV's browser
            await c.request("system.launcher/open", {"target": f"{server_url}/tv"})

    async def home(self, server_url: str, force_original: bool = False) -> None:
        if self.cfg["fast_ui"] and not force_original:
            await self.open_fast_ui(server_url)
        else:
            await self.button("HOME")

    async def handle(self, msg: dict[str, Any], server_url: str) -> None:
        """Dispatch one action message from a phone or the TV launcher."""
        a = msg.get("a")
        c = self.client
        if a == "btn":
            await self.button(str(msg["name"]))
        elif a == "move":
            await self._need().move(int(msg["dx"]), int(msg["dy"]))
        elif a == "click":
            await self._need().click()
        elif a == "scroll":
            await self._need().scroll(int(msg["dx"]), int(msg["dy"]))
        elif a == "vol":
            await self._need().set_volume(max(0, min(100, int(msg["v"]))))
        elif a == "mute":
            await self._need().set_mute(bool(msg["on"]))
        elif a == "launch":
            await self._need().launch_app(str(msg["id"]))
        elif a == "input":
            await self._need().set_input(str(msg["id"]))
        elif a == "text":
            await self._need().request(
                "com.webos.service.ime/insertText", {"text": str(msg["text"]), "replace": 0}
            )
        elif a == "backspace":
            await self._need().request(
                "com.webos.service.ime/deleteCharacters", {"count": int(msg.get("n", 1))}
            )
        elif a == "enter":
            await self._need().request("com.webos.service.ime/sendEnterKey")
        elif a == "power":
            if c and c.is_connected() and c.tv_state.is_on:
                await self.power_off()
            else:
                await self.power_on()
        elif a == "home":
            await self.home(server_url)
        elif a == "original":
            await self.home(server_url, force_original=True)
        elif a == "fastui":
            await self.open_fast_ui(server_url)
        elif a == "set_launch_on_power":
            self.cfg["launch_on_power"] = bool(msg["on"])
            await self._broadcast()
        elif a == "set_fast_ui":
            self.cfg["fast_ui"] = bool(msg["on"])
            await self._broadcast()
        elif a == "fav":
            favs = [f for f in self.cfg["favorites"] if f != msg["id"]]
            if msg.get("on"):
                favs.append(str(msg["id"]))
            self.cfg["favorites"] = favs
            await self._broadcast()
        elif a == "fav_order":
            self.cfg["favorites"] = [str(x) for x in msg["ids"]]
            await self._broadcast()
        else:
            raise ValueError(f"unknown action {a!r}")
