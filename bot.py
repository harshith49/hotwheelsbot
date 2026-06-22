#!/usr/bin/env python3
"""
🏎️  HOT WHEELS ALERT BOT  — Blinkit-only edition
=============================================================
Monitors Blinkit for Hot Wheels stock using Playwright browser automation
and sends Pushover alerts when in stock.

Deploy: Railway / Render / any Docker host / local
Push:   GitHub-ready, credentials read from env vars
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests as http_requests  # renamed to avoid clash with playwright
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    Response,
    async_playwright,
)

# =====================================================================
# ⚙️  CONFIGURATION  — all from env vars (safe for GitHub)
# =====================================================================
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY",  "ua3qtgs5wgk9ygfbsh8ed839zmexm2")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "arx7awfi39mtc9d543afrewaaiiexu")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1518008610922303549/NlRoBsXPK6JqkRlX4NqH4dSpzysr1A3Bql3xgi_8QcQzyMOeNnVSSIFdzkCCBMKVDQgk"
)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_TO_NUMBER   = os.environ.get("TWILIO_TO_NUMBER")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

LATITUDE   = float(os.environ.get("LATITUDE",  "12.924674"))
LONGITUDE  = float(os.environ.get("LONGITUDE", "77.694803"))
PINCODE    = os.environ.get("PINCODE", "560103")
AREA_NAME  = os.environ.get("AREA_NAME", "Bellandur, Bangalore")

SCAN_INTERVAL    = int(os.environ.get("SCAN_INTERVAL", "60"))
SEARCH_QUERY     = os.environ.get("SEARCH_QUERY", "hot wheels")
HEADLESS         = os.environ.get("HEADLESS", "false").lower() == "true"
DRY_RUN          = os.environ.get("DRY_RUN", "false").lower() == "true"

# =====================================================================
# 📋  LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hotwheels")

# =====================================================================
# 🗂️  STATE  — dedup tracking
# =====================================================================
ALERTED: set[str]       = set()
OUT_OF_STOCK: set[str]  = set()
MISSING_COUNT: dict[str, int] = {}

# =====================================================================
# 📦  DATA MODEL
# =====================================================================
@dataclass
class Product:
    platform:   str
    name:       str
    price:      float | str
    in_stock:   bool
    product_id: str
    link:       str
    image_url:  str = ""

# =====================================================================
# 🔔  ALERTS (Pushover, Telegram, Discord, Twilio SMS)
# =====================================================================
def send_telegram_message(text: str, link: Optional[str] = None) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    formatted_text = text
    if link:
        formatted_text = f"{text}\n\n🔗 [Open App / Buy Now]({link})"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": formatted_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        r = http_requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.warning(f"Telegram API {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Telegram failed: {e}")

def send_discord_webhook(text: str, link: Optional[str] = None) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    formatted_text = text
    if link:
        formatted_text = f"{text}\n\n🔗 [Open App / Buy Now]({link})"
    payload = {
        "content": formatted_text
    }
    try:
        r = http_requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code not in [200, 204]:
            log.warning(f"Discord API {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Discord failed: {e}")

def send_twilio_sms(text: str) -> None:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_TO_NUMBER or not TWILIO_FROM_NUMBER:
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    data = {
        "To": TWILIO_TO_NUMBER,
        "From": TWILIO_FROM_NUMBER,
        "Body": text
    }
    try:
        r = http_requests.post(url, data=data, auth=auth, timeout=10)
        if r.status_code not in [200, 201]:
            log.warning(f"Twilio API {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Twilio failed: {e}")

def send_alert(platform: str, name: str, price: float | str, link: str, *, force: bool = False) -> None:
    key = f"{platform.lower()}_{name.lower()}"
    if key in ALERTED and not force:
        return

    msg = f"🏎️ {name}\n💰 ₹{price}\n🏪 {platform} — Near your location!"

    if DRY_RUN:
        log.info(f"[DRY RUN] 🔔 Would alert → {platform}: {name} @ ₹{price}")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            log.info(f"[DRY RUN] Would send Telegram alert → {msg}")
        if DISCORD_WEBHOOK_URL:
            log.info(f"[DRY RUN] Would send Discord alert → {msg}")
        if TWILIO_ACCOUNT_SID:
            log.info(f"[DRY RUN] Would send Twilio SMS alert → {msg}")
        if not force:
            ALERTED.add(key)
        return

    alert_sent = False

    # 1. Pushover Send
    if PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY:
        payload = {
            "token":     PUSHOVER_APP_TOKEN,
            "user":      PUSHOVER_USER_KEY,
            "title":     f"🚨 Hot Wheels IN STOCK — {platform}",
            "message":   msg,
            "url":       link,
            "url_title": "Open App / Buy Now",
            "priority":  1,
            "sound":     "siren",
        }
        try:
            r = http_requests.post(
                "https://api.pushover.net/1/messages.json",
                data=payload,
                timeout=10,
            )
            if r.status_code == 200:
                log.info(f"🔔 Pushover alert fired → {platform}: {name} @ ₹{price}")
                alert_sent = True
            else:
                log.warning(f"Pushover {r.status_code}: {r.text}")
        except Exception as e:
            log.error(f"Pushover failed: {e}")

    # 2. Telegram Send
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            send_telegram_message(f"🚨 *Hot Wheels IN STOCK — {platform}*\n\n{msg}", link)
            log.info(f"🔔 Telegram alert fired → {platform}: {name} @ ₹{price}")
            alert_sent = True
        except Exception as e:
            log.error(f"Telegram alert failed: {e}")

    # 3. Discord Send
    if DISCORD_WEBHOOK_URL:
        try:
            send_discord_webhook(f"🚨 **Hot Wheels IN STOCK — {platform}**\n\n{msg}", link)
            log.info(f"🔔 Discord alert fired → {platform}: {name} @ ₹{price}")
            alert_sent = True
        except Exception as e:
            log.error(f"Discord alert failed: {e}")

    # 4. Twilio SMS Send
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        try:
            sms_text = f"Hot Wheels IN STOCK — {platform}\n🏎️ {name}\n💰 ₹{price}\n🏪 {platform}\n🔗 Buy: {link}"
            send_twilio_sms(sms_text)
            log.info(f"🔔 Twilio SMS alert fired → {platform}: {name} @ ₹{price}")
            alert_sent = True
        except Exception as e:
            log.error(f"Twilio alert failed: {e}")

    if alert_sent and not force:
        ALERTED.add(key)
        OUT_OF_STOCK.discard(key)


def send_oos_alert(platform: str, name: str, price: float | str, link: str) -> None:
    msg = f"🏎️ {name}\n💰 ₹{price}\n🏪 {platform} — Went Out of Stock!"

    if DRY_RUN:
        log.info(f"[DRY RUN] 🔔 Would alert OOS → {platform}: {name} @ ₹{price}")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            log.info(f"[DRY RUN] Would send Telegram OOS alert → {msg}")
        if DISCORD_WEBHOOK_URL:
            log.info(f"[DRY RUN] Would send Discord OOS alert → {msg}")
        if TWILIO_ACCOUNT_SID:
            log.info(f"[DRY RUN] Would send Twilio SMS OOS alert → {msg}")
        return

    # 1. Pushover Send
    if PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY:
        payload = {
            "token":     PUSHOVER_APP_TOKEN,
            "user":      PUSHOVER_USER_KEY,
            "title":     f"↩️ Hot Wheels OUT OF STOCK — {platform}",
            "message":   msg,
            "url":       link,
            "url_title": "Open App",
            "priority":  0,
        }
        try:
            r = http_requests.post(
                "https://api.pushover.net/1/messages.json",
                data=payload,
                timeout=10,
            )
            if r.status_code == 200:
                log.info(f"🔔 Pushover OOS alert fired → {platform}: {name} @ ₹{price}")
            else:
                log.warning(f"Pushover OOS {r.status_code}: {r.text}")
        except Exception as e:
            log.error(f"Pushover OOS failed: {e}")

    # 2. Telegram Send
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            send_telegram_message(f"↩️ *Hot Wheels OUT OF STOCK — {platform}*\n\n{msg}", link)
            log.info(f"🔔 Telegram OOS alert fired → {platform}: {name} @ ₹{price}")
        except Exception as e:
            log.error(f"Telegram OOS failed: {e}")

    # 3. Discord Send
    if DISCORD_WEBHOOK_URL:
        try:
            send_discord_webhook(f"↩️ **Hot Wheels OUT OF STOCK — {platform}**\n\n{msg}", link)
            log.info(f"🔔 Discord OOS alert fired → {platform}: {name} @ ₹{price}")
        except Exception as e:
            log.error(f"Discord OOS failed: {e}")

    # 4. Twilio SMS Send
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        try:
            sms_text = f"Hot Wheels OUT OF STOCK — {platform}\n🏎️ {name}\n💰 ₹{price}\n🏪 {platform}\n🔗 Buy: {link}"
            send_twilio_sms(sms_text)
            log.info(f"🔔 Twilio SMS OOS alert fired → {platform}: {name} @ ₹{price}")
        except Exception as e:
            log.error(f"Twilio OOS failed: {e}")


def mark_oos(platform: str, name: str, price: float | str, link: str) -> None:
    key = f"{platform.lower()}_{name.lower()}"
    if key in ALERTED and key not in OUT_OF_STOCK:
        log.info(f"↩️  {platform}: '{name}' went OOS — will re-alert on restock")
        OUT_OF_STOCK.add(key)
        ALERTED.discard(key)
        MISSING_COUNT.pop(key, None)
        send_oos_alert(platform, name, price, link)


class BlinkitBlockedException(Exception):
    pass

async def fetch_working_proxy(pw: Playwright) -> Optional[str]:
    """Fetch free Indian proxies from Geonode and Proxyscrape and find one that bypasses Cloudflare on Blinkit."""
    all_proxies = []

    # 1. Fetch from Geonode
    geonode_url = 'https://proxylist.geonode.com/api/proxy-list?country=IN&limit=50&page=1&sort_by=lastChecked&sort_type=desc'
    log.info("Fetching fresh proxy list from Geonode...")
    try:
        r = http_requests.get(geonode_url, timeout=10)
        res = r.json()
        for p in res.get('data', []):
            proto = p['protocols'][0]
            if proto in ['socks4', 'socks5', 'http', 'https']:
                all_proxies.append(f"{proto}://{p['ip']}:{p['port']}")
    except Exception as e:
        log.warning(f"Failed to fetch from Geonode: {e}")

    # 2. Fetch from Proxyscrape
    protocols = ['socks4', 'socks5', 'http']
    log.info("Fetching fresh proxy lists from Proxyscrape...")
    for proto in protocols:
        url = f'https://api.proxyscrape.com/v2/?request=displayproxies&protocol={proto}&timeout=10000&country=IN&ssl=all&anonymity=all'
        try:
            r = http_requests.get(url, timeout=10)
            for line in r.text.strip().split('\n'):
                line = line.strip()
                if line and ':' in line:
                    all_proxies.append(f"{proto}://{line}")
        except Exception as e:
            log.warning(f"Failed to fetch {proto} from Proxyscrape: {e}")

    # Deduplicate proxies
    unique_proxies = list(set(all_proxies))[:25]
    if not unique_proxies:
        log.warning("No proxies found from either Geonode or Proxyscrape.")
        return None

    log.info(f"Deduplicated to {len(unique_proxies)} unique Indian proxies to test.")

    # Launch a single validator browser instance to run all tests
    validator_browser = None
    try:
        validator_browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
            ]
        )
    except Exception as e:
        log.error(f"Failed to launch validator browser: {e}")
        return None

    async def check_proxy_context(proxy_server: str) -> Optional[str]:
        context = None
        try:
            context = await validator_browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                proxy={"server": proxy_server}
            )
            page = await context.new_page()
            response = await page.goto("https://blinkit.com", wait_until="domcontentloaded", timeout=8000)
            
            title = await page.title()
            body_text = await page.inner_text("body")
            
            if response and response.status == 200 and "blocked" not in body_text.lower() and "access denied" not in body_text.lower() and "error page" not in title.lower():
                log.info(f"✅ FOUND WORKING Indian proxy: {proxy_server}")
                return proxy_server
        except Exception:
            pass
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
        return None

    async def safe_check_proxy(p: str) -> Optional[str]:
        try:
            return await asyncio.wait_for(check_proxy_context(p), timeout=20.0)
        except Exception:
            return None

    # Test in chunks of 5 (lightweight contexts make this fast and safe)
    chunk_size = 5
    working_proxy = None
    try:
        for i in range(0, len(unique_proxies), chunk_size):
            chunk = unique_proxies[i:i+chunk_size]
            log.info(f"Testing proxy chunk {i//chunk_size + 1} ({len(chunk)} proxies)...")
            
            tasks = [asyncio.create_task(safe_check_proxy(p)) for p in chunk]
            results = await asyncio.gather(*tasks)
            working = [r for r in results if r is not None]
            if working:
                working_proxy = working[0]
                break
    finally:
        # Guarantee that the single browser process is closed
        if validator_browser:
            try:
                await asyncio.wait_for(validator_browser.close(), timeout=10.0)
            except Exception:
                pass

    return working_proxy

# =====================================================================
# 🌐  BROWSER MANAGER
# =====================================================================
class BrowserManager:
    """Owns the single Playwright browser instance shared by all scrapers."""

    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    async def start(self) -> BrowserContext:
        self._pw = await async_playwright().start()
        
        # Dynamically fetch a working proxy
        proxy_server = None
        if os.environ.get("DISABLE_PROXY", "false").lower() != "true":
            try:
                proxy_server = await fetch_working_proxy(self._pw)
            except Exception as e:
                log.error(f"Error checking proxies: {e}")
            
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor",
            "--window-size=1920,1080",
        ]
        
        if proxy_server:
            log.info(f"🚀 Launching browser with proxy: {proxy_server}")
            self._browser = await self._pw.chromium.launch(
                headless=HEADLESS,
                proxy={"server": proxy_server},
                args=launch_args,
            )
        else:
            log.warning("⚠️ No working Indian proxy found. Launching directly (might be blocked on Railway)...")
            self._browser = await self._pw.chromium.launch(
                headless=HEADLESS,
                args=launch_args,
            )
        self._context = await self._browser.new_context(
            geolocation={"latitude": LATITUDE, "longitude": LONGITUDE},
            permissions=["geolocation"],
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        # Stealth: hide automation signals
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) =>
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters);
        """)
        log.info("Browser started ✅")
        return self._context

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        log.info("Browser closed 🛑")

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

