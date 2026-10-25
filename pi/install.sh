#!/usr/bin/env bash
# Brand Intel installer for Raspberry Pi OS (64-bit), Pi 4 or Pi 5.
#
#   git clone https://github.com/Sweitzerc/brand-intel.git
#   cd brand-intel && bash pi/install.sh
#
# Safe to re-run: it updates the code and restarts the services.
#   --lite   skip the AI models (fast keyword scoring; use on a Pi 4 or smaller)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
LITE=0
[[ "${1:-}" == "--lite" ]] && LITE=1

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This needs 64-bit Raspberry Pi OS (uname -m says $(uname -m))." >&2
  exit 1
fi

say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev git

say "Updating code"
git -C "$APP_DIR" pull --ff-only || echo "(could not pull - keeping current code)"

say "Creating Python environment"
python3 -m venv "$APP_DIR/.venv"
PY="$APP_DIR/.venv/bin/python"
"$PY" -m pip install -q --upgrade pip
if [[ $LITE == 0 ]]; then
  # The CPU-only PyTorch build is much smaller than the default one.
  "$PY" -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
  "$PY" -m pip install -q -r "$APP_DIR/requirements.txt"
else
  grep -vE '^(torch|transformers)' "$APP_DIR/requirements.txt" | "$PY" -m pip install -q -r /dev/stdin
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  say "Creating settings file .env"
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi
sed -i "s/^BRAND_INTEL_LITE=.*/BRAND_INTEL_LITE=$LITE/" "$APP_DIR/.env"

if [[ $LITE == 0 ]]; then
  say "Downloading AI models (one time, a few minutes)"
  (cd "$APP_DIR" && "$PY" -c "from brand_intel.analyzers import sentiment, topics; sentiment._model(); topics._model()")
fi

say "Installing services"
sudo tee /etc/systemd/system/brand-intel.service >/dev/null <<UNIT
[Unit]
Description=Brand Intel dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=BRAND_INTEL_DB=$APP_DIR/data/brand_intel.db
ExecStart=$PY app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/brand-intel-monitor.service >/dev/null <<UNIT
[Unit]
Description=Brand Intel daily scan and email alerts
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=BRAND_INTEL_DB=$APP_DIR/data/brand_intel.db
ExecStart=$PY -m brand_intel.monitor
UNIT

sudo tee /etc/systemd/system/brand-intel-monitor.timer >/dev/null <<UNIT
[Unit]
Description=Run Brand Intel monitoring every morning

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now brand-intel.service brand-intel-monitor.timer
sudo systemctl restart brand-intel.service

PORT="$(grep -E '^GRADIO_SERVER_PORT=' "$APP_DIR/.env" | cut -d= -f2)"
say "Done"
echo "Dashboard:  http://$(hostname).local:${PORT:-7860}   (or http://$(hostname -I | awk '{print $1}'):${PORT:-7860})"
echo "Settings:   nano $APP_DIR/.env      then: sudo systemctl restart brand-intel"
echo "Test email: $PY -m brand_intel.monitor --dry-run"
