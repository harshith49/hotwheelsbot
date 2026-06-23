# Walkthrough — Swapping Big Basket for Zepto

We have successfully replaced the Big Basket scraper with a new Zepto scraper, updated the scan runner and startup logs, and verified the entire codebase through a local dry-run scan.

## Changes Made

### 1. Replaced Big Basket with Zepto in [bot.py](file:///Users/harshithpuvvada/mm%20project/untitled%20folder/bot.py)
- Removed `_dom_fallback_bigbasket` and `scan_bigbasket` scraper methods.
- Added `_dom_fallback_zepto` to extract product elements from the Zepto search pages (using `/pn/` href schema) and identify name, price, and stock status.
- Added `scan_zepto` to navigate to Zepto, mock GPS coordinates to Bangalore/Bellandur (`12.924674, 77.694803`), and perform search-query execution.
- Updated the `run_scan` cycle to run Blinkit and Zepto sequentially.
- Updated cleanup tracking references to check `zepto` instead of `big basket` for items disappearing from the catalog.
- Updated `main()` startup information logs to print `Platforms: Blinkit, Zepto`.

---

## Verification Results

### Local Dry Run Scan
Successfully ran a dry-run local scan using:
```bash
DRY_RUN=true HEADLESS=true .venv/bin/python bot.py
```

- **Location Initialization:** Both Blinkit (location field typing) and Zepto (GPS mock button clicks) resolved successfully to Bellandur coordinates.
- **Aggregation:**
  - **Blinkit:** Scraped **20 Hot Wheels products** from the DOM.
  - **Zepto:** Scraped **29 Hot Wheels products** from the DOM.
  - Total scanned: **49 products**, with all dry-run notification mocks sending to stdout correctly.
