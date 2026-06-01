#!/usr/bin/env bash
#
# Jones Big Ass Weather Dashboard — installer for Ubuntu Server / Debian.
#
# What this does:
#   * Installs apt packages: python3, python3-venv, sqlite3, ufw, curl.
#   * Creates a venv at server/.venv and installs the FastAPI server in
#     editable mode with --extra-index-url disabled (uses defaults).
#   * Drops a systemd unit at /etc/systemd/system/weather-server.service
#     so the server runs at boot, restarts on failure, logs to journald.
#   * Opens the configured TCP port in UFW and leaves everything else closed.
#
# What this DOES NOT do (intentionally — out of scope per CLAUDE.md):
#   * No iptables redirects. Dashboard binds directly to the configured port.
#   * No MariaDB / MySQL. Storage is SQLite (single file under server/).
#   * No network configuration (netplan / static IP). DHCP works fine;
#     pin the host in your router if you want a stable address.
#   * No ESP32 sketch flashing. Use arduino-cli or the Arduino IDE.
#
# Port resolution:
#   server/weather.toml is the single source of truth for the port. The
#   systemd unit runs the `weather-server` console script which reads
#   port from that file at startup. install.sh uses the same value for
#   the UFW rule and the "open this URL" message.
#
#   On first install (no weather.toml exists yet) the script seeds the
#   file from weather.toml.example. The --port flag overrides the seeded
#   value. On re-install, the existing file is left alone; pass --port
#   to also rewrite it.
#
# Optional flags:
#   --port N           Use TCP port N instead of the default 8005. Writes
#                      the value into weather.toml so the service and UFW
#                      stay in sync.
#   --with-widget      Also install GTK system packages for the tray
#                      widget (python3-gi, gir1.2-appindicator3-0.1, ...).
#   --no-systemd       Skip the systemd unit (useful for dev environments
#                      where you'd rather run `make dev` by hand).
#   --no-firewall      Skip the UFW step (leaves the firewall untouched).
#   --no-start         Install everything but don't start the service
#                      yet. Handy when you still need to edit weather.toml.
#
# Usage:
#   git clone https://github.com/kbennett2000/weather-station-public.git
#   cd weather-station-public
#   sudo ./install.sh                       # server only, default port 8005
#   sudo ./install.sh --port 9000           # bind to 9000 instead
#   sudo ./install.sh --with-widget         # server + widget deps
#
# This script is idempotent: re-running it on an already-installed host
# is safe and will reconcile any drift (apt, venv, unit file, ufw rule).

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────

WITH_WIDGET=false
DO_SYSTEMD=true
DO_FIREWALL=true
DO_START=true
PORT_OVERRIDE=""

while (( $# )); do
    case "$1" in
        --with-widget)  WITH_WIDGET=true; shift ;;
        --no-systemd)   DO_SYSTEMD=false; shift ;;
        --no-firewall)  DO_FIREWALL=false; shift ;;
        --no-start)     DO_START=false; shift ;;
        --port)
            shift
            if [[ $# -eq 0 || ! "$1" =~ ^[0-9]+$ ]]; then
                echo "--port requires a numeric value (1-65535)" >&2
                exit 2
            fi
            PORT_OVERRIDE="$1"
            shift
            ;;
        --port=*)
            PORT_OVERRIDE="${1#--port=}"
            if [[ ! "$PORT_OVERRIDE" =~ ^[0-9]+$ ]]; then
                echo "--port requires a numeric value (1-65535)" >&2
                exit 2
            fi
            shift
            ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \{0,1\}//; /^set -/d'
            exit 0
            ;;
        *)
            echo "unknown flag: $1" >&2
            echo "see: $0 --help" >&2
            exit 2
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────
# Pre-flight: must be root, must know which user owns the install
# ──────────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "install.sh must be run as root (use sudo)." >&2
    exit 1
fi

if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == "root" ]]; then
    cat >&2 <<EOF
install.sh expected to be invoked via 'sudo' from a regular user
account, so it can derive ownership of the venv, repo, and systemd
unit from \$SUDO_USER. Got: SUDO_USER='${SUDO_USER:-<unset>}'.

If you're logged in as root, log out and run again as a non-root user:
    sudo ./install.sh
EOF
    exit 1
fi