# =====================================================================
#   🛒  BLINKIT SCRAPER
# =====================================================================
async def scan_blinkit(ctx: BrowserContext) -> list[Product]:
    platform = "Blinkit"
    log.info(f"🟡 Scanning {platform}...")

    products: list[Product] = []
    page: Optional[Page] = None

    try:
        # ── Inject location cookies ──────────────────────────────
        await ctx.add_cookies([
            {"name": "lat",      "value": str(LATITUDE),  "domain": ".blinkit.com", "path": "/"},
            {"name": "lon",      "value": str(LONGITUDE), "domain": ".blinkit.com", "path": "/"},
            {"name": "gr_1_lat", "value": str(LATITUDE),  "domain": ".blinkit.com", "path": "/"},
            {"name": "gr_1_lng", "value": str(LONGITUDE), "domain": ".blinkit.com", "path": "/"},
        ])

        page = await ctx.new_page()

        # ── Set up XHR interception ──────────────────────────────
        captured: list[dict] = []
        got_response = asyncio.Event()

        async def intercept(resp: Response):
            try:
                url = resp.url
                if ("/layout/search" in url or "/search/products" in url or "/search?q=" in url) and resp.status == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct or "text" in ct:
                        body = await resp.text()
                        captured.append(json.loads(body))
                        got_response.set()
            except Exception:
                pass

        page.on("response", intercept)

        # ── Step 1: Go to homepage to pass Cloudflare ─────────────
        log.debug(f"  {platform}: loading homepage...")
        await page.goto("https://blinkit.com", wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)

        # ── Step 2: Set location via pincode ──────────────────────
        try:
            loc_input = await page.query_selector(
                'input[placeholder*="delivery location" i], '
                'input[placeholder*="search delivery" i], '
                'input[placeholder*="search area" i], '
                'input[placeholder*="area" i], '
                'input[placeholder*="pincode" i]'
            )
            if loc_input:
                log.info(f"  {platform}: typing location {AREA_NAME} in location field")
                await loc_input.click()
                await page.wait_for_timeout(500)
                await loc_input.fill(AREA_NAME)
                await page.wait_for_timeout(3000)
                
                # Attempt to click the suggestion directly based on AREA_NAME words
                clicked = False
                parts = [p.strip() for p in AREA_NAME.split(",") if p.strip()]
                selectors = []
                if parts:
                    first_part = parts[0]
                    selectors.append(f'text="{first_part}"')
                    selectors.append(f'div:has-text("{first_part}")')
                selectors.extend([
                    'text="Karnataka"',
                    'text="Bangalore"',
                    'text="Bengaluru"',
                    'div:has-text("Karnataka")',
                ])
                
                for sel in selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            log.info(f"  {platform}: clicking location suggestion: {sel}")
                            await el.click(timeout=3000)
                            clicked = True
                            break
                    except Exception:
                        pass
                
                if not clicked:
                    # Keyboard fallback
                    log.info(f"  {platform}: fallback to selecting location via keyboard")
                    await page.keyboard.press("ArrowDown")
                    await page.wait_for_timeout(300)
                    await page.keyboard.press("Enter")
                
                await page.wait_for_timeout(4000)
            else:
                # Fallback: try Detect my location button
                for sel in [
                    'button:has-text("Detect my location")',
                    'button:has-text("Detect")',
                ]:
                    btn = await page.query_selector(sel)
                    if btn:
                        log.info(f"  {platform}: clicking detect-location")
                        try:
                            await btn.click(timeout=5000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(4000)
                        break
        except Exception as e:
            log.warning(f"  {platform}: location setup error (continuing): {e}")

        # ── Step 3: Navigate to search page ──────────────────────
        q = SEARCH_QUERY.replace(" ", "+")
        log.info(f"  {platform}: navigating to search results page")
        await page.goto(f"https://blinkit.com/s/?q={q}",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        # ── Wait for XHR ─────────────────────────────────────────
        try:
            await asyncio.wait_for(got_response.wait(), timeout=20)
        except asyncio.TimeoutError:
            log.warning(f"  {platform}: no XHR captured in 20s")

        # ── Parse XHR data ───────────────────────────────────────
        for data in captured:
            objs = []

            # NEW: response → snippets → each snippet with product_card type IS a product
            resp_data = data.get("response", data)
            for snip in resp_data.get("snippets", []):
                wtype = snip.get("widget_type", "")
                if "product_card" in wtype.lower():
                    sd = snip.get("data", {})
                    if isinstance(sd, dict) and sd.get("name"):
                        objs.append(sd)

            # OLD format fallbacks
            if not objs:
                try:
                    objs = data.get("objects", [{}])[0].get("data", {}).get("objects", [])
                except (IndexError, AttributeError):
                    pass
            if not objs:
                objs = data.get("products", [])

            for obj in objs:
                name = obj.get("name", obj.get("display_name", ""))
                if isinstance(name, dict):
                    name = name.get("text", str(name))
                nl = name.lower()
                if "hot wheels" not in nl and "hotwheels" not in nl:
                    continue
                pid_data = obj.get("identity", {})
                pid = str(pid_data.get("id", obj.get("id", obj.get("product_id", name))))
                
                # Check actual stock state from JSON to support "Coming Soon" and Out of Stock
                is_sold_out = obj.get("is_sold_out", False)
                product_state = str(obj.get("product_state", "available")).lower()
                inventory = obj.get("inventory", 1)
                in_stock = (not is_sold_out) and (product_state == "available") and (inventory > 0)
                raw_price = obj.get("normal_price", obj.get("offer_price", obj.get("price", obj.get("mrp", "N/A"))))
                if isinstance(raw_price, dict):
                    raw_price = raw_price.get("text", "N/A")
                price = str(raw_price).replace("\u20b9", "").replace(",", "").strip()
                click = obj.get("click_action", {})
                link = click.get("url", click.get("deep_link", ""))
                if link and not link.startswith("http"):
                    link = f"https://blinkit.com{link}"
                if not link:
                    link = f"https://blinkit.com/s/?q=hot+wheels"
                img = obj.get("image", obj.get("image_url", ""))
                if isinstance(img, dict):
                    img = img.get("url", "")
                products.append(Product(platform, name, price, in_stock, pid, link, img))

        # ── DOM fallback ─────────────────────────────────────────
        if not products:
            try:
                title = await page.title()
                log.warning(f"  {platform} XHR failed, trying DOM fallback. Page title: {title}")
            except Exception:
                title = "Unknown"
            products = await _dom_fallback(page, platform, SEARCH_QUERY)
            if not products:
                try:
                    body_text = await page.inner_text("body")
                    snippet = body_text[:600].replace('\n', ' ').replace('\r', ' ')
                    log.warning(f"  {platform} DOM fallback failed. Body snippet: {snippet}")
                    if "blocked" in snippet.lower() or "access denied" in snippet.lower() or "error page" in title.lower():
                        raise BlinkitBlockedException("Blinkit blocked by Cloudflare (Access Denied)")
                except BlinkitBlockedException:
                    raise
                except Exception as e:
                    log.warning(f"  {platform} failed to extract body text: {e}")

        page.remove_listener("response", intercept)

    except BlinkitBlockedException:
        raise
    except Exception as e:
        log.error(f"  {platform} error: {e}")
        raise BlinkitBlockedException(f"Blinkit context failed: {e}")
    finally:
        if page and not page.is_closed():
            await page.close()

    log.info(f"  {platform}: {len(products)} Hot Wheels found")
    return products

# =====================================================================
#   🧩  SHARED DOM FALLBACK  — works for Blinkit
# =====================================================================
async def _dom_fallback(page: Page, platform: str, query: str) -> list[Product]:
    """Last resort: scrape visible product cards from the DOM."""
    import re
    products: list[Product] = []
    
    def slugify(t: str) -> str:
        t = t.lower()
        t = re.sub(r'[^a-z0-9]+', '-', t)
        return t.strip('-')

    try:
        await page.wait_for_timeout(5000)

        # Updated selectors to include Blinkit's div[role="button"][id] product card elements
        cards = await page.query_selector_all(
            'div[role="button"][id]:not([id="product_container"]), '
            'div[class*="tw-items-start"][class*="tw-flex-col"], '
            '[data-testid="product-card"], '
            'a[href*="/prn/"], '
            'a[href*="/item/"], '
            'div[class*="ProductCard"], '
            'div[class*="product-card"], '
            'div[role="listitem"]'
        )

        seen_ids = set()

        for card in cards:
            try:
                text = await card.inner_text()
                tl = text.lower()
                if "hot wheels" not in tl and "hotwheels" not in tl:
                    continue

                card_id = await card.get_attribute("id") or ""
                card_id = card_id.strip()

                lines = [l.strip() for l in text.split("\n") if l.strip()]
                SKIP_WORDS = {
                    "add", "added", "notify", "notify me", "remove", "+", "-", 
                    "out of stock", "sold out", "coming soon", "mins", "min", "minutes"
                }

                # Find name: first line containing hot wheels / hotwheels
                name = "Hot Wheels (unknown)"
                for line in lines:
                    ll = line.lower()
                    if "hot wheels" in ll or "hotwheels" in ll:
                        name = line
                        break
                else:
                    # Fallback name logic if no line contains hot wheels explicitly
                    for line in lines:
                        ll = line.lower()
                        if (ll not in SKIP_WORDS and 
                            len(line) > 3 and 
                            not re.match(r'^\d+\s*mins?$', ll) and 
                            "₹" not in line):
                            name = line
                            break

                price: str | float = "N/A"
                for line in lines:
                    if "₹" in line:
                        price = line.replace("₹", "").strip().split()[0]
                        break

                in_stock = "out of stock" not in tl and "sold out" not in tl and "notify" not in tl and "coming soon" not in tl

                # Direct URL structure: /prn/[slug]/prid/[product-id]
                if card_id and card_id.isdigit():
                    product_id = card_id
                    slug = slugify(name)
                    link = f"https://blinkit.com/prn/{slug}/prid/{product_id}"
                else:
                    product_id = f"dom_{abs(hash(name))}"
                    href = await card.get_attribute("href") or ""
                    link = href if href.startswith("http") else f"https://blinkit.com{href}" if href else f"https://blinkit.com/s/?q={query.replace(' ', '+')}"

                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)

                products.append(Product(
                    platform=platform,
                    name=name,
                    price=price,
                    in_stock=in_stock,
                    product_id=product_id,
                    link=link,
                ))
            except Exception:
                continue

        if products:
            log.info(f"  {platform} DOM fallback: found {len(products)} products")

    except Exception as e:
        log.warning(f"  {platform} DOM fallback failed: {e}")

    return products


# =====================================================================
#   🧩  DOM FALLBACK  — works for Big Basket
# =====================================================================
async def _dom_fallback_bigbasket(page: Page, query: str) -> list[Product]:
    """Scrape visible Big Basket product cards from the DOM."""
    import re
    products: list[Product] = []
    try:
        # First group links by pid to handle duplicate card elements and get the best name
        links = await page.query_selector_all('a[href*="/pd/"]')
        pid_to_link = {}
        for link in links:
            href = await link.get_attribute("href") or ""
            if not href:
                continue
            m = re.search(r'/pd/(\d+)/', href)
            if not m:
                continue
            pid = m.group(1)
            name = (await link.inner_text()).strip()
            
            if pid not in pid_to_link:
                pid_to_link[pid] = (link, href, name)
            else:
                existing_link, existing_href, existing_name = pid_to_link[pid]
                if not existing_name and name:
                    pid_to_link[pid] = (link, href, name)

        for pid, (link, href, name) in pid_to_link.items():
            # Get parent card container
            card_info = await page.evaluate('''(el) => {
                let parent = el.parentElement;
                while (parent && parent.tagName !== 'BODY') {
                    let text = parent.innerText || "";
                    if (text.includes("₹") && (text.toLowerCase().includes("add") || text.toLowerCase().includes("out of stock") || text.toLowerCase().includes("notify") || text.toLowerCase().includes("coming soon"))) {
                        return { text: text };
                    }
                    parent = parent.parentElement;
                }
                return null;
            }''', link)
            
            if not card_info:
                continue
                
            text = card_info["text"]
            tl = text.lower()
            
            name = name.replace("\n", " ").strip()
            IGNORE_NAMES = {"out of stock", "notify me", "coming soon", "add", "added", "sold out"}
            if name.lower() in IGNORE_NAMES:
                name = ""

            if not name:
                m_slug = re.search(r'/pd/\d+/([^/?#]+)', href)
                if m_slug:
                    slug = m_slug.group(1)
                    name = slug.replace('-', ' ').strip()
                    name = " ".join(w.capitalize() for w in name.split())
                else:
                    name = "Hot Wheels Product"
            
            if "hot wheels" not in name.lower() and "hotwheels" not in name.lower() and "hot wheels" not in tl:
                continue
                
            prices = []
            for m_price in re.finditer(r'₹\s*(\d+(?:\.\d+)?)', text):
                val = m_price.group(1)
                idx = m_price.end()
                suffix = text[idx:idx+15].lower()
                if "off" in suffix:
                    continue
                prices.append(float(val) if '.' in val else int(val))
            price = min(prices) if prices else "N/A"
            
            in_stock = ("add" in tl) and ("out of stock" not in tl) and ("notify" not in tl) and ("coming soon" not in tl)
            
            full_link = href if href.startswith("http") else f"https://www.bigbasket.com{href}"
            products.append(Product(
                platform="Big Basket",
                name=name,
                price=price,
                in_stock=in_stock,
                product_id=pid,
                link=full_link,
            ))
    except Exception as e:
        log.warning(f"  Big Basket DOM fallback error: {e}")
    return products


# =====================================================================
#   🛒  BIG BASKET SCRAPER
# =====================================================================
async def scan_bigbasket(ctx: BrowserContext) -> list[Product]:
    platform = "Big Basket"
    log.info(f"🟡 Scanning {platform}...")

    products: list[Product] = []
    page: Optional[Page] = None

    try:
        page = await ctx.new_page()

        # ── Step 1: Go to homepage to pass Cloudflare/Akamai ──────
        log.debug(f"  {platform}: loading homepage...")
        await page.goto("https://www.bigbasket.com", wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)

        # Check if blocked
        try:
            body_text = await page.inner_text("body")
            if "blocked" in body_text.lower() or "access denied" in body_text.lower() or "cloudflare" in body_text.lower():
                raise BlinkitBlockedException("Big Basket blocked by Cloudflare/Akamai")
        except BlinkitBlockedException:
            raise
        except Exception:
            pass

        # ── Step 2: Set location via pincode ──────────────────────
        try:
            locator = page.locator('button:has-text("Select Location"), button:has-text("Delivery in")')
            count = await locator.count()
            visible_locator = None
            for i in range(count):
                loc = locator.nth(i)
                if await loc.is_visible():
                    visible_locator = loc
                    
            if visible_locator:
                log.info(f"  {platform}: clicking location button")
                await visible_locator.click()
                await page.wait_for_timeout(3000)
                
                input_locator = page.locator('input[placeholder*="Search for area"]')
                input_count = await input_locator.count()
                visible_input = None
                for i in range(input_count):
                    loc = input_locator.nth(i)
                    if await loc.is_visible():
                        visible_input = loc
                        
                if visible_input:
                    log.info(f"  {platform}: filling pincode {PINCODE}")
                    await visible_input.click()
                    await visible_input.fill(PINCODE)
                    await page.wait_for_timeout(4000)
                    
                    clicked = await page.evaluate('''(pincode) => {
                        let sug = Array.from(document.querySelectorAll('ul li, li, div[class*="suggestion"], div[class*="List"] div, div[class*="item"]')).find(el => {
                            let t = (el.innerText || "").toLowerCase();
                            return t.includes(pincode);
                        });
                        if (sug) {
                            sug.click();
                            return true;
                        }
                        let firstLi = document.querySelector('ul li, li');
                        if (firstLi) {
                            firstLi.click();
                            return true;
                        }
                        return false;
                    }''', PINCODE)
                    if clicked:
                        log.info(f"  {platform}: location suggestion clicked, waiting for update...")
                        await page.wait_for_timeout(5000)
        except Exception as e:
            log.warning(f"  {platform}: location setup error (continuing): {e}")

        # ── Step 3: Navigate/Search using homepage input ──────────
        try:
            log.info(f"  {platform}: searching via homepage input box")
            search_locator = page.locator('input[placeholder*="Search for Products"], input[placeholder*="Search for products"], input[placeholder*="Search 18000"]')
            search_count = await search_locator.count()
            visible_search = None
            for i in range(search_count):
                loc = search_locator.nth(i)
                if await loc.is_visible():
                    visible_search = loc
            
            if visible_search:
                await visible_search.click()
                await visible_search.fill(SEARCH_QUERY)
                await page.wait_for_timeout(1000)
                await page.keyboard.press("Enter")
            else:
                log.info(f"  {platform}: search input not found, navigating directly")
                q = SEARCH_QUERY.replace(" ", "+")
                await page.goto(f"https://www.bigbasket.com/search/?q={q}", wait_until="domcontentloaded", timeout=45_000)
            
            await page.wait_for_timeout(8000)
        except Exception as e:
            log.error(f"  {platform}: search error: {e}")

        # Check block again on search page
        try:
            body_text = await page.inner_text("body")
            if "blocked" in body_text.lower() or "access denied" in body_text.lower() or "cloudflare" in body_text.lower():
                raise BlinkitBlockedException("Big Basket search blocked by Cloudflare/Akamai")
        except BlinkitBlockedException:
            raise
        except Exception:
            pass

        # ── Step 4: Extract from DOM ─────────────────────────────
        products = await _dom_fallback_bigbasket(page, SEARCH_QUERY)

    except BlinkitBlockedException:
        raise
    except Exception as e:
        log.error(f"  {platform} error: {e}")
    finally:
        if page and not page.is_closed():
            await page.close()

    log.info(f"  {platform}: {len(products)} Hot Wheels found")
    return products


# =====================================================================
# ⏱️  MAIN SCAN LOOP
# =====================================================================
async def run_scan(ctx: BrowserContext) -> None:
    log.info("═══ Starting scan cycle ═══")

    # Scrape Blinkit
    try:
        blinkit_results = await scan_blinkit(ctx)
    except BlinkitBlockedException:
        raise
    except Exception as e:
        log.error(f"  Blinkit crashed: {e}")
        blinkit_results = []

    # Scrape Big Basket
    try:
        bb_results = await scan_bigbasket(ctx)
    except BlinkitBlockedException:
        raise
    except Exception as e:
        log.error(f"  Big Basket crashed: {e}")
        bb_results = []

    results = blinkit_results + bb_results

    total_found = 0
    total_in_stock = 0
    seen_in_scan = set()

    for product in results:
        total_found += 1
        key = f"{product.platform.lower()}_{product.name.lower()}"
        seen_in_scan.add(key)
        if product.in_stock:
            total_in_stock += 1
            send_alert(product.platform, product.name, product.price, product.link)
        else:
            mark_oos(product.platform, product.name, product.price, product.link)

    # Reset missing counts for items seen in this scan
    for key in seen_in_scan:
        if key in MISSING_COUNT:
            MISSING_COUNT[key] = 0

    # Clean up keys that were previously ALERTED but have disappeared from search results
    blinkit_found = sum(1 for p in blinkit_results)
    if blinkit_found > 0:
        platform = "blinkit"
        for alerted_key in list(ALERTED):
            if alerted_key.startswith(f"{platform}_") and alerted_key not in seen_in_scan:
                MISSING_COUNT[alerted_key] = MISSING_COUNT.get(alerted_key, 0) + 1
                if MISSING_COUNT[alerted_key] >= 5:
                    name = alerted_key[len(platform) + 1:]
                    log.info(f"↩️  {platform.capitalize()}: '{name}' disappeared from search results for 5 consecutive scans — marking as OOS")
                    OUT_OF_STOCK.add(alerted_key)
                    ALERTED.discard(alerted_key)
                    MISSING_COUNT.pop(alerted_key, None)
                    send_oos_alert(platform.capitalize(), name, "N/A", f"https://blinkit.com/s/?q={SEARCH_QUERY.replace(' ', '+')}")

    bb_found = sum(1 for p in bb_results)
    if bb_found > 0:
        platform = "big basket"
        for alerted_key in list(ALERTED):
            if alerted_key.startswith(f"{platform}_") and alerted_key not in seen_in_scan:
                MISSING_COUNT[alerted_key] = MISSING_COUNT.get(alerted_key, 0) + 1
                if MISSING_COUNT[alerted_key] >= 5:
                    name = alerted_key[len(platform) + 1:]
                    log.info(f"↩️  {platform.capitalize()}: '{name}' disappeared from search results for 5 consecutive scans — marking as OOS")
                    OUT_OF_STOCK.add(alerted_key)
                    ALERTED.discard(alerted_key)
                    MISSING_COUNT.pop(alerted_key, None)
                    send_oos_alert(platform.capitalize(), name, "N/A", "https://www.bigbasket.com")

    log.info(
        f"═══ Scan done: {total_found} products, "
        f"{total_in_stock} in stock. "
        f"Next in {SCAN_INTERVAL}s ═══\n"
    )

# =====================================================================
# 🏥  HEALTH CHECK HTTP SERVER  — keeps Railway happy
# =====================================================================
async def handle_health_check(reader, writer):
    try:
        # Drain headers
        await reader.readuntil(b"\r\n\r\n")
    except Exception:
        pass
    
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 2\r\n"
        "Connection: close\r\n\r\n"
        "OK"
    )
    try:
        writer.write(response.encode())
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

