#!/data/data/com.termux/files/usr/bin/bash
# Installs the TV remote server on this Android phone (Termux).
# Run inside Termux:  curl -s http://<pc-ip>:8080/setup/termux.sh | bash
set -e
SRC="__SERVER__"
echo "== TV remote setup =="

if python -c "import aiowebostv" 2>/dev/null; then
  echo "Packages already installed: updating the remote only."
else
# Full upgrade first. A partial upgrade leaves libraries out of sync
# (e.g. curl linked against a newer openssl than the one installed).
echo "Updating Termux packages (can take a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
apt-get update
apt-get "${APT_OPTS[@]}" full-upgrade
apt-get "${APT_OPTS[@]}" install python

# aiohttp & friends have no prebuilt Android wheels: use their pure-Python
# versions so no compiler is needed. Fast enough for a remote control.
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 \
       FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
echo "Installing Python packages (1-3 min)..."
pip install -q --no-cache-dir --no-binary aiohttp,multidict,yarl,frozenlist,propcache \
    "aiohttp>=3.11" "aiowebostv>=1.0"
fi

# download with Python, not curl, so a broken curl can't stop us
echo "Downloading the remote..."
mkdir -p "$HOME/tvremote"
python - "$SRC/setup/bundle.tar.gz" "$HOME/tvremote" <<'PY'
import io, json, os, sys, tarfile, urllib.request
dest = sys.argv[2]
state = os.path.join(dest, "data", "state.json")
old = json.load(open(state)) if os.path.exists(state) else None
data = urllib.request.urlopen(sys.argv[1], timeout=30).read()
tarfile.open(fileobj=io.BytesIO(data)).extractall(dest)
if old:  # keep this phone's pairing key and pinned apps
    new = json.load(open(state))
    new.update({k: v for k, v in old.items() if v is not None and k != "dev_token"})
    json.dump(new, open(state, "w"), indent=2)
PY

# stop an older server so the new code is used
for p in /proc/[0-9]*; do
  c="$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)"
  case "$c" in *"while true; do python -m tvremote"*|"python -m tvremote ") kill "${p#/proc/}" 2>/dev/null;; esac
done
true

# `tvremote` command: start the server (if not running) and open the remote
cat > "$PREFIX/bin/tvremote" <<'EOS'
#!/data/data/com.termux/files/usr/bin/bash
# Start the TV remote server (kept alive by a restart loop) and open the remote.
up() { (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; }
cd "$HOME/tvremote"
if ! up; then
  termux-wake-lock 2>/dev/null || true      # keeps the CPU awake: Termux shows a notification
  nohup bash -c 'while true; do python -m tvremote >> "$HOME/tvremote/server.log" 2>&1; sleep 2; done' \
    > /dev/null 2>&1 &
  for i in $(seq 1 40); do up && break; sleep 0.3; done
  up || { echo "Server did not start. Log:"; tail -20 "$HOME/tvremote/server.log"; exit 1; }
fi
[ "$1" = "--no-open" ] || termux-open-url http://127.0.0.1:8080
EOS
chmod +x "$PREFIX/bin/tvremote"

# start automatically when the phone boots (needs the Termux:Boot app)
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-tvremote" <<'EOB'
#!/data/data/com.termux/files/usr/bin/sh
# Run by Termux:Boot after the phone starts. Leaves a line in boot.log each time.
termux-wake-lock
echo "boot script ran: $(date)" >> "$HOME/tvremote/boot.log"
"$PREFIX/bin/tvremote" --no-open >> "$HOME/tvremote/boot.log" 2>&1
EOB
chmod +x "$HOME/.termux/boot/start-tvremote"

echo
echo "Done. Starting the remote..."
tvremote
echo "If the TV shows a pairing prompt, press Accept."
echo "Next time, just type:  tvremote"
echo
echo "--- checks ---"
echo "Termux build: $(termux-info 2>/dev/null | grep -io 'APK_RELEASE=[A-Z_]*' | head -1)"
ls -la "$HOME/.termux/boot/"
tail -3 "$HOME/tvremote/boot.log" 2>/dev/null || echo "boot.log: no boot run recorded yet"