INSTALL_USER="$SUDO_USER"
INSTALL_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/server/.venv"
UNIT_PATH="/etc/systemd/system/weather-server.service"

echo "==> Install user: $INSTALL_USER (home: $INSTALL_HOME)"
echo "==> Repo dir: $REPO_DIR"
echo "==> Venv: $VENV_DIR"

# ──────────────────────────────────────────────────────────────────────
# apt packages
# ──────────────────────────────────────────────────────────────────────

echo "==> Installing system packages…"
apt-get update
apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    sqlite3 \
    curl \
    ufw

if $WITH_WIDGET; then
    echo "==> Installing GTK widget dependencies…"
    apt-get install -y \
        python3-gi \
        gir1.2-gtk-3.0 \
        gir1.2-appindicator3-0.1 \
        python3-requests
fi

# ──────────────────────────────────────────────────────────────────────
# Python venv + server install
# ──────────────────────────────────────────────────────────────────────

if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating venv at $VENV_DIR…"
    sudo -u "$INSTALL_USER" python3 -m venv "$VENV_DIR"
fi

echo "==> Installing server (editable) into venv…"
sudo -u "$INSTALL_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$INSTALL_USER" "$VENV_DIR/bin/pip" install -e "$REPO_DIR/server"

# ──────────────────────────────────────────────────────────────────────
# Config: copy weather.toml.example if no live config exists yet
# ──────────────────────────────────────────────────────────────────────

if [[ ! -f "$REPO_DIR/server/weather.toml" ]]; then
    echo "==> Seeding server/weather.toml from weather.toml.example…"
    sudo -u "$INSTALL_USER" cp \
        "$REPO_DIR/server/weather.toml.example" \
        "$REPO_DIR/server/weather.toml"
    SEEDED_SERVER_CONFIG=true
else
    echo "==> server/weather.toml already exists, leaving it alone."
    SEEDED_SERVER_CONFIG=false
fi

# If --port was passed, rewrite [server] port in weather.toml so the
# systemd unit and UFW agree with the file. We do this even on re-runs
# because that's the documented behavior — pass --port to change it.
if [[ -n "$PORT_OVERRIDE" ]]; then
    echo "==> Setting [server] port = $PORT_OVERRIDE in server/weather.toml…"
    sudo -u "$INSTALL_USER" python3 - "$REPO_DIR/server/weather.toml" "$PORT_OVERRIDE" <<'PY'
"""Replace the `port = ...` line under [server] without touching the
rest of the file. We do a string rewrite rather than a full TOML
round-trip so user comments and ordering are preserved."""
import re, sys
path, port = sys.argv[1], int(sys.argv[2])
text = open(path).read()
# Anchor inside [server] section: replace the first `port = N` between
# `[server]` and the next `[…]` heading.
new_text, n = re.subn(
    r"(\[server\][^\[]*?\bport\s*=\s*)\d+",
    lambda m: f"{m.group(1)}{port}",
    text,
    count=1,
    flags=re.DOTALL,
)
if n == 0:
    print(f"warning: couldn't find [server] port to rewrite in {path}; appending one")
    new_text = text.rstrip() + f"\n\n# Added by install.sh --port\n[server]\nport = {port}\n"
open(path, "w").write(new_text)
PY
fi

# Read the resolved port back out of weather.toml — this is the value
# used for the UFW rule and the final URL message. The systemd unit
# also reads it (via the weather-server console script).
RESOLVED_PORT="$(python3 - "$REPO_DIR/server/weather.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    cfg = tomllib.load(f)
print(cfg.get("server", {}).get("port", 8005))
PY
)"
echo "==> Resolved port from weather.toml: $RESOLVED_PORT"

if [[ ! -f "$REPO_DIR/branding.toml" ]]; then
    echo "==> Seeding branding.toml from branding.toml.example…"
    sudo -u "$INSTALL_USER" cp \
        "$REPO_DIR/branding.toml.example" \
        "$REPO_DIR/branding.toml"
else
    echo "==> branding.toml already exists, leaving it alone."
fi

if $WITH_WIDGET && [[ ! -f "$REPO_DIR/widget/config.toml" ]]; then
    echo "==> Seeding widget/config.toml from config.toml.example…"
    sudo -u "$INSTALL_USER" cp \
        "$REPO_DIR/widget/config.toml.example" \
        "$REPO_DIR/widget/config.toml"
