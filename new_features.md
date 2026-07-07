# New Feature Ideas for Leboncoin Scraper Dashboard

### 1. Proxy Support & Rotation (The Ultimate Anti-Ban)
Since you've recently worked on anti-IP ban mechanisms (like exponential backoff), the next logical step is to implement proxy support.
*   **What it does:** Allows the scraper to route traffic through rotating residential proxies. 
*   **Why it's great:** Instead of waiting hours for an IP block to lift, you can instantly switch IPs and continue scraping without interruption.

### 2. Market Analytics & Price Tracking
You're already storing listings in a SQLite database, which is a goldmine for data.
*   **Price History:** Track if a specific listing drops its price over time.
*   **"Deal Score":** Calculate the average price for a specific search query (e.g., "iPhone 13") and automatically flag listings that are significantly below the market average.
*   **Charts:** Add a simple Chart.js widget to your dashboard showing the price distribution for your active queries.

### 3. AI-Powered Listing Analysis (LLM Integration)
Standard Leboncoin filters are limited. You could integrate an LLM (like Gemini or OpenAI) to analyze the description and images.
*   **Condition Extraction:** Have the AI read the description to find hidden red flags (e.g., "screen cracked," "for parts," "no box") and automatically score or filter out the listing.
*   **Negotiability:** Detect if the seller mentions "négociable" or "urgent" in the text, indicating you might get a better price.

### 4. Multi-Platform Support
If you're hunting for deals, Leboncoin isn't the only place to look.
*   **Expand the Scraper:** Abstract your scraping logic so you can easily plug in modules for **Vinted**, **Facebook Marketplace**, or **eBay**.
*   **Unified Dashboard:** See all cross-platform listings in your single Vanilla JS dashboard, sent to the same Discord webhook.

### 5. Historical Archiving (The "Wayback Machine" for Ads)
Listings often disappear quickly when they are sold or deleted.
*   **Archival System:** Instead of just updating the status, keep a full archive of the title, description, price, and download the primary image. 
*   **Why it's useful:** Allows you to build your own historical pricing database for specific niches (like cars, real estate, or collectibles) to know exactly what things *actually* sell for, not just what they are listed for.

### 6. Interactive Discord Bot
Right now, you have one-way Discord webhooks. You could upgrade this to a two-way Discord bot.
*   **Commands:** Use commands like `/pause`, `/resume`, or `/search <keyword>` directly from Discord to control your server without needing to open the web dashboard.

### 7. Block Alerts (Know When You're Blocked)
A Datadome block currently only shows up in the logs.
*   **What it does:** Send a one-time Discord ping when the scraper gets blocked ("blocked, backing off until 14:30") using the existing webhook sender.
*   **Why it's great:** You find out immediately instead of silently missing listings for hours. ~10-minute change.

### 8. Mobile Push via ntfy.sh
Discord is fine at a desk, but not ideal for grabbing a deal on the go.
*   **What it does:** Mirror alerts to [ntfy.sh](https://ntfy.sh) for a phone notification with near-zero setup and no app account.
*   **Why it's great:** Fast, cheap, and great for "grab it before someone else" deals when you're away from the computer.

### 9. Smart Scheduling (Human Hours + Per-Query Pause)
Ties into the anti-ban work.
*   **Human hours:** Skip overnight scrapes — fewer bot signals and less request footprint.
*   **Per-query pause:** Mute one noisy query without disabling the whole scraper.

---

## Recommended Starting Point
**Price-drop alerts + deal score in the notification** (from ideas #2) give the
biggest jump in "does this tool actually find me deals," and both sit right on
top of the existing price-history and deal-detection code — a small diff. Add
the **block alert** (#7) at the same time; it's a ~10-minute change.
