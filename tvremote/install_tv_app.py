"""Package the Quick Launch app and install it on the TV via Developer Mode.

No LG SDK needed. Usage:
    uv run python -m tvremote.install_tv_app --passphrase ABC123
    uv run python -m tvremote.install_tv_app --build-only      # just make the .ipk

Before installing: install "Developer Mode" from the LG Content Store, sign in
with a free LG developer account, switch Dev Mode on, let the TV reboot, then
switch "Key Server" on. The passphrase is shown in the Developer Mode app.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import ROOT, Config
from .pngcolor import accent

APP_DIR = Path(__file__).resolve().parent / "tv"
DIST = ROOT / "dist"


def _tar_gz(entries: list[tuple[str, bytes | None]]) -> bytes:
    """entries: (path, data) where data None means a directory."""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for name, data in entries:
            ti = tarfile.TarInfo("./" + name if name else ".")
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = now, 0, 0, "root", "root"
            if data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(ti)
            else:
                ti.size, ti.mode = len(data), 0o644
                tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    now = int(time.time())
    for name, data in members:
        hdr = f"{name:<16}{now:<12}{0:<6}{0:<6}{0o100644:<8o}{len(data):<10}`\n"
        assert len(hdr) == 60
        out += hdr.encode() + data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def snapshot_tv(cfg: Config) -> tuple[dict, dict[str, bytes]]:
    """Read the app list, inputs and icons from the TV over the remote-control API.

    Developer apps may launch apps but are denied listing them, so the
    launcher ships with this snapshot. Re-run the installer after adding apps.
    """
    import aiohttp
    from aiowebostv import WebOsClient

    async def run() -> tuple[dict, dict[str, bytes]]:
        if not cfg["client_key"]:
            raise SystemExit("This PC isn't paired with the TV yet: run `uv run python -m tvremote` once.")
        async with aiohttp.ClientSession() as session:
            c = WebOsClient(cfg["tv_host"], cfg["client_key"], client_session=session, connect_timeout=15)
            await c.connect()
            apps, icons = [], {}
            for i, a in enumerate(sorted(c.tv_state.apps.values(), key=lambda a: a.get("title", "").lower())):
                if a["id"] == cfg["launcher_app_id"]:
                    continue
                entry = {"id": a["id"], "title": a.get("title", a["id"]), "bgColor": a.get("bgColor", "")}
                url = a.get("largeIcon") or a.get("icon")
                if url:
                    try:
                        async with session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as r:
                            if r.status == 200:
                                ext = (r.content_type or "image/png").split("/")[-1].replace("jpeg", "jpg")
                                name = f"icons/{i}.{ext}"
                                icons[name] = await r.read()
                                entry["icon"] = name
                                entry["accent"] = accent(icons[name])
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                apps.append(entry)
            # browser bookmarks pinned to LG's home bar share the browser's app id,
            # so read them from the raw launch-point list
            bookmarks = []
            lps = (await c.request("com.webos.applicationManager/listLaunchPoints")).get("launchPoints", [])
            for lp in lps:
                url = (lp.get("params") or {}).get("target")
                if lp.get("id") != "com.webos.app.browser" or not url:
                    continue
                bm = {"id": "@bm:" + str(lp.get("launchPointId")), "title": lp.get("title", url)[:60], "url": url}
                icon_url = lp.get("largeIcon") or lp.get("icon")
                if icon_url:
                    try:
                        async with session.get(icon_url, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as r:
                            if r.status == 200:
                                name = f"icons/bm{len(bookmarks)}.png"
                                icons[name] = await r.read()
                                bm["icon"] = name
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                bookmarks.append(bm)
            for a in apps:
                if a["id"] == "com.webos.app.browser":
                    a["title"] = "Web Browser"
            inputs = [{"id": x.get("id"), "app": x.get("appId"), "label": x.get("label") or x.get("id"),
                       "connected": x.get("connected", False)} for x in c.tv_state.inputs.values()]
            await c.disconnect()
        favs = [{"id": f} for f in cfg["favorites"]]
        return {"apps": apps, "inputs": inputs, "favorites": favs, "bookmarks": bookmarks}, icons

    return asyncio.run(run())


def build_ipk(server_url: str | None = None, baked: tuple[dict, dict[str, bytes]] | None = None) -> Path:
    info = json.loads((APP_DIR / "appinfo.json").read_text())
    app_id, version = info["id"], info["version"]
    app_root = f"usr/palm/applications/{app_id}"
    pkg_root = f"usr/palm/packages/{app_id}"

    files: list[tuple[str, bytes | None]] = [("", None)]
    for d in ("usr", "usr/palm", "usr/palm/applications", app_root, "usr/palm/packages", pkg_root):
        files.append((d, None))
    size = 0
    for f in sorted(APP_DIR.rglob("*")):
        rel = f.relative_to(APP_DIR).as_posix()
        if f.is_dir():
            files.append((f"{app_root}/{rel}", None))
            continue
        if rel == "baked.js" and baked:
            continue  # replaced by the real snapshot below
        if rel == "bg.default.jpg":
            if (APP_DIR / "bg.jpg").exists():
                continue                      # your own background wins
            rel = "bg.jpg"
        data = f.read_bytes()
        if rel == "config.js" and server_url:
            data = f'var TV_SERVER = "{server_url}";\n'.encode()
        files.append((f"{app_root}/{rel}", data))
        size += len(data)
    if baked:
        data, icons = baked
        js = f"var BAKED = {json.dumps(data, ensure_ascii=False)};\n".encode()
        files.append((f"{app_root}/baked.js", js))
        files.append((f"{app_root}/icons", None))
        for name, raw in icons.items():
            files.append((f"{app_root}/{name}", raw))
            size += len(raw)
    pkginfo = {"app": app_id, "id": app_id, "loc_name": info["title"],
               "package_format_version": 2, "vendor": info.get("vendor", ""), "version": version}
    files.append((f"{pkg_root}/packageinfo.json", json.dumps(pkginfo, indent=2).encode()))

    control = (
        f"Package: {app_id}\nVersion: {version}\nSection: misc\nPriority: optional\n"
        f"Architecture: all\nInstalled-Size: {size // 1024 + 1}\n"
        f"Maintainer: N/A <nomail@lge.com>\nDescription: {info['title']}\n"
        "webOS-Package-Format-Version: 2\nwebOS-Packager-Version: x.y.x\n"
    ).encode()
    ipk = _ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", _tar_gz([("", None), ("control", control)])),
        ("data.tar.gz", _tar_gz(files)),
    ])
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{app_id}_{version}_all.ipk"
    out.write_bytes(ipk)
    return out


SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
    # the TV runs an old dropbear that only speaks ssh-rsa
    "-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "ConnectTimeout=8", "-p", "9922",
]


KEY_FILE = ROOT / "data" / "webos_rsa"


def get_key(host: str, passphrase: str | None) -> Path:
    """Reuse the saved Developer Mode key; otherwise fetch it from the TV's key server."""
    if KEY_FILE.exists():
        return KEY_FILE
    if not passphrase:
        raise SystemExit("First install: pass --passphrase (shown in the Developer Mode app on the TV).")
    print("Fetching the Developer Mode key from the TV...")
    try:
        raw = urllib.request.urlopen(f"http://{host}:9991/webos_rsa", timeout=8).read()
    except OSError as err:
        raise SystemExit(
            f"Could not reach the TV's key server ({err}).\n"
            "Open the Developer Mode app on the TV and switch 'Key Server' on."
        ) from err
    tmp = Path(tempfile.mkdtemp()) / "webos_rsa"
    tmp.write_bytes(raw)
    tmp.chmod(0o600)
    r = subprocess.run(["ssh-keygen", "-p", "-P", passphrase, "-N", "", "-f", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Wrong passphrase. It is shown in the Developer Mode app on the TV (case-sensitive).")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(tmp.read_bytes())
    KEY_FILE.chmod(0o600)
    return KEY_FILE


def install(ipk: Path, host: str, passphrase: str | None, app_id: str) -> None:
    key = get_key(host, passphrase)
    target = f"prisoner@{host}"
    remote = f"/media/developer/temp/{ipk.name}"
    ssh = ["ssh", "-i", str(key), *SSH_OPTS, target]
    print("Copying the app to the TV...")
    subprocess.run([*ssh, "mkdir -p /media/developer/temp"], check=True)
    # plain cat over ssh: the TV has no sftp server for modern scp
    with ipk.open("rb") as fh:
        subprocess.run([*ssh, f"cat > {remote}"], stdin=fh, check=True)
    print("Installing...")
    payload = json.dumps({"id": app_id, "ipkUrl": remote, "subscribe": True})
    # luna-send-pub quits as soon as its stdin closes, so keep stdin open with
    # `sleep`, and stop reading once the installer reports a final state.
    cmd = f"sleep 120 | luna-send-pub -i luna://com.webos.appInstallService/dev/install '{payload}'"
    proc = subprocess.Popen([*ssh, cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    result, last = None, ""
    try:
        for line in proc.stdout:
            last = line.strip()
            if '"state":"installed"' in line.replace(" ", ""):
                result = "ok"
                break
            if '"errorCode"' in line or '"state":"install failed"' in line.replace(" ", ""):
                result = "fail"
                break
    finally:
        proc.kill()
    subprocess.run([*ssh, f"rm -f {remote}"])
    if result != "ok":
        raise SystemExit(f"Install did not confirm. Last message from the TV:\n{last[-1500:]}")
    # save the Developer Mode session token so the server can keep it alive
    tok = subprocess.run([*ssh, "cat /var/luna/preferences/devmode_enabled"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    if tok:
        cfg = Config()
        cfg["dev_token"] = tok
        print("Saved the Developer Mode token: the remote server will extend the session daily.")
    print("Installed. Opening Quick Launch on the TV...")
    subprocess.run(
        [*ssh, f"sleep 5 | luna-send-pub -n 1 luna://com.webos.applicationManager/launch '{{\"id\":\"{app_id}\"}}'"],
        capture_output=True, timeout=20,
    )


def bar_position(cfg: Config) -> tuple[int | None, int]:
    """Where Quick Launch sits on LG's home bar (0 = first). Read-only."""
    import aiohttp
    from aiowebostv import WebOsClient

    async def run() -> tuple[int | None, int]:
        async with aiohttp.ClientSession() as session:
            c = WebOsClient(cfg["tv_host"], cfg["client_key"], client_session=session, connect_timeout=15)
            await c.connect()
            lp = (await c.request("com.webos.applicationManager/listLaunchPoints")).get("launchPoints", [])
            await c.disconnect()
        ids = [p.get("id") for p in lp]
        lid = cfg["launcher_app_id"]
        return (ids.index(lid) if lid in ids else None), len(ids)

    return asyncio.run(run())


def main() -> None:
    cfg = Config()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--passphrase", help="shown in the Developer Mode app on the TV")
    p.add_argument("--tv", default=cfg["tv_host"])
    p.add_argument("--server", help="remote server URL baked in as fallback, e.g. http://<phone-or-pc-ip>:8080")
    p.add_argument("--build-only", action="store_true")
    a = p.parse_args()
    baked = None if a.build_only else snapshot_tv(cfg)
    if baked:
        print(f"Read {len(baked[0]['apps'])} apps and {len(baked[1])} icons from the TV")
    ipk = build_ipk(a.server, baked)
    print(f"Built {ipk} ({ipk.stat().st_size} bytes)")
    if a.build_only:
        return
    install(ipk, a.tv, a.passphrase, json.loads((APP_DIR / "appinfo.json").read_text())["id"])
    try:
        pos, total = bar_position(cfg)
        if pos == 0:
            print("Quick Launch is first on LG's home bar: Home, OK opens it.")
        elif pos is not None:
            print(f"Note: Quick Launch is at position {pos + 1} of {total} on LG's home bar. To make Home, OK "
                  "open it: press Home, highlight it, press Up (or hold OK), move it to the first spot, press OK.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
