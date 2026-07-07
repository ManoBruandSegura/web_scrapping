# Update Log — 2026-07-07

Changes made during this session, in order.

---

## 1. Fix: auto-scraper stranded after laptop sleep/wake

**Problem:** Closing the laptop lid with the server running would leave the
auto-scraper stuck on wake — the dashboard showed "Scrape in 0m 0s" but nothing
fired until the auto-scraper was toggled off and back on.

**Root cause:** The background polling loop (`app.py`, `background_polling_loop`)
counted its wait with a per-second accumulator over `asyncio.sleep(1)`.
`asyncio.sleep` runs on the event loop's *monotonic* clock, which **freezes while
the system is suspended**. After wake, the backend still believed the full
interval remained, while the dashboard countdown (computed from the wall-clock
`next_run_time`) had already hit zero. The two clocks were decoupled, so the
loop could never self-correct — only tearing it down (off/on) restarted it.

**Fix:**
- The wait loop now compares **wall-clock** `datetime.now()` against
  `scraper_state["next_run_time"]` (the same value the dashboard reads) instead
  of counting elapsed sleeps. Waking past the deadline fires the cycle
  immediately.
- Added a warning log when a scheduled run was missed by more than 60s
  (`"Missed scheduled run by Ns (system sleep?) — scraping now."`), so wake
  behaviour is visible in the logs.
- Manual scrape (`POST /api/scrape`) now pushes `next_run_time` forward by one
  interval, so an auto-scrape doesn't fire immediately after a manual one.

**Also fixed alongside:**
- `requirements.txt` — the `playwright-stealth` line was corrupted into
  space-separated characters (`p l a y w r i g h t ...`), which broke
  `pip install -r`. Restored to `playwright-stealth==2.0.0`.
- `README.md` — corrected a stale line saying listings are stored in
  `listings.json`; data actually lives in a SQLite database (`scraper.db`).

**Files touched:** `app.py`, `requirements.txt`, `README.md`
**Status:** merged to `main`.

---

## 2. Anti-IP-ban improvements (free levers)

**Problem:** Leboncoin (protected by Datadome) would block our IP for a couple
of hours, and shortly after coming back we'd get blocked again quickly.

**Diagnosis:** Datadome scores on IP reputation, browser fingerprint, and
request pattern *together*. The existing setup fought the request pattern
(jitter, 30-min interval) but hurt the other two:
1. The block cooldown was a flat 60 min — coming back while the IP was still
   tainted just re-armed the block and reset the clock.
2. Every cycle launched a brand-new browser context, discarding the Datadome
   cookie, so each run looked like a fresh unknown client.
3. UA and viewport were randomised every cycle — from a single home IP, that
   inconsistency is itself a bot signal.

**Changes (all in `app.py`):**

- **Exponential backoff on blocks.** Added `consecutive_blocks` to
  `scraper_state`. On each `BlockedError` the cooldown doubles
  (`BLOCK_COOLDOWN_MINUTES` = 60 base) up to a cap
  (`BLOCK_COOLDOWN_MAX_MINUTES` = 480, i.e. 8h): 60 → 120 → 240 → 480 → 480…
  A clean cycle resets the counter to zero. This breaks the
  "re-banned within minutes" loop.

- **Persistent browser profile.** The scraping cycle now uses
  `launch_persistent_context(BROWSER_PROFILE_DIR, …)` instead of a fresh
  `new_context` each time. The Datadome cookie and fingerprint persist across
  cycles, so reputation accumulates instead of resetting. Profile stored in
  `.browser_profile/` (gitignored).

- **Stable fingerprint.** Removed the random UA list and random viewport.
  The cycle now uses one fixed `BROWSER_UA` (Chrome 124 on Windows, coherent
  with the host OS) and a fixed `1920×1080` viewport. Consistency reads as
  human on a fixed IP.

**Note:** Not tested against the live site — running a scrape purely to test
risks triggering the very block we're mitigating, and the benefit isn't
observable in a single request. Verified `app.py` compiles and the backoff
sequence caps correctly.

**Files touched:** `app.py`, `.gitignore`
**Status:** applied to the working tree (not yet committed at time of writing).

---

## Deferred / not done

- Persisting `is_running` across server restarts (declined earlier).
- Auth on API endpoints — fine for localhost-only use.
- Async config-file I/O — file is tiny, not worth it.
- Residential/mobile proxies — the decisive anti-ban lever, but paid; left for
  later if the free changes above aren't enough. (Planned in Tier 2.)

---

## [2026-07-07] Brainstormed Features

- Created `new_features.md` to outline potential future features like proxy support, market analytics, AI analysis, and more.
- Created `IMPLEMENTATION_PLAN.md` — an ordered (easiest-first) roadmap for those
  features. Noted that price-drop alerts and the deal score were **already
  implemented** in the codebase, so they were excluded from the plan.

---

