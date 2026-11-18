#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the bot. Run as root: bash /opt/statecraft/deploy/update.sh
set -euo pipefail
APP=/opt/statecraft
sudo -u statecraft git -C "$APP" pull --ff-only
sudo -u statecraft "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
(cd "$APP" && sudo -u statecraft "$APP/.venv/bin/python" -m unittest discover -s tests -q)
systemctl restart statecraft
sleep 5
systemctl --no-pager status statecraft | head -n 5
