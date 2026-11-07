"""Render contrib/make_background.html to the default launcher background (headless Chrome)."""
import base64, re, subprocess, sys, tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
out = here.parent / "tvremote" / "tv" / "bg.default.jpg"
with tempfile.TemporaryDirectory() as prof:
    dom = subprocess.run(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={prof}",
         "--virtual-time-budget=5000", "--dump-dom", (here / "make_background.html").as_uri()],
        capture_output=True, text=True, timeout=60).stdout
m = re.search(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", dom)
if not m:
    sys.exit("render failed")
out.write_bytes(base64.b64decode(m.group(1)))
print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