async def start_health_check_server():
    port_str = os.environ.get("PORT")
    if not port_str:
        log.info("No PORT env var found, skipping health check HTTP server.")
        return
    try:
        port = int(port_str)
        server = await asyncio.start_server(handle_health_check, "0.0.0.0", port)
        log.info(f"Health check HTTP server listening on port {port} ✅")
        # Keep server running in the background loop
        asyncio.create_task(server.serve_forever())
    except Exception as e:
        log.error(f"Failed to start health check server: {e}")

async def main() -> None:
    log.info("🏁 Hot Wheels Alert Bot — STARTING")
    # Start health check server
    await start_health_check_server()
    log.info(f"📍 Location: {LATITUDE}, {LONGITUDE} ({AREA_NAME})")
    log.info(f"🔍 Query: '{SEARCH_QUERY}'")
    log.info(f"⏱️  Interval: {SCAN_INTERVAL}s")
    log.info(f"🖥️  Headless: {HEADLESS}")
    log.info("🛒 Platforms: Blinkit, Big Basket")
    
    # Check if any notification method is configured
    has_pushover = bool(PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN)
    has_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    has_discord  = bool(DISCORD_WEBHOOK_URL)
    has_twilio   = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_TO_NUMBER and TWILIO_FROM_NUMBER)

    if DRY_RUN:
        log.info("🧪 DRY RUN MODE — no alerts will be sent")
    elif not (has_pushover or has_telegram or has_discord or has_twilio):
        log.error("No notification credentials found. You must configure at least one alerting method: Pushover, Telegram, Discord, or Twilio SMS when DRY_RUN is false.")
        sys.exit(1)

    # ── Start browser ────────────────────────────────────────────
    mgr = BrowserManager()
    ctx = None  # Will be initialized inside the scan loop

    # ── Graceful shutdown ────────────────────────────────────────
    shutdown = asyncio.Event()

    def handle_signal(*_):
        log.info("🛑 Shutdown signal received")
        shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            signal.signal(sig, handle_signal)

    # ── Scan loop ────────────────────────────────────────────────
    try:
        while not shutdown.is_set():
            if not ctx:
                try:
                    ctx = await mgr.start()
                except Exception as e:
                    log.error(f"Failed to start browser manager: {e}")
                    await asyncio.sleep(10)
                    continue

            try:
                await run_scan(ctx)
            except BlinkitBlockedException as e:
                log.warning(f"💥 Scan blocked: {e}. Re-initializing browser with a new proxy...")
                ctx = None
                try:
                    await mgr.stop()
                except Exception as ex:
                    log.error(f"Error stopping browser manager: {ex}")
                await asyncio.sleep(2)
                continue
            except Exception as e:
                log.error(f"💥 Scan loop error: {e}")

            # Wait for SCAN_INTERVAL or until shutdown
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=SCAN_INTERVAL)
                break  # shutdown was triggered
            except asyncio.TimeoutError:
                pass  # normal: interval elapsed, do another scan
    finally:
        await mgr.stop()
        log.info("👋 Bot stopped cleanly")

if __name__ == "__main__":
    asyncio.run(main())
