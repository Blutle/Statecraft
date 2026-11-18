# Running Statecraft on a DigitalOcean droplet

Commands marked **(PC)** go in PowerShell on your Windows PC; **(droplet)** ones go in the SSH session.
Replace `IP` with your droplet's IP address (on its page in DigitalOcean).

## First-time setup

1. **(PC)** Connect: `ssh root@IP`. Say `yes` to the fingerprint question the first time.
2. **(droplet)** Get the code and run the setup script:
   ```
   apt-get update && apt-get install -y git
   git clone https://github.com/Blutle/Statecraft.git /opt/statecraft
   bash /opt/statecraft/deploy/setup.sh
   ```
   If the repo is private, `git clone` asks for a username and password: use your GitHub username and a
   personal access token (GitHub → Settings → Developer settings → Personal access tokens), not your password.
   The script installs Python, the bot's packages, and fonts, runs the tests, and installs the service.
3. **(PC)** **Stop the bot on your PC** (close the `run_bot.bat` window). Only one copy may run at a time.
4. **(PC)** From `C:\Projects\StrategyBot`, copy the secrets and the current game up:
   ```
   scp .env game.json server.json game.history.jsonl root@IP:/opt/statecraft/
   ```
5. **(droplet)** Lock down the files and start the bot:
   ```
   chown statecraft:statecraft /opt/statecraft/.env /opt/statecraft/*.json /opt/statecraft/*.jsonl
   chmod 600 /opt/statecraft/.env
   systemctl start statecraft
   journalctl -u statecraft -f
   ```
   The last command shows the live log. Look for `Logged in as ...`, then press Ctrl+C to stop watching
   (the bot keeps running).

## Day to day

| What | Command (droplet) |
|---|---|
| Is it running? | `systemctl status statecraft` |
| Live log | `journalctl -u statecraft -f` |
| Last 100 log lines | `journalctl -u statecraft -n 100` |
| Restart | `systemctl restart statecraft` |
| Stop | `systemctl stop statecraft` |
| Get the latest code and restart | `bash /opt/statecraft/deploy/update.sh` |

The bot starts itself after a crash or a droplet reboot.

## Backups

Every night at 4:15 AM Central, the game files are copied to `/opt/statecraft/backups/` (kept 30 days).
To pull the latest copy down to your PC: **(PC)** `scp -r root@IP:/opt/statecraft/backups ./droplet-backups`

Those backups live on the same droplet. For protection against losing the droplet itself, turn on
DigitalOcean's weekly backups on the droplet's Backups tab (about 20% of the droplet price).

## Running Statecraft inside someone else's server

The bot can live quietly in a shared server: it only touches its own roles and categories, and only pings
players. In `/opt/statecraft/.env`:

```
GUILD_ID=<the other server's ID>
CATEGORY_PLACEMENT=bottom        # Info, players' categories, then the game channels, after everything else
ANNOUNCEMENTS_CHANNEL=           # blank: Statecraft gets its own #announcements (won't touch theirs)
PLAYERS_ONLY=1                   # non-players see only Info (to read and /join), not the game channels
```

Then: invite the bot to that server with the same permissions as before (it needs Manage Channels and Manage
Roles), `systemctl restart statecraft`, and run `/admin setup` there. Start that game fresh: `server.json`
holds the old server's channel IDs, so move it (and `game.json`) aside first, e.g.
`mv /opt/statecraft/server.json /opt/statecraft/server.old.json`. One bot runs one server at a time; to run two
at once, set up a second bot application and a second copy in its own folder.

## Moving the game back to your PC

Stop the droplet bot first (`systemctl stop statecraft`), then **(PC)**
`scp root@IP:/opt/statecraft/{game.json,server.json,game.history.jsonl} .` and run `run_bot.bat`.