## 3. Tier 1 features implemented

Implemented the three Tier-1 quick wins from `IMPLEMENTATION_PLAN.md`.

### 3a. Block alerts (`new_features.md` #7)
A Datadome block previously only appeared in the logs. Now, when the scraper is
blocked, it fires a one-time alert (desktop toast + Discord + ntfy) —
`"⛔ Scraper blocked (block #N). Backing off until HH:MM."` — from inside the
`BlockedError` handler in `perform_scraping_cycle` (`app.py`). Reuses the
existing `send_discord_async` / `show_desktop_notification` helpers.

### 3b. Mobile push via ntfy.sh (#8)
Added `send_ntfy_sync` / `send_ntfy` helpers (stdlib `urllib`, no new
dependency) that POST the alert body to `https://ntfy.sh/<topic>`. Wired into the
new-listings, price-drop, and block-alert paths. Controlled by a new
`ntfy_topic` config key (blank = disabled). The ntfy `Title` header is
ASCII-sanitised (emoji stripped) since headers must be latin-1.

### 3c. Human-hours scheduling (#9)
Added a `within_active_hours(config)` helper and gated the automated scrape in
`background_polling_loop` so cycles are skipped outside the configured window
(handles windows that cross midnight; unset = always on). **Manual "Scrape Now"
bypasses this** — the gate is in the loop, not in `perform_scraping_cycle`.
Controlled by new `active_start` / `active_end` config keys (hour 0–23).

### Config + UI
- Added `ntfy_topic`, `active_start`, `active_end` to `ConfigModel` and
  `load_config()` defaults (required, or the values would be dropped on save).
- Added matching fields to the Settings tab (`static/index.html`) and the
  load/save handlers (`static/index.js`).

**Files touched:** `app.py`, `static/index.html`, `static/index.js`
**Verification:** `app.py` compiles; `within_active_hours` logic checked with
asserts (daytime window, midnight-crossing window, always-on default).
**Status:** committed (`5f75da8`).

---

## 4. Tier 2 features implemented

Implemented the three Tier-2 features from `IMPLEMENTATION_PLAN.md`.

### 4a. Proxy support (`new_features.md` #1)
Route the browser through a proxy — the decisive anti-ban lever, building on this
session's backoff/persistent-profile work.
- Added `parse_proxy(config)` which turns a config URL
  (`http://user:pass@host:port`, also `socks5://…`) into Playwright's proxy dict,
  URL-decoding credentials. Passed as `proxy=` to `launch_persistent_context`
  (`None` = direct).
- New `proxy` config key + Settings-tab field.

### 4b. Price chart (remainder of `new_features.md` #2)
Turned the numeric Query Stats block into a visual market view.
- Added Chart.js (CDN, consistent with the existing FontAwesome/Google-Fonts CDN
  use) and a `<canvas>` in the Query Stats panel.
- `renderPriceChart()` draws a grouped bar chart of **min / median / avg price
  per query** from the `per_query` stats already in `/api/status`. The chart
  instance is created once and updated in place on each 2.5s poll (no flicker,
  no backend change).

### 4c. Historical archiving — image slice (`new_features.md` #5)
Keep a listing's image after the ad is pruned/deleted.
- **DB:** added `image_path` + `description` columns to `listings` (via the
  existing guarded `ALTER TABLE` migration in `db.init_db()`), plus
  `db.set_image_path()`.
- **Download:** `archive_image_sync/async` downloads the **already-scraped
  thumbnail URL** (a plain CDN GET — deliberately *no* extra detail-page visit,
  so it adds no block risk) to `archive/<sha1>.jpg`, dedupes on repeat, and
  records the path. Gated by a new `archive_images` config toggle (default off);
  only new listings are archived.
- **Deferred (`ponytail:`):** scraping the ad **description** needs a
  Datadome-protected detail-page visit per listing — left until proxies are in
  place. A *queryable* archive that survives DB pruning would need a separate
  archive table (image files already persist on disk regardless).

### Config + UI
- Added `proxy`, `archive_images` to `ConfigModel` and `load_config()` defaults.
- Added matching Settings-tab fields (`static/index.html`) and load/save handlers
  (`static/index.js`). `archive/` is gitignored.

**Files touched:** `app.py`, `db.py`, `static/index.html`, `static/index.js`,
`.gitignore`
**Verification:** `app.py`/`db.py` compile; `parse_proxy` checked with asserts
(host-only, credentialed with URL-encoded password, socks5); DB migration +
`set_image_path` + image download/dedup tested against a temp DB and a local
`file://` source; server boots clean and serves the chart canvas, proxy, and
archive fields.
**Status:** applied to the working tree (not yet committed at time of writing).

---

## Remaining (Tier 3, not started)

- AI-powered listing analysis (depends on description archiving).
- Multi-platform support (Vinted / eBay).
- Interactive Discord bot (two-way control).
