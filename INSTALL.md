# Installation & Usage

Full-stack options portal (FastAPI backend + React/Vite frontend) plus a
standalone **expiry-day short-strangle** automation script.

- [1. Prerequisites](#1-prerequisites)
- [2. Clone](#2-clone)
- [3. Python environment](#3-python-environment)
- [4. Playwright browser](#4-playwright-browser-required)
- [5. Credentials (`.env`)](#5-credentials-env)
- [6. Frontend build](#6-frontend-build)
- [7. Run the web server](#7-run-the-web-server)
- [8. Strangle automation](#8-strangle-automation)
- [9. Telegram notifications](#9-telegram-notifications)

---

## 1. Prerequisites

- **Python 3.10+** (uses `zoneinfo`, `X | None`, `dict[str, str]`)
- **Node.js 18+** and npm (frontend is Vite 6 / React 18)
- Git, and outbound network to `api.shoonya.com` / Upstox / Telegram
- Broker accounts: **Shoonya** (order placement) and **Upstox** (market data)

## 2. Clone

```bash
git clone git@github.com:gowthamparuchuru/options-portal.git
cd options-portal
```

## 3. Python environment

`server.sh` expects a venv named `venv/` at the project root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Playwright browser (required)

Both brokers authenticate through a headless Chromium OAuth flow, so the pip
package alone is not enough — install the browser binary:

```bash
playwright install chromium
playwright install-deps chromium   # Linux only: system libraries
```

## 5. Credentials (`.env`)

Copy the template and fill in real values:

```bash
cp .env.example .env
```

`backend/config.py` **fails hard** if any Shoonya or Upstox value is missing or
still starts with `your_`. Telegram is optional (see section 9). See
`.env.example` for the exact keys.

## 6. Frontend build

The backend serves `frontend/dist` when it exists:

```bash
cd frontend
npm install
npm run build          # outputs frontend/dist
cd ..
```

For UI development use `npm run dev` (Vite dev server) instead of a build.

## 7. Run the web server

```bash
./server.sh start      # activates venv + runs run.py
# or: python run.py
```

Serves on **`http://0.0.0.0:8111`**. For a remote VPS, tunnel it:

```bash
ssh -L 8111:localhost:8111 gowtham@astro-prod
```

`./server.sh build` runs the pip install + frontend build in one shot, but it
does **not** create the venv or install Playwright — do those (steps 3–4) once
by hand first.

---

## 8. Strangle automation

Standalone script that, **only on an index's expiry day**, sells a short
strangle (one OTM CE + one OTM PE) at a configured time. No web server needed —
it reuses the same venv and `.env`.

### How it works

1. Logs in to Shoonya (orders) and Upstox (data).
2. Detects which enabled index expires **today**, honouring `index_priority`
   (NIFTY wins over SENSEX when both expire the same day). Exits cleanly if
   none expire today.
3. Sleeps until that index's `trigger_time` (unless `--now`). Aborts if started
   more than `max_late_secs` after the trigger.
4. Fetches spot, scans the option chain, and picks the **furthest-OTM CE and PE
   strikes whose premium is still just above `premium_threshold`**.
5. SELLs both legs with an LTP-chasing limit order (`LTP+0.10 → +0.05 → LTP`).
6. Logs the outcome and exits. **No monitoring / stop-loss / square-off.**

### Configuration — `strangle_config.json`

Per-index tunables live in `strangle_config.json` at the project root:

| Key | Meaning |
|-----|---------|
| `timezone` | Timezone for the trigger-time wait (default `Asia/Kolkata`) |
| `max_late_secs` | Abort if launched more than this many seconds past the trigger |
| `index_priority` | Order to resolve ties when multiple indices expire the same day |
| `indices.<IDX>.enabled` | Include this index in expiry detection |
| `indices.<IDX>.trigger_time` | `HH:MM` (in `timezone`) to place the strangle |
| `indices.<IDX>.premium_threshold` | Sell the furthest-OTM strike whose premium is still above this |
| `indices.<IDX>.lots` | Number of lots per leg |
| `indices.<IDX>.lot_size` | Contract lot size (order qty = `lots × lot_size`) |
| `indices.<IDX>.product_type` | Shoonya product code (`M` = NRML) |
| `indices.<IDX>.scan_range_pct` | Chain scan window around spot, percent |

> Verify `SENSEX.lot_size` against the current BSE contract before enabling it —
> it defaults to `20`.

### Running it

```bash
source venv/bin/activate

# Safe end-to-end check — selects strikes and logs intended orders, places NOTHING:
python -m backend.strategies.strangle --dry-run --now

# Force a specific index (skips expiry detection) for testing:
python -m backend.strategies.strangle --dry-run --now --index NIFTY

# LIVE, wait until the configured trigger_time (this is what cron runs):
python -m backend.strategies.strangle
```

CLI flags:

| Flag | Effect |
|------|--------|
| `--dry-run` | Select strikes and log; place no orders |
| `--now` | Skip the wait and fire immediately |
| `--index NIFTY\|SENSEX` | Force an index, bypassing expiry detection |
| `--config PATH` | Path to `strangle_config.json` (default: project root) |

Exit codes: `0` success / no-op, `2` login/config failure, `3` past trigger
window, `4` lost expiry, `5` no legs prepared, `6` a leg failed.

### Scheduling on the VPS (cron)

The script self-verifies the real expiry and no-ops on non-expiry days, so it is
safe to launch every weekday and let it decide — this also survives any exchange
change to the expiry weekday.

```bash
# 1. Set the machine timezone to IST (so cron times = IST):
sudo timedatectl set-timezone Asia/Kolkata

# 2. Provision the venv on the VPS once (pip + Playwright — cron won't do this):
cd ~/options-portal && source venv/bin/activate \
  && pip install -r requirements.txt \
  && playwright install chromium && playwright install-deps chromium \
  && deactivate

# 3. Create the log dir and install the cron job:
mkdir -p ~/options-portal/logs
crontab -e
```

Add (use your absolute paths):

```cron
CRON_TZ=Asia/Kolkata
# Every weekday 09:00 IST; the script detects expiry, waits to trigger_time (09:16),
# then places the strangle. Remove --dry-run to go LIVE.
0 9 * * 1-5  cd /home/gowtham/options-portal && ./venv/bin/python -m backend.strategies.strangle --dry-run >> logs/strangle.log 2>&1
```

Verify and watch:

```bash
crontab -l
tail -f ~/options-portal/logs/strangle.log
```

> The cron line above includes `--dry-run` for safety. Remove it to place real
> orders once you've confirmed the behaviour.

---

## 9. Telegram notifications

Optional. When both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in
`.env`, the strangle script sends alerts on: startup, broker login status,
selected strikes, each leg (trade taken / failed / not filled), and no-op days.
If either is unset, notifications are silently disabled.

### Get the two values

1. Create a bot with [@BotFather](https://t.me/BotFather) → copy the **token**.
2. Open your bot in Telegram and tap **Start** (bots cannot message you first).
3. Fetch your **chat id** (replace `<TOKEN>`):

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" \
   | python3 -c "import sys,json; d=json.load(sys.stdin); print([u['message']['chat']['id'] for u in d['result'] if 'message' in u])"
   ```

   Personal chat ids are positive; group/supergroup ids are negative (keep the
   minus sign). For a group, add the bot to it and send a message there first.
4. Test delivery:

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/sendMessage" \
     -d chat_id=<CHAT_ID> -d text="strangle test"
   ```

Then set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
