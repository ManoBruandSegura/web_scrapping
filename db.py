import sqlite3
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger("scraper_db")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_conn()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS listings (
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
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                url        TEXT NOT NULL REFERENCES listings(url) ON DELETE CASCADE,
                price_value REAL NOT NULL,
                recorded_at TEXT NOT NULL
            )
        ''')
        
        try:
            conn.execute("ALTER TABLE listings ADD COLUMN is_deal INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Column already exists
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS run_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                items_seen  INTEGER DEFAULT 0,
                items_new   INTEGER DEFAULT 0,
                blocked     INTEGER DEFAULT 0,
                error       TEXT
            )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    finally:
        conn.close()

def upsert_listing(item: Dict[str, Any], query_id: str = None) -> bool:
    """
    Inserts a new listing or updates an existing one.
    Returns True if the listing was newly inserted, False if it was just updated.
    """
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT first_seen FROM listings WHERE url = ?", (item.get("url"),))
        row = cursor.fetchone()

        if row:
            # Update existing
            cursor.execute('''
                UPDATE listings 
                SET last_seen = ?, price = ?, price_value = ?, title = COALESCE(?, title), query_id = COALESCE(?, query_id), thumbnail_url = COALESCE(?, thumbnail_url), location = COALESCE(?, location), is_deal = COALESCE(?, is_deal)
                WHERE url = ?
            ''', (item.get("last_seen"), item.get("price"), item.get("price_value"), item.get("title"), query_id, item.get("thumbnail_url"), item.get("location"), item.get("is_deal"), item.get("url")))
            conn.commit()
            return False
        else:
            # Insert new
            cursor.execute('''
                INSERT INTO listings (url, title, price, price_value, query_id, first_seen, last_seen, published_date, thumbnail_url, location, is_deal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item.get("url"),
                item.get("title", "N/A"),
                item.get("price"),
                item.get("price_value"),
                query_id,
                item.get("first_seen"),
                item.get("last_seen"),
                item.get("published_date"),
                item.get("thumbnail_url"),
                item.get("location"),
                item.get("is_deal", 0)
            ))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error upserting listing {item.get('url')}: {e}")
        return False
    finally:
        conn.close()

def get_listings(search: str = "", sort: str = "date_desc", query_id: str = "all") -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        query = "SELECT * FROM listings WHERE 1=1"
        params = []

        if search:
            query += " AND title LIKE ?"
            params.append(f"%{search}%")

        if query_id and query_id != "all":
            query += " AND query_id = ?"
            params.append(query_id)

        if sort == "date_desc":
            query += " ORDER BY first_seen DESC"
        elif sort == "date_asc":
            query += " ORDER BY first_seen ASC"
        elif sort == "price_desc":
            query += " ORDER BY price_value DESC"
        elif sort == "price_asc":
            query += " ORDER BY price_value ASC"
        else:
            query += " ORDER BY first_seen DESC"

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dicts for FastAPI
        listings = [dict(row) for row in rows]
        
        if listings:
            urls = [row["url"] for row in listings]
            placeholders = ",".join("?" * len(urls))
            history_cursor = conn.execute(f"SELECT url, price_value, recorded_at FROM price_history WHERE url IN ({placeholders}) ORDER BY recorded_at ASC", urls)
            history_rows = history_cursor.fetchall()
            history_map = {}
            for row in history_rows:
                h_url = row["url"]
                if h_url not in history_map:
                    history_map[h_url] = []
                history_map[h_url].append({"price_value": row["price_value"], "recorded_at": row["recorded_at"]})
            
            for listing in listings:
                listing["price_history"] = history_map.get(listing["url"], [])
                
        return listings
    except Exception as e:
        logger.error(f"Error fetching listings: {e}")
        return []
    finally:
        conn.close()

def get_listing_count() -> int:
    conn = get_conn()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM listings")
        return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Error fetching listing count: {e}")
        return 0
    finally:
        conn.close()

def delete_all_listings():
    conn = get_conn()
    try:
        conn.execute("DELETE FROM listings")
        conn.commit()
    except Exception as e:
        logger.error(f"Error deleting all listings: {e}")
    finally:
        conn.close()

def prune_old_listings(date_threshold_iso: str):
    conn = get_conn()
    try:
        cursor = conn.execute("DELETE FROM listings WHERE last_seen < ?", (date_threshold_iso,))
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
    except Exception as e:
        logger.error(f"Error pruning old listings: {e}")
        return 0
    finally:
        conn.close()

def get_current_price(url: str):
    conn = get_conn()
    try:
        cursor = conn.execute("SELECT price_value FROM listings WHERE url = ?", (url,))
        row = cursor.fetchone()
        if row:
            return row["price_value"]
        return None
    except Exception:
        return None
    finally:
        conn.close()

def add_price_history(url: str, price_value: float):
    if price_value is None:
        return
    conn = get_conn()
    try:
        now_str = __import__("datetime").datetime.now().isoformat()
        conn.execute('''
            INSERT INTO price_history (url, price_value, recorded_at)
            VALUES (?, ?, ?)
        ''', (url, float(price_value), now_str))
        conn.commit()
    except Exception as e:
        logger.error(f"Error adding price history for {url}: {e}")
    finally:
        conn.close()

def query_stats() -> Dict[str, dict]:
    conn = get_conn()
    try:
        cursor = conn.execute('''
            SELECT query_id, price_value 
            FROM listings 
            WHERE price_value IS NOT NULL AND query_id IS NOT NULL
        ''')
        rows = cursor.fetchall()
        
        from collections import defaultdict
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["query_id"]].append(row["price_value"])
                
        stats = {}
        for q_id, prices in grouped.items():
            if not prices:
                continue
            prices.sort()
            count = len(prices)
            min_p = prices[0]
            avg_p = sum(prices) / count
            
            mid = count // 2
            if count % 2 == 0:
                median_p = (prices[mid - 1] + prices[mid]) / 2.0
            else:
                median_p = prices[mid]
                
            stats[q_id] = {
                "count": count,
                "min": min_p,
                "avg": avg_p,
                "median": median_p
            }
        return stats
    except Exception as e:
        logger.error(f"Error computing query stats: {e}")
        return {}
    finally:
        conn.close()

def mark_viewed(urls: List[str]):
    if not urls:
        return
    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(urls))
        conn.execute(f"UPDATE listings SET viewed = 1 WHERE url IN ({placeholders})", urls)
        conn.commit()
    except Exception as e:
        logger.error(f"Error marking viewed: {e}")
    finally:
        conn.close()

def get_unseen_count() -> int:
    conn = get_conn()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM listings WHERE viewed = 0")
        return cursor.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()

def add_run_history(started_at: str, finished_at: str = None, items_seen: int = 0, items_new: int = 0, blocked: int = 0, error: str = None):
    conn = get_conn()
    try:
        conn.execute('''
            INSERT INTO run_history (started_at, finished_at, items_seen, items_new, blocked, error)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (started_at, finished_at, items_seen, items_new, blocked, error))
        
        # Keep only the last 500 runs
        conn.execute('''
            DELETE FROM run_history WHERE id NOT IN (
                SELECT id FROM run_history ORDER BY id DESC LIMIT 500
            )
        ''')
        conn.commit()
    except Exception as e:
        logger.error(f"Error adding run history: {e}")
    finally:
        conn.close()

def get_run_history(limit: int = 50) -> List[dict]:
    conn = get_conn()
    try:
        cursor = conn.execute("SELECT * FROM run_history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting run history: {e}")
        return []
    finally:
        conn.close()
