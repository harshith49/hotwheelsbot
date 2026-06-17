# 🏎️ Hot Wheels Alert Bot

Real-time stock monitoring for Hot Wheels on **Blinkit** with instant Pushover notifications.

## How It Works

Uses **Playwright browser automation** (not raw HTTP requests) to bypass anti-bot protections. The bot:

1. Launches a headless Chromium browser
2. Navigates to Blinkit's search page
3. Intercepts the real API responses the browser receives
4. Parses product data (name, price, stock status)
5. Sends Pushover alerts for in-stock items
6. Loops every 60 seconds

## Quick Start (Local)

```bash
# Install dependencies
pip install -r requirements.txt

# Install Chromium for Playwright
playwright install chromium

# Run (dry-run mode — no alerts)
DRY_RUN=true python bot.py

# Run for real
python bot.py

# Run with visible browser (for debugging)
HEADLESS=false python bot.py
```

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.com](https://railway.com) → New Project → Deploy from GitHub
3. Add these **environment variables** in Railway:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PUSHOVER_USER_KEY` | ✅ | — | Your Pushover user key |
| `PUSHOVER_APP_TOKEN` | ✅ | — | Your Pushover app token |
| `LATITUDE` | ✅ | `12.924674` | Your latitude |
| `LONGITUDE` | ✅ | `77.694803` | Your longitude |
| `PINCODE` | — | `560066` | Nearest pincode |
| `AREA_NAME` | — | `Bellandur, Bangalore` | Area name for Blinkit |
| `SCAN_INTERVAL` | — | `60` | Seconds between scans |
| `SEARCH_QUERY` | — | `hot wheels` | What to search for |
| `HEADLESS` | — | `true` | `true` for cloud, `false` for local debugging |
| `DRY_RUN` | — | `false` | Log alerts without sending |

4. Deploy! Railway will build the Docker image and start scanning.

## Files

```
bot.py              ← Everything: scraper + alerts + loop (single file)
requirements.txt    ← Python dependencies
Dockerfile          ← Docker build (Railway uses this)
railway.toml        ← Railway deploy config
.gitignore          ← Ignore caches & env files
```

## Architecture

```
┌─────────────────────────────────────────────┐
│              SCAN LOOP (every 60s)          │
│                                             │
│                 ┌──────────┐                │
│                 │ Blinkit  │                │  ← Playwright headless
│                 │ scraper  │                │     Chromium browser
│                 └────┬─────┘                │
│                      │                      │
│                      ▼                      │
│              In-stock products?              │
│              ┌──────┴──────┐                 │
│              ▼             ▼                 │
│            Yes            No                 │
│         🔔 Pushover     Log & skip           │
│         alert                                │
└─────────────────────────────────────────────┘
```

## Notes

- **No raw API calls** — Blinkit blocks direct HTTP requests (403 / Cloudflare)
- **XHR interception** — captures the actual JSON API responses the browser receives
- **DOM fallback** — if XHR capture fails, scrapes product cards from the page
- **Duplicate protection** — won't spam you; alerts once per product, re-alerts on restock
