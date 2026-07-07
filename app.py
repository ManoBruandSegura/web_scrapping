import asyncio
import json
import os
import urllib.request
import logging
import re
from collections import deque
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from plyer import notification

def parse_price(price_str: str) -> Optional[float]:
    # Handles French formats like "1 450,00 \u20ac" (incl. \xa0 / \u202f separators)
    try:
        match = re.search(r"(\d[\d\s\xa0\u202f]*(?:,\d+)?)", price_str or "")
        if match:
            cleaned = re.sub(r"[\s\xa0\u202f]", "", match.group(1)).replace(",", ".")
            return float(cleaned)
    except Exception:
        pass
    return None

from scrape_leboncoin import scrape_leboncoin, BlockedError

# Set up directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

# Set up logging and log queue
logs_deque = deque(maxlen=500)

class AppLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        logs_deque.append(log_entry)

logger = logging.getLogger("scraper")
logger.setLevel(logging.INFO)

# Standard formatter
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Queue handler
q_handler = AppLogHandler()
q_handler.setFormatter(formatter)
logger.addHandler(q_handler)

# Stdout handler
c_handler = logging.StreamHandler()
c_handler.setFormatter(formatter)
logger.addHandler(c_handler)

# Files
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")

import db

# State
scraper_state = {
    "is_running": False,
    "is_scraping": False,
    "last_run_time": None,
    "next_run_time": None,
    "blocked_until": None,
    "consecutive_blocks": 0,
}
BLOCK_COOLDOWN_MINUTES = 60          # base cooldown, doubled per consecutive block
BLOCK_COOLDOWN_MAX_MINUTES = 480     # cap the backoff at 8h

# Stable browser identity — a coherent, fixed fingerprint reads as human on a
# single home IP; rotating UA/viewport every cycle is itself a bot signal.
BROWSER_PROFILE_DIR = os.path.join(BASE_DIR, ".browser_profile")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BROWSER_VIEWPORT = {"width": 1920, "height": 1080}

# Config Pydantic Schema
class QueryModel(BaseModel):
    id: str
    name: str
    mode: str = Field(..., pattern="^(query|url)$")
    query: Optional[str] = ""
    custom_url: Optional[str] = ""
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    shippable: bool = True
    enabled: bool = True
    required_keywords: Optional[str] = ""
    excluded_keywords: Optional[str] = ""
    notify: bool = True
    webhook_override: Optional[str] = ""

class ConfigModel(BaseModel):
    queries: List[QueryModel] = []
    interval_minutes: int = Field(5, ge=1)
    discord_webhook: Optional[str] = ""
    headless: bool = True
    deal_threshold_pct: int = 25
    deal_min_sample: int = 5
    ntfy_topic: Optional[str] = ""
    active_start: Optional[int] = None   # hour [0-23]; None = always active
    active_end: Optional[int] = None

# Helper functions
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading config: {e}")
    # Defaults
    return {
        "queries": [
            {
                "id": "q1",
                "name": "Ryzen 9 5950x",
                "mode": "query",
                "query": "ryzen 9 5950x",
                "custom_url": "",
                "price_min": 300,
                "price_max": None,
                "shippable": True,
                "enabled": True,
                "required_keywords": "",
                "excluded_keywords": ""
            }
        ],
        "interval_minutes": 5,
        "discord_webhook": "",
        "headless": True,
        "deal_threshold_pct": 25,
        "deal_min_sample": 5,
        "ntfy_topic": "",
        "active_start": None,
        "active_end": None
    }

