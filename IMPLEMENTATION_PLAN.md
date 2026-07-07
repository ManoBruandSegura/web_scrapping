# Implementation Plan — Features from `new_features.md`

Ordered easiest/best-first (value-for-effort). Each item names the real files
and functions to touch.

**Current architecture:** FastAPI (`app.py`) + Playwright scraper
(`scrape_leboncoin.py`) + vanilla JS SPA (`static/index.html`, `static/index.js`)
+ SQLite (`db.py` / `scraper.db`) + one-way Discord webhooks and desktop toasts.

## Already implemented — do NOT re-build

Checked against the code before writing this plan:

- **Price-drop alerts** (`new_features.md` #2) — already done. `price_drops` are
  computed and sent to both desktop toast and Discord (`app.py:347-419`).
- **Deal score in notifications** (#2) — already done. Discord messages render
  `🔥 DEAL … (median X €, -Y%)` (`app.py:318-320`), backed by
  `db.query_stats()` (median/min/avg per query).
- **Price history storage** (#2) — table + `db.add_price_history()` /
  `get_current_price()` already exist and are populated each cycle.
- **Per-query enable/disable** — already exists (`q.get("enabled")`,
  `app.py:484`).

What remains from #2 is only the **charts** widget (no Chart.js in the frontend
yet — the `fa-chart-*` classes are just FontAwesome icons).

---

## Tier 1 — quick wins (hours each, high value)

### 1. Block alerts (`new_features.md` #7) — START HERE
When Datadome blocks us it only hits the logs. Fire a Discord/desktop alert once.
- **Where:** the `BlockedError` handler in `perform_scraping_cycle`
  (`app.py:501-519`, just after `blocked_until` is set).
- **How:** reuse `send_discord_async()` (`app.py:159`) and
  `show_desktop_notification()` (`app.py:162`) — send
  `"⛔ Blocked (block #N). Backing off until HH:MM."`. Guard with a flag so it
  fires once per block, not every skipped cycle.
- **Effort:** ~15 lines. No new deps, no schema change. Best first task.

### 2. Mobile push via ntfy.sh (#8)
- **Where:** add `send_ntfy_sync/async` next to `send_discord_sync`
  (`app.py:149-160`) — a plain `urllib` POST of the message body to
  `https://ntfy.sh/<topic>`.
- **Wire in:** call it alongside `send_discord_async` in the new-listings,
  price-drop, and block-alert paths. New config key `ntfy_topic` (Settings tab +
  `config.json`); skip if empty.
- **Effort:** ~25 lines, no new deps (stdlib `urllib`).

### 3. Human-hours scheduling (#9)
Skip overnight scrapes (fewer bot signals, smaller footprint).
- **Where:** early in `perform_scraping_cycle` (`app.py:421`), next to the
  existing `blocked_until` skip — return early if `datetime.now().hour` is
  outside `[active_start, active_end)`.
- **Config:** `active_start` / `active_end` in `config.json` + Settings tab
  (default 8–23). Per-query pause already exists via `enabled`.
- **Effort:** ~15 lines. Pairs naturally with the anti-ban work.

## Tier 2 — medium (a day each)

### 4. Price-distribution charts (remainder of #2)
Turn the numeric Query Stats block into a *when-to-buy* signal.
- **Backend already provides the data:** `db.get_listings()` returns
  `price_history` per listing (`db.py:150-163`) and `db.query_stats()` returns
  median/min/avg per query — both already surfaced via `/api/status` and the
  listings endpoint.
- **Frontend:** add Chart.js and render (a) a price-over-time line per watched
  listing and (b) a price-distribution/median marker per query, in the existing
  "Query Stats" section (`static/index.html:173`). Bundle Chart.js locally (no
  CDN needed for a localhost app).
- **Effort:** mostly frontend (`static/index.js`), no backend change.

### 5. Proxy support (#1)
The decisive anti-ban lever, building on this session's backoff/persistent-profile work.
- **Where:** `p.chromium.launch_persistent_context(...)` in
  `perform_scraping_cycle` (`app.py:465-480`) — Playwright takes a `proxy=`
  argument directly.
- **Config:** `proxy` (single) or `proxies` (list to rotate per cycle) in
  `config.json` + Settings tab. On `BlockedError`, optionally rotate to the next
  proxy instead of only backing off.
- **Effort:** ~30 lines + a Settings field. Note: **paid** proxies to actually
  help; residential/mobile recommended.

### 6. Historical archiving (#5)
Keep a full record (description + image) so listings that vanish are still yours.
- **Scraper:** optionally open each ad's detail page to grab the description;
  download the primary image to a local `archive/` dir.
- **DB:** add `description` + `image_path` columns to `listings` (follow the
  existing `ALTER TABLE … ADD COLUMN` migration pattern in `db.init_db()`,
  `db.py:45-48`).
- **Effort:** medium — extra page loads raise the request footprint, so gate it
  behind a config toggle and only archive *new* listings.

## Tier 3 — big swings (multi-day; do last)

### 7. AI-powered listing analysis (#3)
Read the description to flag red flags ("écran cassé", "pour pièces", "no box")
and detect negotiability ("négociable", "urgent"), then score/filter.
- **Depends on** #6 (need the description text first).
- **Model:** use the Claude API — **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`)
  is the cost-effective choice for short classification calls. Add `anthropic` to
  `requirements.txt` and an API key to `.env`. (Load the `claude-api` skill when
  implementing.)
- **Effort:** large — new dependency, API key/cost, per-listing calls (batch and
  cache by URL to control spend).

### 8. Multi-platform support (#4)
Monitor the same product across Vinted / eBay / etc.
- **Refactor:** extract a scraper interface from `scrape_leboncoin.py` (a common
  `scrape(config) -> listings` contract) and add per-site modules; the
  query-driven cycle and notification path stay as-is.
- **Effort:** largest structural change. Do only after the site-specific anti-bot
  handling above is stable.

### 9. Interactive Discord bot (#6)
Two-way control (`/pause`, `/resume`, `/search`) instead of one-way webhooks.
- **Needs** a persistent gateway connection (e.g. `discord.py`) running alongside
  FastAPI — a second long-lived task in the `lifespan` handler, plus command
  handlers that mutate `scraper_state` / `config.json`.
- **Effort:** large and operationally heavier (bot token, gateway, always-on
  connection). Lowest priority.

---

## Suggested execution order

**1 → 2 → 3** first (all quick, all high-value, no new deps), then **4** (charts,
pure frontend on data you already have), then **5** (proxies) if bans persist.
Tiers 6–9 are larger projects to schedule individually.

## Verification per feature

- **Alerts (1, 2):** trigger the path and confirm the message arrives (force a
  `BlockedError` for #1; post a test message for #2 — a `test_webhook` endpoint
  already exists at `app.py:757`).
- **Scheduling (3):** set a narrow active window and confirm cycles skip outside
  it (check logs).
- **Charts (4):** load the dashboard, confirm the chart renders from live
  `price_history` / `query_stats`.
- **Proxy (5):** verify the outbound IP changed (log the IP seen on a test page).
- **Archiving/AI (6, 7):** confirm the new columns populate and a sample
  description is classified as expected.
