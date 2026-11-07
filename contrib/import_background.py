"""Turn any picture into the launcher background (tvremote/tv/bg.jpg).

    python3 contrib/import_background.py "image copy.png" [--dim 0.45]

Scales/crops to 1920x1080, darkens it so the UI stays readable, and saves a
small JPEG: the TV decodes it once at launch, so size matters for start-up.
"""
import argparse, base64, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("--dim", type=float, default=0.45, help="0 = untouched, 1 = black")
ap.add_argument("--rotate", choices=["0", "ccw", "cw", "180"], default="0", help="turn the picture first")
ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "tvremote" / "tv" / "bg.jpg"))
a = ap.parse_args()

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / ("src" + Path(a.image).suffix.lower())
    shutil.copy(a.image, src)
    page = Path(tmp) / "render.html"
    page.write_text(f"""<!doctype html><body><canvas id=c width=1920 height=1080></canvas><pre id=o></pre><script>
var img = new Image();
img.onload = function () {{
  // optional rotation into an intermediate canvas
  var rot = "{a.rotate}", side = rot === "ccw" || rot === "cw";
  var r = document.createElement("canvas");
  r.width = side ? img.height : img.width; r.height = side ? img.width : img.height;
  var rx = r.getContext("2d");
  if (rot === "ccw") {{ rx.translate(0, img.width); rx.rotate(-Math.PI / 2); }}
  if (rot === "cw") {{ rx.translate(img.height, 0); rx.rotate(Math.PI / 2); }}
  if (rot === "180") {{ rx.translate(img.width, img.height); rx.rotate(Math.PI); }}
  rx.drawImage(img, 0, 0);
  var c = document.getElementById("c"), x = c.getContext("2d"), W = 1920, H = 1080;
  x.imageSmoothingQuality = "high";
  var s = Math.max(W / r.width, H / r.height), w = r.width * s, h = r.height * s;
  x.drawImage(r, (W - w) / 2, (H - h) / 2, w, h);
  x.fillStyle = "rgba(6,4,8,{a.dim})"; x.fillRect(0, 0, W, H);
  // extra shade where the panel text sits (left/top), lighter on the right
  var g = x.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, "rgba(0,0,0,0.30)"); g.addColorStop(0.55, "rgba(0,0,0,0.10)"); g.addColorStop(1, "rgba(0,0,0,0)");
  x.fillStyle = g; x.fillRect(0, 0, W, H);
  document.getElementById("o").textContent = c.toDataURL("image/jpeg", 0.85);
}};
img.src = "{src.name}";
</script></body>""")
    dom = subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
                          "--allow-file-access-from-files", f"--user-data-dir={tmp}/prof",
                          "--virtual-time-budget=8000", "--dump-dom", page.as_uri()],
                         capture_output=True, text=True, timeout=90).stdout
m = re.search(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", dom)
if not m:
    sys.exit("render failed")
Path(a.out).write_bytes(base64.b64decode(m.group(1)))
print(f"wrote {a.out} ({Path(a.out).stat().st_size // 1024} KB)")