def save_config(config: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved successfully.")
    except Exception as e:
        logger.error(f"Error saving config: {e}")



def send_discord_sync(webhook_url: str, message: str):
    data = {"content": message}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.read()

async def send_discord_async(webhook_url: str, message: str):
    await asyncio.to_thread(send_discord_sync, webhook_url, message)

async def show_desktop_notification(title: str, message: str):
    try:
        await asyncio.to_thread(
            notification.notify,
            title=title,
            message=message,
            app_name="Leboncoin Scraper",
            timeout=10
        )
    except Exception as e:
        logger.error(f"Failed to show desktop notification: {e}")

def send_ntfy_sync(topic: str, title: str, message: str):
    # ntfy.sh: POST the body to /<topic>. Title header must be ASCII (latin-1).
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title.encode("ascii", "ignore").decode(), "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.read()

async def send_ntfy(config: dict, title: str, message: str):
    topic = (config.get("ntfy_topic") or "").strip()
    if not topic:
        return
    try:
        await asyncio.to_thread(send_ntfy_sync, topic, title, message)
    except Exception as e:
        logger.error(f"Failed to send ntfy notification: {e}")

def within_active_hours(config: dict) -> bool:
    """True if scraping is allowed now. Unset start/end = always active."""
    start = config.get("active_start")
    end = config.get("active_end")
    if start is None or end is None:
        return True
    hour = datetime.now().hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # window crossing midnight

def filter_listings(listings: List[dict], q: dict) -> List[dict]:
    req_keywords = [k.strip().lower() for k in (q.get("required_keywords") or "").split(",") if k.strip()]
    ex_keywords = [k.strip().lower() for k in (q.get("excluded_keywords") or "").split(",") if k.strip()]
    price_min = q.get("price_min")
    price_max = q.get("price_max")

    filtered = []
    for item in listings:
        title_lower = item.get("title", "").lower()

        # Required keywords: must contain at least one
        if req_keywords and not any(req in title_lower for req in req_keywords):
            logger.info(f"Skipping listing '{item.get('title')}' - missing required keywords ({q.get('required_keywords')})")
            continue

        # Excluded keywords: must contain none
        if ex_keywords and any(ex in title_lower for ex in ex_keywords):
            logger.info(f"Skipping listing '{item.get('title')}' - matches excluded keywords ({q.get('excluded_keywords')})")
            continue

        # Strict price filtering (skipped when price can't be parsed)
        if price_min is not None or price_max is not None:
            p_val = parse_price(item.get("price", ""))
            if p_val is not None:
                if price_min is not None and p_val < price_min:
                    logger.info(f"Skipping listing '{item.get('title')}' - price {p_val} below min {price_min}")
                    continue
                if price_max is not None and p_val > price_max:
                    logger.info(f"Skipping listing '{item.get('title')}' - price {p_val} above max {price_max}")
                    continue

        item["query_id"] = q.get("id")
        filtered.append(item)
    return filtered

async def process_scraped_listings(scraped_items: List[dict]):
    is_first_run = db.get_listing_count() == 0
    new_items = []
    price_drops = []
    now_str = datetime.now().isoformat()
    
    config_dict = load_config()
    deal_threshold_pct = config_dict.get("deal_threshold_pct", 25)
    deal_min_sample = config_dict.get("deal_min_sample", 5)
    stats = db.query_stats()
    
    for item in scraped_items:
        # Prepare item data with default values
        item_data = {
            "title": item["title"],
            "price": item["price"],
            "price_value": parse_price(item["price"]),
            "url": item["url"],
            "first_seen": now_str,
            "last_seen": now_str,
            "published_date": item.get("published_date"),
            "thumbnail_url": item.get("thumbnail_url"),
            "location": item.get("location")
        }
        
        # Check if deal
        item_data["is_deal"] = 0
        if item_data["price_value"] is not None and item.get("query_id"):
            q_id = item["query_id"]
            if q_id in stats and stats[q_id]["count"] >= deal_min_sample:
                median_price = stats[q_id]["median"]
                if item_data["price_value"] <= (1 - deal_threshold_pct / 100.0) * median_price:
                    item_data["is_deal"] = 1
                    item_data["median"] = median_price
        
        old_price = db.get_current_price(item["url"])
        
        # upsert_listing returns True if it's a new item, False if it was updated
        is_new = db.upsert_listing(item_data, item.get("query_id"))
        if is_new:
            new_items.append(item_data)
            if item_data["price_value"] is not None:
                db.add_price_history(item["url"], item_data["price_value"])
        else:
            new_price = item_data["price_value"]
            if new_price is not None and old_price is not None and new_price != old_price:
                db.add_price_history(item["url"], new_price)
                if new_price < old_price:
                    item_data["old_price"] = old_price
                    item_data["query_id"] = item.get("query_id")
                    price_drops.append(item_data)
            
    # Prune items not seen in 3 days
    three_days_ago = (datetime.now() - timedelta(days=3)).isoformat()
    pruned_count = db.prune_old_listings(three_days_ago)
    if pruned_count > 0:
        logger.info(f"Pruned {pruned_count} old listings from database.")
    
    if new_items:
        logger.info(f"Found {len(new_items)} new listings!")
        if is_first_run:
            logger.info("First run detected. Suppressing notifications for initial batch.")
            return len(scraped_items), len(new_items)
        
        # Build Desktop Toast (Summarized)
        if len(new_items) == 1:
            title = "New Leboncoin Listing!"
            message = f"{new_items[0]['title']} - {new_items[0]['price']}"
        else:
            title = f"{len(new_items)} New Leboncoin Listings!"
            message = "\n".join([f"{item['title']} - {item['price']}" for item in new_items[:3]])
            if len(new_items) > 3:
                message += f"\n...and {len(new_items) - 3} more."
                
        # Desktop Toast
        await show_desktop_notification(title, message)

        # Discord Notification
        config = load_config()
        await send_ntfy(config, title.replace("!", ""), message)
        global_webhook = config.get("discord_webhook")
        queries = {q["id"]: q for q in config.get("queries", [])}
        
        # Group items by webhook URL
        webhook_groups = {}
        for item in new_items:
            q_id = item.get("query_id")
            q = queries.get(q_id, {})
            
            if not q.get("notify", True):
                continue
                
            webhook = q.get("webhook_override") or global_webhook
            if not webhook or not webhook.strip():
                continue
                
            if webhook not in webhook_groups:
                webhook_groups[webhook] = {}
                
            q_name = q.get("name", "Other")
            if q_name not in webhook_groups[webhook]:
                webhook_groups[webhook][q_name] = []
            webhook_groups[webhook][q_name].append(item)
            
        for webhook_url, grouped_items in webhook_groups.items():
            total_items = sum(len(items) for items in grouped_items.values())
            full_msg = f"🔔 **{total_items} New Leboncoin Listings!**\n\n"
            for q_name, items in grouped_items.items():
                full_msg += f"__**{q_name}** ({len(items)} items):__\n"
                for item in items:
                    if item.get("is_deal") and item.get("median"):
                        pct = round((item["median"] - item["price_value"]) / item["median"] * 100)
                        full_msg += f"- 🔥 **DEAL**: [{item['title']}]({item['url']}) - **{item['price']}** (median {round(item['median'])} €, -{pct}%)\n"
                    else:
                        full_msg += f"- [{item['title']}]({item['url']}) - **{item['price']}**\n"
                full_msg += "\n"
            
            # Discord has a 2000 character limit per message, chunk it if necessary
            chunks = []
            current_chunk = ""
            for line in full_msg.split("\n"):
                if len(current_chunk) + len(line) + 1 > 1900:
                    chunks.append(current_chunk)
                    current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"
            if current_chunk.strip():
                chunks.append(current_chunk)
                
            for chunk in chunks:
                if not chunk.strip(): continue
                try:
                    await send_discord_async(webhook_url, chunk.strip())
                    await asyncio.sleep(0.5) # brief pause to prevent rate-limiting
                except Exception as de:
                    logger.error(f"Failed to send Discord webhook chunk: {de}")
        if webhook_groups:
            logger.info("Discord notifications for new items sent successfully.")

    if price_drops:
        logger.info(f"Found {len(price_drops)} price drops!")
        if is_first_run:
            logger.info("First run detected. Suppressing drop notifications for initial batch.")
        else:
            # Desktop Toast
            if len(price_drops) == 1:
                drop = price_drops[0]
                pct = int((drop["old_price"] - drop["price_value"]) / drop["old_price"] * 100)
                title = "Price Drop Alert!"
                message = f"{drop['title']} dropped {pct}% to {drop['price']}"
            else:
                title = f"{len(price_drops)} Price Drops!"
                message = "\n".join([f"{d['title']} dropped to {d['price']}" for d in price_drops[:3]])
                if len(price_drops) > 3:
                    message += f"\n...and {len(price_drops) - 3} more."
                    
            await show_desktop_notification(title, message)

            # Discord Notification
            config = load_config()
            await send_ntfy(config, title.replace("!", ""), message)
            global_webhook = config.get("discord_webhook")
            queries = {q["id"]: q for q in config.get("queries", [])}

            # Group items by webhook URL
            webhook_groups = {}
            for drop in price_drops:
                q_id = drop.get("query_id")
                q = queries.get(q_id, {})
                
                if not q.get("notify", True):
                    continue
                    
                webhook = q.get("webhook_override") or global_webhook
                if not webhook or not webhook.strip():
                    continue
                    
                if webhook not in webhook_groups:
                    webhook_groups[webhook] = {}
                    
                q_name = q.get("name", "Other")
                if q_name not in webhook_groups[webhook]:
                    webhook_groups[webhook][q_name] = []
                webhook_groups[webhook][q_name].append(drop)
                
            for webhook_url, grouped_drops in webhook_groups.items():
                total_drops = sum(len(items) for items in grouped_drops.values())
                full_msg = f"📉 **{total_drops} Price Drops Detected!**\n\n"
                for q_name, items in grouped_drops.items():
                    full_msg += f"**{q_name}**\n"
                    for drop in items:
                        pct = round((drop["old_price"] - drop["price_value"]) / drop["old_price"] * 100)
                        full_msg += f"• [{drop['title']}]({drop['url']}) : ~~{round(drop['old_price'])} €~~ **{drop['price']}** (-{pct}%)\n"
                    full_msg += "\n"
                    
                # Reuse chunking
                chunks = []
                current_chunk = ""
                for line in full_msg.split("\n"):
                    if len(current_chunk) + len(line) + 1 > 1900:
                        chunks.append(current_chunk)
                        current_chunk = line + "\n"
                    else:
                        current_chunk += line + "\n"
                if current_chunk.strip():
                    chunks.append(current_chunk)
                    
                for chunk in chunks:
                    if not chunk.strip(): continue
                    try:
                        await send_discord_async(webhook_url, chunk.strip())
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Failed to send Discord price drop chunk: {e}")
            if webhook_groups:
                logger.info("Discord price drop notifications sent successfully.")
    
    if not new_items and not price_drops:
        logger.info("Scraping cycle complete. No new items or drops found.")
        
    return len(scraped_items), len(new_items)

async def perform_scraping_cycle():
    if scraper_state["is_scraping"]:
        logger.info("Scraping already in progress. Skipping.")
        return

    blocked_until = scraper_state.get("blocked_until")
    if blocked_until and datetime.now() < datetime.fromisoformat(blocked_until):
        logger.warning(f"IP block cooldown active until {blocked_until}. Skipping cycle to let the block expire.")
        return
    scraper_state["blocked_until"] = None

    scraper_state["is_scraping"] = True
    
    # Run history tracking
    started_at = datetime.now().isoformat()
    items_seen = 0
    items_new = 0
    blocked_flag = 0
    error_msg = None
    
    try:
        config = load_config()
        headless = config.get("headless", True)
        queries = config.get("queries", [])
        
        if not queries:
            logger.info("No queries configured. Skipping cycle.")
        else:
            logger.info(f"Starting scraping cycle for {len(queries)} queries...")
            all_listings = []
            

            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                logger.info(f"Launching browser (headless={headless}) with persistent profile...")
                # Persistent context keeps the Datadome cookie and fingerprint
                # across cycles, so we accumulate reputation instead of showing up
                # as an unknown client every cycle.
                context = await p.chromium.launch_persistent_context(
                    BROWSER_PROFILE_DIR,
                    headless=headless,
                    user_agent=BROWSER_UA,
                    viewport=BROWSER_VIEWPORT,
                    locale="fr-FR",
                    timezone_id="Europe/Paris",
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-web-security",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ]
                )

                try:
                    for i, q in enumerate(queries):
                        if not q.get("enabled", True):
                            continue
                            
                        logger.info(f"Processing query: {q.get('name', 'Unnamed')}")
                        scrape_config = {
                            "mode": q.get("mode", "query"),
                            "query": q.get("query", ""),
                            "custom_url": q.get("custom_url", ""),
                            "price_min": q.get("price_min"),
                            "price_max": q.get("price_max"),
                            "shippable": q.get("shippable", True)
                        }
                        
                        try:
                            listings = await scrape_leboncoin(scrape_config, logger, context=context)
                            filtered_listings = filter_listings(listings, q)
                            all_listings.extend(filtered_listings)
                        except BlockedError as be:
                            # Exponential backoff: each consecutive block doubles the
                            # wait (capped). Coming back too soon while the IP is still
                            # tainted just re-arms the block, so back off harder.
                            scraper_state["consecutive_blocks"] += 1
                            cooldown_min = min(
                                BLOCK_COOLDOWN_MINUTES * (2 ** (scraper_state["consecutive_blocks"] - 1)),
                                BLOCK_COOLDOWN_MAX_MINUTES,
                            )
                            cooldown_end = datetime.now() + timedelta(minutes=cooldown_min)
                            scraper_state["blocked_until"] = cooldown_end.isoformat()
                            blocked_flag = 1
                            error_msg = str(be)
                            logger.error(
                                f"{be} Block #{scraper_state['consecutive_blocks']}. Aborting cycle and "
                                f"pausing all scraping for {cooldown_min} min (until {cooldown_end.strftime('%H:%M')}). "
                                f"Continuing to send requests would extend the block."
                            )
                            # Alert once per block so it's visible outside the logs
                            try:
                                alert_msg = (
                                    f"Scraper blocked (block #{scraper_state['consecutive_blocks']}). "
                                    f"Backing off until {cooldown_end.strftime('%H:%M')}."
                                )
                                await show_desktop_notification("Leboncoin Scraper Blocked", alert_msg)
                                webhook = config.get("discord_webhook")
                                if webhook and webhook.strip():
                                    await send_discord_async(webhook, f"⛔ **{alert_msg}**")
                                await send_ntfy(config, "Leboncoin Scraper Blocked", alert_msg)
                            except Exception as alert_err:
                                logger.error(f"Failed to send block alert: {alert_err}")
                            break
                        except Exception as e:
                            error_msg = str(e)
                            logger.error(f"Error scraping query {q.get('name')}: {e}")
                            
                        if i < len(queries) - 1:
                            # 4b. Jitter delay between queries to seem less robotic (4 to 9 seconds)
                            import random
                            delay = random.uniform(4, 9)
                            logger.info(f"Waiting {delay:.1f}s before next query to avoid rate limits...")
                            await asyncio.sleep(delay)
                finally:
                    logger.info("Closing browser for cycle...")
                    await context.close()
                
            if blocked_flag == 0:
                # Reached the site without a block — clear the backoff.
                scraper_state["consecutive_blocks"] = 0

            logger.info(f"Cycle complete. Processing {len(all_listings)} total matched items.")
            seen, new = await process_scraped_listings(all_listings)
            if seen is not None:
                items_seen = seen
                items_new = new
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Cycle failed with exception: {e}")
    finally:
        scraper_state["is_scraping"] = False
        scraper_state["last_run_time"] = datetime.now().isoformat()
        
        # Save run history
        finished_at = datetime.now().isoformat()
        db.add_run_history(started_at, finished_at, items_seen, items_new, blocked_flag, error_msg)
        
        config = load_config()
        interval = config.get("interval_minutes", 5)
        # Note: next_run_time is set by the polling loop with jitter applied
        # Only set it here as a fallback if the loop hasn't set it yet
        if not scraper_state.get("next_run_time"):
            scraper_state["next_run_time"] = (datetime.now() + timedelta(minutes=interval)).isoformat()

# Lifespan background task runner
async def background_polling_loop():
    logger.info("Background polling loop started.")
    while True:
        try:
            if scraper_state["is_running"]:
                config = load_config()
                import random
                base_interval = config.get("interval_minutes", 5) * 60
                # 4b. Randomized polling jitter
                interval = int(base_interval * random.uniform(0.8, 1.2))
                
                # Calculate next run time WITH jitter so dashboard countdown is accurate
                next_run = datetime.now() + timedelta(seconds=interval)
                scraper_state["next_run_time"] = next_run.isoformat()
                
                # Execute (skip if outside configured active hours — fewer bot
                # signals overnight; manual scrapes bypass this)
                if within_active_hours(config):
                    await perform_scraping_cycle()
                else:
                    logger.info(
                        f"Outside active hours ({config.get('active_start')}h–"
                        f"{config.get('active_end')}h); skipping this cycle."
                    )

                # Preserve the jittered next_run_time (perform_scraping_cycle may have reset it)
                scraper_state["next_run_time"] = next_run.isoformat()
                
                # Wait for next run, responsive to changes in state
                # (wall-clock check so laptop sleep/resume can't strand the loop)
                while scraper_state["is_running"]:
                    nr = scraper_state.get("next_run_time")
                    if not nr or datetime.now() >= datetime.fromisoformat(nr):
                        break
                    await asyncio.sleep(1)

                nr = scraper_state.get("next_run_time")
                if scraper_state["is_running"] and nr:
                    lateness = (datetime.now() - datetime.fromisoformat(nr)).total_seconds()
                    if lateness > 60:
                        logger.warning(f"Missed scheduled run by {int(lateness)}s (system sleep?) — scraping now.")
            else:
                scraper_state["next_run_time"] = None
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Background loop stopping...")
            break
        except Exception as e:
            logger.error(f"Unexpected error in background loop: {e}")
            await asyncio.sleep(5)

# Fast API Application Lifecycle
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    db.init_db()

    # Migration checklist
    seen_file = os.path.join(BASE_DIR, "seen_items.json")
    if os.path.exists(seen_file) and not os.path.exists(LISTINGS_FILE):
        try:
            with open(seen_file, "r") as f:
                seen_urls = json.load(f)
            migrated = []
            now_str = datetime.now().isoformat()
            for url in seen_urls:
                db.upsert_listing({
                    "title": "Migrated Item",
                    "price": "N/A",
                    "price_value": None,
                    "url": url,
                    "first_seen": now_str,
                    "last_seen": now_str,
                    "published_date": None
                })
            logger.info(f"Migrated {len(seen_urls)} items from seen_items.json to SQLite")
        except Exception as me:
            logger.error(f"Error migrating old seen_items file: {me}")
            
    if os.path.exists(LISTINGS_FILE):
        try:
            with open(LISTINGS_FILE, "r", encoding="utf-8") as f:
                old_listings = json.load(f)
            migrated_count = 0
            for item in old_listings:
                item_data = {
                    "title": item.get("title", "N/A"),
                    "price": item.get("price"),
                    "price_value": parse_price(item.get("price", "")),
                    "url": item.get("url"),
                    "first_seen": item.get("first_seen", datetime.now().isoformat()),
                    "last_seen": item.get("last_seen", datetime.now().isoformat()),
                    "published_date": item.get("published_date")
                }
                if db.upsert_listing(item_data, item.get("query_id")):
                    migrated_count += 1
            os.rename(LISTINGS_FILE, LISTINGS_FILE + ".bak")
            logger.info(f"Migrated {migrated_count} items to SQLite and backed up listings.json.")
        except Exception as me:
            logger.error(f"Error migrating listings.json file: {me}")

    # Startup logic
    task = asyncio.create_task(background_polling_loop())
    yield
    # Shutdown logic
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Leboncoin Scraper Web App", lifespan=lifespan)

# API Endpoints
@app.get("/api/status")
async def get_status():
    config = load_config()
    total = db.get_listing_count()
    unseen_count = db.get_unseen_count()
    query_stats = db.query_stats()
    return {
        "status": scraper_state,
        "config": config,
        "stats": {
            "total_listings": total,
            "unseen_count": unseen_count,
            "per_query": query_stats,
            "last_run": scraper_state["last_run_time"],
            "next_run": scraper_state["next_run_time"]
        }
    }

class MarkViewedRequest(BaseModel):
    urls: List[str]

@app.post("/api/listings/mark-viewed")
async def mark_viewed(req: MarkViewedRequest):
    db.mark_viewed(req.urls)
    return {"success": True, "count": len(req.urls)}

@app.post("/api/toggle")
async def toggle_scraper():
    scraper_state["is_running"] = not scraper_state["is_running"]
    action = "started" if scraper_state["is_running"] else "stopped"
    logger.info(f"Auto-scraping has been manual-toggled: {action}")
    return {"success": True, "is_running": scraper_state["is_running"]}

@app.post("/api/scrape")
async def trigger_manual_scrape(force: bool = False):
    if scraper_state["is_scraping"]:
        raise HTTPException(status_code=400, detail="Scraper is already active.")
    
    if force:
        scraper_state["blocked_until"] = None
        logger.info("Manual scrape cycle triggered with FORCE bypass.")
    else:
        logger.info("Manual scrape cycle triggered via web API.")
    
    # Run in background to return status instantly
    asyncio.create_task(perform_scraping_cycle())

    # Push the auto-scrape schedule forward so it doesn't fire right after a manual run
    if scraper_state["is_running"]:
        interval = load_config().get("interval_minutes", 5) * 60
        scraper_state["next_run_time"] = (datetime.now() + timedelta(seconds=interval)).isoformat()

    return {"success": True, "message": "Scrape cycle started."}

@app.delete("/api/listings")
async def delete_all_listings():
    db.delete_all_listings()
    logger.info("All listings have been cleared via the web UI.")
    return {"success": True, "message": "All listings deleted."}

@app.get("/api/listings")
async def get_listings(search: Optional[str] = "", sort: Optional[str] = "date_desc", query_id: Optional[str] = "all"):
    listings = db.get_listings(search=search, sort=sort, query_id=query_id)
    return listings

@app.get("/api/runs")
async def get_runs(limit: int = 50):
    return db.get_run_history(limit)

@app.get("/api/logs")
async def get_logs():
    return list(logs_deque)

@app.post("/api/config")
async def update_config(config_data: ConfigModel):
    current = load_config()
    new_config = config_data.model_dump()
    
    # Merge / update
    current.update(new_config)
        
    save_config(current)
    logger.info("Configuration updated via web interface.")
    return {"success": True, "config": current}

@app.post("/api/test-webhook")
async def test_webhook(data: dict):
    webhook_url = data.get("webhook_url")
    if not webhook_url or not webhook_url.strip():
        raise HTTPException(status_code=400, detail="Discord webhook URL is empty.")
    
    try:
        logger.info(f"Sending test Discord message to: {webhook_url[:40]}...")
        await send_discord_async(
            webhook_url,
            "🔔 **Leboncoin Scraper Web App**: This is a test notification. Your Discord webhook is configured correctly!"
        )
        logger.info("Test Discord webhook notification sent successfully.")
        return {"success": True, "message": "Test notification sent successfully"}
    except Exception as e:
        logger.error(f"Test webhook failed: {e}")
        return {"success": False, "message": str(e)}

# Serve frontend SPA
@app.get("/")
async def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_file):
        raise HTTPException(status_code=404, detail="Frontend build index.html not found.")
    return FileResponse(index_file)

# Mount static files (served under /static)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
