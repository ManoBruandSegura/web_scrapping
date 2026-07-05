# Leboncoin IP Block — What Happened & How It's Handled

## The Issue (2026-07-05)

Leboncoin's anti-bot system (Datadome) temporarily blocked the scraper's IP.
Instead of search results, it served a block page:

> **"Access is temporarily restricted"** — Automated (bot) activity on your network (IP 103.69.224.98)

This is **not a code bug**. It's the site reacting to the request pattern:
2 queries every 5 minutes, 24/7 (~576 requests/day), plus bursts of manual test
scrapes while debugging.

Key facts:

- The block is **temporary** — it lifts within minutes to a few hours, **but only
  if the traffic stops**.
- Before the fix, the scraper couldn't tell a block page from an empty results
  page, so it kept retrying every cycle — which keeps the block alive.

## How the Code Handles It Now

### `scrape_leboncoin.py`

- Immediately after page load, the scraper checks the page text for block-page
  phrases ("Access is temporarily restricted", "unusual activity", and French
  variants).
- If detected, it raises a `BlockedError` instead of falling through to DOM
  scraping / debug screenshots.

### `app.py`

- On `BlockedError`, the current cycle **aborts immediately** (remaining queries
  are skipped — no point hitting the site again from a blocked IP).
- All scraping is **paused for 60 minutes** (`BLOCK_COOLDOWN_MINUTES`).
- The background loop keeps running but skips cycles until the cooldown
  expires. The logs show when scraping will resume:

  ```
  IP block cooldown active until <time>. Skipping cycle to let the block expire.
  ```

## What To Do When Blocked

1. **Stop scraping** (or just let the automatic cooldown do its job).
2. Wait 1–2 hours.
3. Try **one** manual scrape. If the block page appears again, the cooldown
   re-arms automatically — don't retry in a loop.

## Prevention

- **Raise the polling interval** to 15–30 minutes (Settings tab). New listings
  don't appear often enough for 5-minute polling to help, and it cuts the
  request footprint by 3–6×.
- **Avoid bursts of manual scrapes** — several rapid "Scrape Now" clicks look
  exactly like bot activity.
- **Don't try to defeat the detection** (fingerprint spoofing, proxy rotation,
  etc.). It's an arms race that escalates from temporary IP blocks to longer
  ones. Backing off is what actually gets you unblocked.

## Tunables

| Setting | Location | Default |
|---|---|---|
| `BLOCK_COOLDOWN_MINUTES` | `app.py` | 60 |
| Block-page detection phrases | `scrape_leboncoin.py` (after `page.goto`) | EN + FR variants |
| Polling interval | Settings tab / `config.json` (`interval_minutes`) | 5 (recommend 15–30) |
