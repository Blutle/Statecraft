#!/usr/bin/env bash
# One-time setup of the Statecraft bot on a fresh Ubuntu server (e.g. a DigitalOcean droplet).
# Run as root:   bash setup.sh
# Safe to re-run: it updates what's already there.
set -euo pipefail

REPO="https://github.com/Blutle/Statecraft.git"
APP=/opt/statecraft
USER_NAME=statecraft

echo "== Installing system packages"
apt-get update -q
apt-get install -y -q git python3 python3-venv python3-pip fonts-dejavu-core

echo "== Setting the server clock to Central (only affects logs; the game has its own timezone setting)"
timedatectl set-timezone America/Chicago || true

echo "== Creating the '$USER_NAME' user (the bot never runs as root)"
id "$USER_NAME" &>/dev/null || useradd --system --create-home --home-dir /home/$USER_NAME --shell /usr/sbin/nologin "$USER_NAME"

echo "== Getting the code"
if [ ! -d "$APP/.git" ]; then
    git clone "$REPO" "$APP"
fi
chown -R "$USER_NAME:$USER_NAME" "$APP"
sudo -u "$USER_NAME" git -C "$APP" pull --ff-only

echo "== Installing Python packages"
sudo -u "$USER_NAME" python3 -m venv "$APP/.venv"
sudo -u "$USER_NAME" "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "== Running the tests"
(cd "$APP" && sudo -u "$USER_NAME" "$APP/.venv/bin/python" -m unittest discover -s tests -q)

echo "== Installing the service and nightly backups"
install -m 644 "$APP/deploy/statecraft.service" /etc/systemd/system/statecraft.service
install -m 755 "$APP/deploy/backup.sh" /usr/local/bin/statecraft-backup
echo "15 4 * * * $USER_NAME /usr/local/bin/statecraft-backup" > /etc/cron.d/statecraft-backup
systemctl daemon-reload
systemctl enable statecraft >/dev/null

echo
if [ -f "$APP/.env" ]; then
    echo "Setup done. .env is present: start the bot with   systemctl restart statecraft"
else
    echo "Setup done. Next: copy your .env (and game.json / server.json) into $APP, then run:"
    echo "    chown $USER_NAME:$USER_NAME $APP/.env $APP/*.json; chmod 600 $APP/.env"
    echo "    systemctl start statecraft"
fi