fi

# ──────────────────────────────────────────────────────────────────────
# systemd unit
# ──────────────────────────────────────────────────────────────────────

if $DO_SYSTEMD; then
    echo "==> Writing $UNIT_PATH…"
    cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Jones Big Ass Weather Dashboard (FastAPI)
Documentation=https://github.com/kbennett2000/weather-station-public
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=$INSTALL_USER
Group=$INSTALL_USER
WorkingDirectory=$REPO_DIR/server
ExecStart=$VENV_DIR/bin/weather-server
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Lock down a few things — server only needs to read repo files and
# write its SQLite db under server/. No network privilege escalation,
# no /tmp, no kernel modules.
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$REPO_DIR/server
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable weather-server.service >/dev/null

    if $DO_START; then
        echo "==> Starting weather-server.service…"
        systemctl restart weather-server.service
        sleep 2
        systemctl --no-pager --lines=10 status weather-server.service || true
    else
        echo "==> --no-start: leaving weather-server.service stopped."
        echo "    Edit $REPO_DIR/server/weather.toml, then:"
        echo "        sudo systemctl start weather-server.service"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# Firewall
# ──────────────────────────────────────────────────────────────────────

if $DO_FIREWALL; then
    echo "==> Configuring UFW (allow ssh + $RESOLVED_PORT/tcp, deny everything else)…"
    ufw --force enable >/dev/null
    ufw allow ssh >/dev/null
    ufw allow "$RESOLVED_PORT/tcp" >/dev/null
    ufw reload >/dev/null
    ufw status verbose | sed 's/^/    /'
fi

# ──────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────

cat <<EOF

✔ Install complete.

Next steps:
  1. Edit $REPO_DIR/server/weather.toml — set the IPs and
     fallback_lat / fallback_lon for your sensors. The example file
     uses fixture mode for offline testing; comment out the
     [development] block to poll real ESP32s.
  1b. Edit $REPO_DIR/branding.toml to fill in the [BRANDING] slots
     on the dashboard (taglines, footer, offline-state copy, etc.).
     Optional but recommended.
EOF

if $DO_SYSTEMD; then
cat <<EOF
  2. Restart the service so it picks up your edits:
        sudo systemctl restart weather-server.service
     Tail logs with:
        journalctl -u weather-server.service -f
EOF
fi

cat <<EOF
  3. Open http://<this-host>:$RESOLVED_PORT in a browser. Dashboard should
     load and (if real sensors are reachable) show live data within ~30s.

EOF

if $WITH_WIDGET; then
cat <<EOF
Widget (optional):
  * Run 'make widget' from $REPO_DIR, or add 'python3 widget/weather_tray.py'
    to your desktop autostart. Uses the SYSTEM python so it can find gi.
  * Edit $REPO_DIR/widget/config.toml to point at this host's URL.

EOF
fi

if $SEEDED_SERVER_CONFIG; then
    echo "Reminder: server/weather.toml was just created from the example."
    echo "You almost certainly want to edit it before considering this done."
fi

# Loud banner if the resolved weather.toml has fixture mode active —
# the #1 thing new installs miss. Skip if [development] is gone or
# fixture_dir is null.
FIXTURE_ACTIVE="$(python3 - "$REPO_DIR/server/weather.toml" <<'PY'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as f:
        cfg = tomllib.load(f)
    print("yes" if cfg.get("development", {}).get("fixture_dir") else "no")
except Exception:
    print("no")
PY
)"

if [[ "$FIXTURE_ACTIVE" == "yes" ]]; then
cat <<'EOF'

══════════════════════════════════════════════════════════════════════
⚠  FIXTURE MODE IS ACTIVE — the dashboard will show DUMMY DATA, not
   live sensor readings. This is the #1 thing new installs miss.

   To switch to real polling:
     1. Edit server/weather.toml
     2. Comment out the entire [development] block (two lines)
     3. Confirm each [[sensors]] block's `ip = "..."` matches your
        actual sensor on the LAN
     4. sudo systemctl restart weather-server.service
        (or Ctrl-C + restart if you're using `make dev`)

   You'll know it worked when /api/v1/current returns your real
   outdoor temperature instead of 64.2°F.
══════════════════════════════════════════════════════════════════════

EOF
fi
