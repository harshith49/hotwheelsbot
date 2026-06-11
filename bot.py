#!/usr/bin/env python3
"""
🏎️  HOT WHEELS ALERT BOT  — Single-file, cloud-ready edition
=============================================================
Monitors Blinkit, Zepto & Swiggy Instamart for Hot Wheels stock
using Playwright browser automation (not raw HTTP — those get blocked).

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
from dataclasses import dataclass, field
from datetime import datetime
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


LATITUDE   = float(os.environ.get("LATITUDE",  "12.924674"))
LONGITUDE  = float(os.environ.get("LONGITUDE", "77.694803"))
PINCODE    = os.environ.get("PINCODE", "560066")
AREA_NAME  = os.environ.get("AREA_NAME", "Bellandur, Bangalore")

SCAN_INTERVAL    = int(os.environ.get("SCAN_INTERVAL", "60"))
SEARCH_QUERY     = os.environ.get("SEARCH_QUERY", "hot wheels")
HEADLESS         = os.environ.get("HEADLESS", "true").lower() == "true"
ENABLE_BLINKIT   = os.environ.get("ENABLE_BLINKIT",   "true").lower() == "true"
ENABLE_ZEPTO     = os.environ.get("ENABLE_ZEPTO",     "true").lower() == "true"
ENABLE_INSTAMART = os.environ.get("ENABLE_INSTAMART", "true").lower() == "true"
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
# 🔔  PUSHOVER ALERTS
# =====================================================================
def send_alert(platform: str, name: str, price, link: str, *, force: bool = False) -> None:
    if not DRY_RUN and (not PUSHOVER_USER_KEY or not PUSHOVER_APP_TOKEN):
        log.warning("Pushover credentials missing; alert skipped")
        return

    key = f"{platform.lower()}_{name.lower()}"
    if key in ALERTED and not force:
        return

    msg = f"🏎️ {name}\n💰 ₹{price}\n🏪 {platform} — Near your location!"
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

    if DRY_RUN:
        log.info(f"[DRY RUN] 🔔 Would alert → {platform}: {name} @ ₹{price}")
        if not force:
            ALERTED.add(key)
        return

    try:
        r = http_requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=10,
        )
        if r.status_code == 200:
            log.info(f"🔔 Alert fired → {platform}: {name} @ ₹{price}")
            if not force:
                ALERTED.add(key)
                OUT_OF_STOCK.discard(key)
        else:
            log.warning(f"Pushover {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Pushover failed: {e}")


def mark_oos(platform: str, name: str) -> None:
    key = f"{platform.lower()}_{name.lower()}"
    if key in ALERTED and key not in OUT_OF_STOCK:
        log.info(f"↩️  {platform}: '{name}' went OOS — will re-alert on restock")
        OUT_OF_STOCK.add(key)
        ALERTED.discard(key)


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
        self._browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor",
                "--window-size=1920,1080",
            ],
        )
        self._context = await self._browser.new_context(
            geolocation={"latitude": LATITUDE, "longitude": LONGITUDE},
            permissions=["geolocation"],
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
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

        # ── Set up dual XHR interception ──────────────────────────
        captured: list[dict] = []
        got_response = asyncio.Event()

        # Method 1: page.on("response") — works when browser makes the call
        async def intercept_resp(resp: Response):
            try:
                url = resp.url
                if "/v1/layout/search" not in url.lower():
                    return
                if resp.status != 200:
                    return
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return

                body = await resp.text()
                data = json.loads(body)
                if not isinstance(data, dict):
                    return

                captured.append(data)
                if not got_response.is_set():
                    got_response.set()
                log.info(f"  {platform}: captured search API ({len(body)} bytes) {url}")
            except Exception:
                pass

        page.on("response", intercept_resp)

        # ── Step 1: Go to homepage to set location ────────────────
        log.debug(f"  {platform}: loading homepage...")
        await page.goto("https://blinkit.com", wait_until="domcontentloaded", timeout=30_000)
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
                log.info(f"  {platform}: typing pincode {PINCODE} in location field")
                await loc_input.click()
                await page.wait_for_timeout(500)
                await loc_input.fill(AREA_NAME)
                await page.wait_for_timeout(2500)
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(300)
                await page.keyboard.press("Enter")
                log.info(f"  {platform}: selected location via keyboard")
                await page.wait_for_timeout(3000)
            else:
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
        await page.wait_for_timeout(5000)

        # ── Wait for XHR ─────────────────────────────────────────
        try:
            await asyncio.wait_for(got_response.wait(), timeout=15)
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
                in_stock = True  # if it appears in search results, it's available
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
            products = await _dom_fallback(page, platform, SEARCH_QUERY)

    except Exception as e:
        log.error(f"  {platform} error: {e}")
    finally:
        if page and not page.is_closed():
            try:
                page.remove_listener("response", intercept_resp)
            except Exception:
                pass
            await page.close()

    log.info(f"  {platform}: {len(products)} Hot Wheels found")
    return products


# =====================================================================
#   🛒  ZEPTO SCRAPER
# =====================================================================
async def scan_zepto(ctx: BrowserContext) -> list[Product]:
    platform = "Zepto"
    log.info(f"🟣 Scanning {platform}...")

    products: list[Product] = []
    page: Optional[Page] = None

    try:
        await ctx.add_cookies([
            {"name": "user_lat",  "value": str(LATITUDE),  "domain": ".zeptonow.com", "path": "/"},
            {"name": "user_lng",  "value": str(LONGITUDE), "domain": ".zeptonow.com", "path": "/"},
            {"name": "latitude",  "value": str(LATITUDE),  "domain": ".zeptonow.com", "path": "/"},
            {"name": "longitude", "value": str(LONGITUDE), "domain": ".zeptonow.com", "path": "/"},
        ])

        page = await ctx.new_page()

        captured: list[dict] = []
        got_response = asyncio.Event()

        async def intercept(resp: Response):
            try:
                if "search" in resp.url and resp.status == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        body = await resp.text()
                        captured.append(json.loads(body))
                        got_response.set()
            except Exception:
                pass

        page.on("response", intercept)

        # ── Step 1: Go to homepage first for session ─────────────
        log.debug(f"  {platform}: loading homepage to establish session...")
        await page.goto("https://www.zeptonow.com", wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)

        # ── Step 2: Handle location picker ───────────────────────
        for selector in [
            'button:has-text("Detect")',
            'button:has-text("Current Location")',
            'button:has-text("Use my location")',
        ]:
            btn = await page.query_selector(selector)
            if btn:
                log.info(f"  {platform}: clicking detect-location")
                await btn.click()
                await page.wait_for_timeout(4000)
                for confirm_sel in ['button:has-text("Confirm")', 'button:has-text("Continue")']:
                    cbtn = await page.query_selector(confirm_sel)
                    if cbtn:
                        await cbtn.click()
                        await page.wait_for_timeout(2000)
                        break
                break

        # ── Step 3: Navigate to search page ──────────────────────
        q = SEARCH_QUERY.replace(" ", "+")
        await page.goto(f"https://www.zeptonow.com/search?query={q}",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        # Type in search if the URL didn't trigger results
        search_input = await page.query_selector(
            'input[type="search"], input[placeholder*="Search" i], '
            'input[name="search"], input[data-testid="search-input"]'
        )
        if search_input:
            await search_input.fill("")
            await search_input.fill(SEARCH_QUERY)
            await page.keyboard.press("Enter")

        try:
            await asyncio.wait_for(got_response.wait(), timeout=20)
        except asyncio.TimeoutError:
            log.warning(f"  {platform}: no XHR captured in 20s")

        # ── Parse Zepto response ─────────────────────────────────
        for data in captured:
            items: list = []
            # Path: layout → items → product
            for section in data.get("layout", []):
                for item in section.get("items", []):
                    p = item.get("product", item.get("productResponse", item))
                    if isinstance(p, dict):
                        items.append(p)
            # Path: sections → items → product
            for section in data.get("sections", data.get("data", {}).get("sections", [])):
                for item in section.get("items", []):
                    p = item.get("product", item.get("productResponse", item))
                    if isinstance(p, dict):
                        items.append(p)
            if not items:
                items = data.get("products", data.get("storeProducts", []))
            if not items:
                items = data.get("storeProductsV3", {}).get("products", [])

            for item in items:
                name = item.get("name", item.get("productName", ""))
                nl = name.lower()
                if "hot wheels" not in nl and "hotwheels" not in nl:
                    continue
                pid = str(item.get("product_id", item.get("productId", item.get("id", name))))
                in_stock = (
                    item.get("available", False)
                    or item.get("is_available", False)
                    or item.get("inStock", False)
                    or item.get("stockStatus", "") == "IN_STOCK"
                )
                raw = item.get("sellingPrice", item.get("selling_price",
                      item.get("discountedSellingPrice", item.get("mrp", 0))))
                price = round(raw / 100, 2) if isinstance(raw, (int, float)) and raw > 1000 else raw
                slug = item.get("product_slug", item.get("slug", item.get("productSlug", pid)))
                link = f"https://www.zeptonow.com/pn/{slug}/pvid/{pid}"
                products.append(Product(platform, name, price, in_stock, pid, link,
                                        item.get("imageUrl", item.get("image_url", ""))))

        if not products:
            products = await _dom_fallback_zepto(page, platform, SEARCH_QUERY)

        page.remove_listener("response", intercept)

    except Exception as e:
        log.error(f"  {platform} error: {e}")
    finally:
        if page and not page.is_closed():
            await page.close()

    log.info(f"  {platform}: {len(products)} Hot Wheels found")
    return products


# =====================================================================
#   🛒  SWIGGY INSTAMART SCRAPER
# =====================================================================
async def scan_instamart(ctx: BrowserContext) -> list[Product]:
    platform = "Instamart"
    log.info(f"🟠 Scanning {platform}...")

    products: list[Product] = []
    page: Optional[Page] = None

    try:
        await ctx.add_cookies([
            {"name": "lat",     "value": str(LATITUDE),  "domain": ".swiggy.com", "path": "/"},
            {"name": "lng",     "value": str(LONGITUDE), "domain": ".swiggy.com", "path": "/"},
            {"name": "userLat", "value": str(LATITUDE),  "domain": ".swiggy.com", "path": "/"},
            {"name": "userLng", "value": str(LONGITUDE), "domain": ".swiggy.com", "path": "/"},
            {"name": "address", "value": AREA_NAME,      "domain": ".swiggy.com", "path": "/"},
        ])

        page = await ctx.new_page()

        # Use page.route() to reliably intercept the search API response
        captured: list[dict] = []
        got_response = asyncio.Event()

        async def handle_route(route):
            try:
                resp = await route.fetch()
                body_bytes = await resp.body()
                body_str = body_bytes.decode("utf-8")
                data = json.loads(body_str)
                if isinstance(data, dict) and "data" in data:
                    captured.append(data)
                    got_response.set()
                    log.info(f"  {platform}: captured search API ({len(body_str)} bytes)")
                await route.fulfill(response=resp, body=body_bytes)
            except Exception as e:
                log.debug(f"  {platform}: route intercept error: {e}")
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route("**/api/instamart/search/v2**", handle_route)

        q = SEARCH_QUERY.replace(" ", "+")

        # Try up to 3 times
        for attempt in range(3):
            if got_response.is_set():
                break
            log.info(f"  {platform}: loading Instamart search (attempt {attempt + 1}/3)")
            try:
                await page.goto(
                    f"https://www.swiggy.com/instamart/search?custom_back=true&query={q}",
                    wait_until="domcontentloaded", timeout=45_000,
                )
                try:
                    await asyncio.wait_for(got_response.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass

                if got_response.is_set():
                    break

                # Try clicking "Try Again" if visible
                try:
                    try_btn = await page.query_selector('button:has-text("Try Again")')
                    if try_btn:
                        log.info(f"  {platform}: clicking Try Again")
                        await try_btn.click(timeout=3000)
                        try:
                            await asyncio.wait_for(got_response.wait(), timeout=10)
                        except asyncio.TimeoutError:
                            pass
                except Exception:
                    pass

                if not got_response.is_set():
                    await page.reload(wait_until="domcontentloaded", timeout=30_000)
                    try:
                        await asyncio.wait_for(got_response.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
            except Exception as e:
                log.warning(f"  {platform}: attempt {attempt + 1} error: {e}")

        if not got_response.is_set():
            log.warning(f"  {platform}: no search API captured after 3 attempts")

        # ── Parse Instamart response (new card-based format) ──────
        for data in captured:
            cards = data.get("data", {}).get("cards", [])
            for card_wrapper in cards:
                cc = card_wrapper.get("card", {}).get("card", {})
                ctype = cc.get("@type", "").split(".")[-1]

                # Extract product items from various card types
                raw_items: list[dict] = []

                # GridWidget: gridElements → infoWithStyle → items (in-stock)
                grid = cc.get("gridElements", {})
                if grid:
                    iws = grid.get("infoWithStyle", {})
                    items_data = iws.get("items", [])
                    if isinstance(items_data, list):
                        raw_items.extend(items_data)
                    elif isinstance(items_data, dict):
                        raw_items.extend(items_data.get("items", []))

                # OOSItemCollectionCard: items dict → items list
                oos_data = cc.get("items", cc.get("products", None))
                if isinstance(oos_data, dict):
                    oos_list = oos_data.get("items", [])
                    if isinstance(oos_list, list):
                        raw_items.extend(oos_list)
                elif isinstance(oos_data, list):
                    raw_items.extend(oos_data)

                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    name = (item.get("displayName", "")
                            or item.get("display_name", "")
                            or item.get("name", "")
                            or item.get("productName", ""))
                    nl = name.lower()
                    if "hot wheels" not in nl and "hotwheels" not in nl:
                        continue

                    in_stock = item.get("inStock", item.get("in_stock", False))

                    # Product ID from variations or direct
                    variations = item.get("variations", [])
                    pid = ""
                    price = "N/A"
                    if isinstance(variations, list) and variations:
                        v0 = variations[0]
                        pid = str(v0.get("skuId", v0.get("id", "")))
                        raw_price = v0.get("price", v0.get("mrp", v0.get("offer_price", "N/A")))
                        # Swiggy nested price: {offerPrice: {units: "179"}, mrp: {units: "179"}}
                        if isinstance(raw_price, dict):
                            op = raw_price.get("offerPrice", raw_price.get("mrp", {}))
                            if isinstance(op, dict):
                                price = op.get("units", op.get("value", "N/A"))
                            else:
                                price = raw_price.get("units", raw_price.get("value", "N/A"))
                        else:
                            price = raw_price
                    if not pid:
                        pid = str(item.get("id", item.get("productId", name)))
                    if price == "N/A":
                        raw_price = item.get("price", item.get("mrp", item.get("offer_price", "N/A")))
                        if isinstance(raw_price, dict):
                            op = raw_price.get("offerPrice", raw_price.get("mrp", {}))
                            if isinstance(op, dict):
                                price = op.get("units", op.get("value", "N/A"))
                            else:
                                price = raw_price.get("units", raw_price.get("value", "N/A"))
                        else:
                            price = raw_price
                    # Convert paise to rupees if needed
                    if isinstance(price, (int, float)) and price > 10000:
                        price = price / 100

                    link = f"https://www.swiggy.com/instamart/search?query={q}"
                    img = item.get("image", item.get("imageId", ""))
                    if img and not img.startswith("http"):
                        img = f"https://instamart-media-assets.swiggy.com/swiggy/image/upload/{img}"

                    products.append(Product(platform, name, price, in_stock, pid, link, img))

        if not products and got_response.is_set():
            log.info(f"  {platform}: API returned data but no Hot Wheels products found")

        # DOM fallback if nothing captured
        if not products and not got_response.is_set():
            products = await _dom_fallback(page, platform, SEARCH_QUERY)

    except Exception as e:
        log.error(f"  {platform} error: {e}")
    finally:
        if page and not page.is_closed():
            try:
                await page.unroute("**/api/instamart/search/v2**")
            except Exception:
                pass
            await page.close()

    log.info(f"  {platform}: {len(products)} Hot Wheels found")
    return products


# =====================================================================
#   🧩  SHARED DOM FALLBACK  — works for all platforms
# =====================================================================
async def _dom_fallback(page: Page, platform: str, query: str) -> list[Product]:
    """Last resort: scrape visible product cards from the DOM."""
    products: list[Product] = []
    try:
        await page.wait_for_timeout(5000)

        cards = await page.query_selector_all(
            '[data-testid="product-card"], '
            'a[href*="/pn/"], '
            'a[href*="/prn/"], '
            'a[href*="/p/"], '
            'a[href*="/product/"], '
            'a[href*="/item/"], '
            'div[class*="ProductCard"], '
            'div[class*="product-card"], '
            'div[class*="ProductTile"], '
            'div[class*="search-result"], '
            'div[class*="SearchResult"], '
            'div[role="listitem"]'
        )

        for card in cards:
            try:
                text = await card.inner_text()
                tl = text.lower()
                if "hot wheels" not in tl and "hotwheels" not in tl:
                    continue

                lines = [l.strip() for l in text.split("\n") if l.strip()]
                # Filter out button text, short labels, etc.
                SKIP_WORDS = {"add", "added", "notify", "notify me", "remove", "+", "-", "out of stock", "sold out"}
                name_lines = [l for l in lines if l.lower() not in SKIP_WORDS and len(l) > 3]
                name = name_lines[0] if name_lines else "Hot Wheels (unknown)"

                price: str | float = "N/A"
                for line in lines:
                    if "₹" in line:
                        price = line.replace("₹", "").strip().split()[0]
                        break

                in_stock = "out of stock" not in tl and "sold out" not in tl and "notify" not in tl

                href = await card.get_attribute("href") or ""
                link = href if href.startswith("http") else ""
                if not link and href:
                    # Relative URL
                    base = "https://www.zeptonow.com" if "zepto" in platform.lower() else \
                           "https://blinkit.com" if "blinkit" in platform.lower() else \
                           "https://www.swiggy.com"
                    link = f"{base}{href}"

                products.append(Product(
                    platform=platform,
                    name=name,
                    price=price,
                    in_stock=in_stock,
                    product_id=f"dom_{abs(hash(name))}",
                    link=link or f"https://google.com/search?q={query.replace(' ', '+')}+{platform.lower()}",
                ))
            except Exception:
                continue

        if products:
            log.info(f"  {platform} DOM fallback: found {len(products)} products")

    except Exception as e:
        log.warning(f"  {platform} DOM fallback failed: {e}")

    return products


async def _dom_fallback_zepto(page: Page, platform: str, query: str) -> list[Product]:
    """Zepto-specific DOM fallback with better product name extraction."""
    products: list[Product] = []
    try:
        await page.wait_for_timeout(5000)

        # Zepto uses link cards for products
        cards = await page.query_selector_all('a[href*="/pn/"]')
        if not cards:
            cards = await page.query_selector_all(
                '[data-testid="product-card"], '
                'div[class*="product-card"], '
                'div[role="listitem"]'
            )

        for card in cards:
            try:
                text = await card.inner_text()
                tl = text.lower()
                if "hot wheels" not in tl and "hotwheels" not in tl:
                    continue

                lines = [l.strip() for l in text.split("\n") if l.strip()]
                # Filter out junk: button labels, quantity selectors, short text
                SKIP = {"add", "added", "+", "-", "notify", "notify me",
                        "remove", "out of stock", "sold out", "qty", "1"}
                good_lines = [l for l in lines if l.lower() not in SKIP and len(l) > 3]

                # Product name is usually the longest meaningful line
                name = max(good_lines, key=len) if good_lines else "Hot Wheels (unknown)"

                price: str | float = "N/A"
                for line in lines:
                    if "₹" in line:
                        raw = line.replace("₹", "").replace(",", "").strip().split()[0]
                        try:
                            price = float(raw)
                        except ValueError:
                            price = raw
                        break

                in_stock = "out of stock" not in tl and "sold out" not in tl and "notify" not in tl

                href = await card.get_attribute("href") or ""
                link = href if href.startswith("http") else f"https://www.zeptonow.com{href}" if href else ""

                products.append(Product(
                    platform=platform,
                    name=name,
                    price=price,
                    in_stock=in_stock,
                    product_id=f"dom_{abs(hash(name))}",
                    link=link or f"https://www.zeptonow.com/search?query={query.replace(' ', '+')}",
                ))
            except Exception:
                continue

        if products:
            log.info(f"  {platform} DOM fallback: found {len(products)} products")

    except Exception as e:
        log.warning(f"  {platform} DOM fallback failed: {e}")

    return products


# =====================================================================
# ⏱️  MAIN SCAN LOOP
# =====================================================================
async def run_scan(ctx: BrowserContext) -> None:
    log.info("═══ Starting scan cycle ═══")

    scanners = []
    if ENABLE_BLINKIT:
        scanners.append(("Blinkit", scan_blinkit))
    if ENABLE_ZEPTO:
        scanners.append(("Zepto", scan_zepto))
    if ENABLE_INSTAMART:
        scanners.append(("Instamart", scan_instamart))

    # Run all scrapers concurrently
    tasks = [asyncio.create_task(fn(ctx)) for _, fn in scanners]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_found = 0
    total_in_stock = 0

    for (platform_name, _), result in zip(scanners, results):
        if isinstance(result, Exception):
            log.error(f"  {platform_name} crashed: {result}")
            continue

        for product in result:
            total_found += 1
            if product.in_stock:
                total_in_stock += 1
                send_alert(product.platform, product.name, product.price, product.link)
            else:
                mark_oos(product.platform, product.name)

    log.info(
        f"═══ Scan done: {total_found} products, "
        f"{total_in_stock} in stock. "
        f"Next in {SCAN_INTERVAL}s ═══\n"
    )


async def main() -> None:
    log.info("🏁 Hot Wheels Alert Bot — STARTING")
    log.info(f"📍 Location: {LATITUDE}, {LONGITUDE} ({AREA_NAME})")
    log.info(f"🔍 Query: '{SEARCH_QUERY}'")
    log.info(f"⏱️  Interval: {SCAN_INTERVAL}s")
    log.info(f"🖥️  Headless: {HEADLESS}")
    log.info(
        f"🛒 Platforms: "
        f"{'Blinkit ' if ENABLE_BLINKIT else ''}"
        f"{'Zepto ' if ENABLE_ZEPTO else ''}"
        f"{'Instamart' if ENABLE_INSTAMART else ''}"
    )
    if DRY_RUN:
        log.info("🧪 DRY RUN MODE — no Pushover alerts will be sent")
    elif not PUSHOVER_USER_KEY or not PUSHOVER_APP_TOKEN:
        log.error("PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN must be set when DRY_RUN is false.")
        sys.exit(1)

    # ── Send startup notification ────────────────────────────────
    send_alert(
        "BOT STATUS",
        f"Hot Wheels Alert Bot is LIVE — scanning every {SCAN_INTERVAL}s",
        "0",
        "https://railway.com",
        force=True,
    )

    # ── Start browser ────────────────────────────────────────────
    mgr = BrowserManager()
    ctx = await mgr.start()

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
            try:
                await run_scan(ctx)
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
