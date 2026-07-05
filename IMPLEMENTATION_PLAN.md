# LBC Scraper — Implementation Plan

Roadmap for the 10 planned features, ordered by dependency and value-for-effort.
Current architecture: FastAPI (`app.py`) + Playwright scraper (`scrape_leboncoin.py`) +
vanilla JS SPA (`static/`) + JSON file persistence (`config.json`, `listings.json`).

**Phases:**

| Phase | Features | Theme |
|-------|----------|-------|
| 1 | SQLite migration | Storage foundation (everything else builds on it) |
| 2 | Richer scrape data (thumbnails, location), price history + drop alerts | Data capture |
| 3 | Price stats, good-deal scoring, new/seen state | Analysis & UX |
| 4 | Run history, polling jitter | Robustness |
| 5 | Per-query notifications, Telegram | Alerting |

Phase 1 is listed first because price history (#1) and run history (#6) would strain the
rewrite-whole-JSON-file model. If you want to defer it, Phase 2+ can still be done on JSON —
each section notes the JSON fallback.

---

## Phase 1 — SQLite storage (#10)

**Goal:** replace `listings.json` with a `sqlite3` (stdlib) database; keep `config.json` as-is
(it's small, human-editable, and Pydantic-validated).

### Schema

```sql
CREATE TABLE listings (
    url            TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    price          TEXT,               -- raw string as scraped ("1 450 €")
    price_value    REAL,               -- parsed numeric (nullable)
    query_id       TEXT,
    first_seen     TEXT NOT NULL,      -- ISO datetime
    last_seen      TEXT NOT NULL,
    published_date TEXT,
    thumbnail_url  TEXT,               -- Phase 2
    location       TEXT,               -- Phase 2
    viewed         INTEGER DEFAULT 0   -- Phase 3
);

CREATE TABLE price_history (            -- Phase 2
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL REFERENCES listings(url) ON DELETE CASCADE,
    price_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE run_history (              -- Phase 4
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    items_seen  INTEGER DEFAULT 0,
    items_new   INTEGER DEFAULT 0,
    blocked     INTEGER DEFAULT 0,
    error       TEXT
);
```

### Steps

1. New module `db.py`: `get_conn()` (open `scraper.db`, `PRAGMA journal_mode=WAL`,
   `PRAGMA foreign_keys=ON`), `init_db()` with the schema above, and CRUD helpers
   (`upsert_listing`, `get_listings(search, sort, query_id)`, `delete_all_listings`, …).
2. One-time migration in `lifespan()` (`app.py:444`): if `listings.json` exists and the DB
   is empty, import every record then rename the file to `listings.json.bak`. Mirrors the
   existing `seen_items.json` migration pattern already there.
3. Replace the in-memory `listings_db` list + `load_listings`/`save_listings`
   (`app.py:132-154`) with `db.py` calls. `process_scraped_listings` (`app.py:216`) becomes
   an upsert loop; the 3-day pruning becomes one `DELETE WHERE last_seen < ?`.
4. Move search/sort in `/api/listings` (`app.py:517`) into SQL (`ORDER BY price_value`,
   `LIKE` on title). Keep `parse_price` (`app.py:16`) to populate `price_value` on write.
5. Add `scraper.db*` to `.gitignore` (WAL creates `-wal`/`-shm` side files).

**Testing:** start app with an existing `listings.json`, confirm migration count in logs;
run a scrape cycle; exercise search/sort/delete from the UI.

---

## Phase 2 — Richer data capture

### 2a. Thumbnails + location (#4)

The fast path already parses `__NEXT_DATA__` ads (`scrape_leboncoin.py:61-82`). Each `ad`
object carries `images.thumb_url` (and `images.urls[]`) plus `location.city` / `location.zipcode`.

1. In the `__NEXT_DATA__` evaluate, extend the mapped object:
   ```js
   thumbnail_url: ad.images?.thumb_url || ad.images?.urls?.[0] || null,
   location: ad.location ? [ad.location.city, ad.location.zipcode].filter(Boolean).join(' ') : null,
   ```
2. DOM fallback (`scrape_leboncoin.py:109`): best-effort — first `img[src]` inside the card
   for the thumbnail; location often appears as a `<p>` with a postcode pattern
   (`/\b\d{5}\b/`). Return `null` when not found; the UI must handle missing values.
3. Persist both fields in `process_scraped_listings` (update on every sighting, like
   price/title today at `app.py:228-230`).
4. UI (`static/index.js` `renderListings`, `static/index.css`): add an image block at the top
   of `.listing-card` — fixed `aspect-ratio: 4/3`, `object-fit: cover`, `loading="lazy"`,
   and a phosphor-styled placeholder (icon on `--surface-2`) when `thumbnail_url` is null.
   Add a location line with a map-pin icon next to the dates.

*Note: hotlinking LBC image CDN generally works but may break; the placeholder path is the
safety net, no proxying needed initially.*

### 2b. Price history + drop alerts (#1)

1. In `process_scraped_listings`, when an existing listing's parsed price differs from the
   stored `price_value`, insert a `price_history` row **and** collect it into a
   `price_drops` list when the new value is lower.
2. Also insert one history row when a listing is first created (baseline point).
3. Notifications: extend the existing notification block (`app.py:266-325`) to send a second
   message for drops, e.g. `📉 Price drop: <title> 450 € → 390 € (-13%)` — reuse
   `send_discord_async` and `show_desktop_notification`; respect the same first-run
   suppression flag.
4. API: `GET /api/listings/history?url=...` returning `[{recorded_at, price_value}]`.
5. UI: on listing cards with ≥2 history points, render an inline SVG sparkline (no chart
   library needed — a single `<polyline>` sized ~100×24 in accent green, or `--danger` red
   if the latest move is up). Show `▼ -13%` badge next to the price when the last change
   was a drop.

**JSON fallback (if Phase 1 deferred):** store `price_history: [[iso, value], ...]` per item
in `listings.json` — works, but the file grows and the whole-file rewrite gets slower.

---

## Phase 3 — Analysis & UX

### 3a. Price stats per query (#2)

1. `db.py`: `query_stats()` → per `query_id`: `COUNT`, `MIN`, `AVG`, median (compute median
   in Python from sorted `price_value`s; SQLite lacks a built-in).
2. Expose under `stats.per_query` in `/api/status` (`app.py:480`).
3. UI: new dashboard panel "Query Stats" — one row per query: name, item count,
   min / median / avg in mono tabular figures. Also show median in the listings filter bar
   for the currently selected query.

### 3b. Good-deal scoring (#8) — depends on 3a

1. Definition: a listing is a **deal** when `price_value <= (1 - threshold) * median(query)`,
   default `threshold = 0.25`, only when the query has ≥ N (default 5) priced items —
   otherwise medians are noise.
2. Config: add `deal_threshold_pct: int = 25` and `deal_min_sample: int = 5` to
   `ConfigModel` (`app.py:88`) + two inputs in the Global Settings form.
3. Compute at scrape time in `process_scraped_listings` for **new** items; deals get a
   priority notification: `🔥 Deal: <title> 290 € (median 410 €, -29%)`.
4. Store `is_deal INTEGER` on the listing row; UI shows a flame badge on the card and a
   "Deals only" checkbox in the listings filter bar.

### 3c. New / seen state (#3)

1. `viewed` column already in the Phase 1 schema. New items insert with `viewed = 0`.
2. API: `POST /api/listings/mark-viewed` with `{urls: [...]}` (batch), and change the
   nav badge source: `/api/status` returns `stats.unseen_count` (`WHERE viewed = 0`)
   instead of the total (`static/index.js:262`).
3. UI behavior: unseen cards get an accent left border + `NEW` badge. When the listings tab
   renders, start a 2s timer; visible unseen cards are then marked viewed (batch call).
   Clicking "View Item" marks that card immediately. Badge disappears; nav count updates
   on next poll.
4. Keep it JS-simple: no IntersectionObserver initially — mark everything rendered in the
   current filter after the timer. Refine later if listings grow large.

---

## Phase 4 — Robustness

### 4a. Scrape run history (#6)

1. Insert a `run_history` row at the start of `perform_scraping_cycle` (`app.py:329`);
   update it in the `finally` block with counts, `blocked` flag (set where `BlockedError`
   is caught at `app.py:381`), and any fatal error string.
2. `process_scraped_listings` returns `(items_seen, items_new)` so the cycle can record them.
3. API: `GET /api/runs?limit=50`.
4. UI: dashboard "Run History" panel — compact bar chart (inline SVG, one bar per run,
   height = new items, red bar when blocked) + a small table of the last 10 runs
   (time, seen, new, status). Keep only the last ~500 rows (`DELETE` older on insert).

### 4b. Randomized polling jitter (#7)

1. In `background_polling_loop` (`app.py:410`): after computing `interval`, apply
   `interval = int(interval * random.uniform(0.8, 1.2))`.
2. `next_run_time` is already computed from the same value, so the dashboard countdown
   stays truthful.
3. Also jitter the fixed 5s inter-query delay (`app.py:394`): `random.uniform(4, 9)`.

---

## Phase 5 — Alerting

### 5a. Per-query notification settings (#5)

1. `QueryModel` (`app.py:75`): add `notify: bool = True` and
   `webhook_override: Optional[str] = ""`.
2. Notification block (`app.py:285-325`): it already groups new items per query via
   `query_map` — change the loop to resolve each query's target
   (muted → skip; override set → that webhook; else global webhook) and build one message
   per target instead of one global message. Keep the 1900-char chunking helper, extracted
   into a `chunk_message(text)` function.
3. Deal/drop alerts (Phases 2b/3b) route through the same resolution.
4. UI: in the query modal, add a "Notifications" section — mute toggle + optional webhook
   URL field. Show a muted-bell icon on muted query cards in Settings.

### 5b. Telegram notifier (#9)

1. Config: `telegram_bot_token: Optional[str]` and `telegram_chat_id: Optional[str]` in
   `ConfigModel` + Global Settings inputs with a "Test" button.
2. New function alongside `send_discord_sync` (`app.py:156`):
   ```python
   def send_telegram_sync(token, chat_id, text):
       url = f"https://api.telegram.org/bot{token}/sendMessage"
       # urllib.request POST, json {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
   ```
   No new dependency — same `urllib.request` pattern. Telegram's limit is 4096 chars;
   reuse `chunk_message` with a per-service limit parameter.
3. Refactor suggestion: introduce `async def broadcast(message, query=None)` that fans out
   to every configured channel (desktop toast, Discord, Telegram) honoring per-query
   settings — one call site for all three alert types (new / drop / deal).
4. `POST /api/test-telegram` mirroring `test_webhook` (`app.py:557`).

---

## Cross-cutting notes

- **Backward compatibility of config:** every new `ConfigModel` field needs a default so
  existing `config.json` files load unchanged (Pydantic handles this if defaults are set).
- **Frontend cache busting:** bump `?v=` on `index.css` / `index.js` with each phase.
- **UI style:** all new components follow the Field Station theme — mono uppercase labels,
  `--radius: 2px`, accent green for positive signals, `--danger` for drops-in-your-favor
  is *not* used (a price drop is good news here: use accent green for drop badges,
  amber for deals).
- **No new Python dependencies** are required by any phase (sqlite3, random, urllib are stdlib).
- **Verification per phase:** run the app, trigger a manual scrape against a cheap query,
  and check: DB rows created, dashboard panels populated, one real Discord/Telegram test
  message. Playwright screenshot pass (same script as the UI remake) for visual regressions.

## Suggested order of attack

1. Phase 1 (storage) — ~half a day, unlocks everything.
2. Phase 2a (thumbnails/location) — quick win, biggest visible improvement.
3. Phase 2b (price history + drop alerts) — the headline feature.
4. Phase 4b (jitter) — 15 minutes, do it whenever touching `app.py`.
5. Phase 3a → 3b → 3c, then 4a, then Phase 5.
