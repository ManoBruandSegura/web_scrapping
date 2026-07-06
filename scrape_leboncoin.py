import asyncio
import json
import os
import urllib.parse
import logging
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Setup fallback logger
logging.basicConfig(level=logging.INFO)
default_logger = logging.getLogger("scraper_default")

class BlockedError(Exception):
    """Raised when Leboncoin serves its anti-bot 'access restricted' page."""
    pass

async def scrape_leboncoin(config: dict, logger: logging.Logger = None, context=None):
    if logger is None:
        logger = default_logger

    async def _do_scrape(ctx):
        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)
        
        # Build URL from configuration
        if config.get("mode") == "url" and config.get("custom_url"):
            url = config["custom_url"]
        else:
            query = config.get("query", "ryzen 9 5950x")
            encoded_query = urllib.parse.quote(query)
            url = f"https://www.leboncoin.fr/recherche?text={encoded_query}"
            if config.get("shippable"):
                url += "&shippable=1"
            
            price_min = config.get("price_min")
            price_max = config.get("price_max")
            if price_min is not None and price_max is not None:
                url += f"&price={price_min}-{price_max}"
            elif price_min is not None:
                url += f"&price=min-{price_min}"
            elif price_max is not None:
                url += f"&price=max-{price_max}"
                
        logger.info(f"Navigating to: {url}...")
        
        try:
            # Perf 1: Fast load without waiting for idle network
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            logger.info("Page loaded. Attempting fast __NEXT_DATA__ extraction...")

            # Datadome serves block pages with a 403/429 status
            if response and response.status in (403, 429):
                raise BlockedError(f"Leboncoin is blocking this IP (HTTP {response.status} served).")

            async def is_block_page():
                return await page.evaluate('''() => {
                    const t = (document.body && document.body.innerText) || '';
                    return t.includes('Access is temporarily restricted')
                        || t.includes('unusual activity from your device')
                        || t.includes('vous avez été bloqué')
                        || t.includes('activité inhabituelle');
                }''')

            # Detect the anti-bot block page before anything else
            if await is_block_page():
                raise BlockedError("Leboncoin is blocking this IP (anti-bot page served).")
            
            # Immediately try to get __NEXT_DATA__ before falling back to DOM parsing
            listings = await page.evaluate('''() => {
                const el = document.getElementById('__NEXT_DATA__');
                if (!el) return null;
                try {
                    const ads = JSON.parse(el.textContent)?.props?.pageProps?.searchData?.ads;
                    if (!Array.isArray(ads) || ads.length === 0) return null;
                    return ads.map(ad => {
                        const badgesStr = JSON.stringify(ad.badges || []).toLowerCase();
                        const isSold = badgesStr.includes('vendu') || badgesStr.includes('achat en cours') || ad.status === 'inactive' || ad.status === 'deleted';
                        
                        return {
                            title: ad.subject || 'N/A',
                            price: Number.isFinite(ad.price_cents)
                                ? (ad.price_cents / 100).toLocaleString('fr-FR', {maximumFractionDigits: 2}) + ' €'
                                : (Array.isArray(ad.price) && ad.price.length ? ad.price[0] + ' €' : 'N/A'),
                            url: ad.url || '',
                            is_sold: isSold,
                            published_date: ad.first_publication_date || ad.index_date || null,
                            thumbnail_url: ad.images?.thumb_url || ad.images?.urls?.[0] || null,
                            location: ad.location ? [ad.location.city, ad.location.zipcode].filter(Boolean).join(' ') : null
                        };
                    }).filter(a => a.url && !a.is_sold);
                } catch (e) { return null; }
            }''')

            if listings:
                logger.info(f"Successfully scraped {len(listings)} items from __NEXT_DATA__.")
                return listings
                
            logger.warning("__NEXT_DATA__ not usable, falling back to DOM scraping.")
            
            # If we fall back, handle cookies quickly inside one evaluate call
            await page.evaluate('''() => {
                try {
                    const btn = document.querySelector('#didomi-notice-agree-button');
                    if (btn) btn.click();
                } catch(e) {}
            }''')
            
            await asyncio.sleep(2) # Settle layout
            
            selector = 'div[data-qa-id="aditem_container"]'
            try:
                await page.wait_for_selector(selector, timeout=10000)
                logger.info("Listings found on page.")
            except Exception as e:
                # The block page renders its text via JS after domcontentloaded,
                # so the early check can miss it — re-check now that it's rendered.
                if await is_block_page():
                    raise BlockedError("Leboncoin is blocking this IP (anti-bot page rendered late).")
                logger.warning("Could not find listing cards selector. Page might be empty or CAPTCHA active.")
                raise e

            # Fallback: evaluate page DOM
            listings = await page.evaluate('''() => {
                const items = document.querySelectorAll('div[data-qa-id="aditem_container"]');
                const data = [];
                
                items.forEach(item => {
                    const linkEl = item.querySelector('a[href*="/ad/"]');
                    if (!linkEl) return;
                    const url = 'https://www.leboncoin.fr' + linkEl.getAttribute('href');
                    
                    let title = 'N/A';
                    const titleSpan = linkEl.querySelector('span[title]');
                    if (titleSpan) {
                        const rawTitle = titleSpan.getAttribute('title');
                        title = rawTitle.replace(/^Voir l(?:’|')annonce:\\s*/i, '').trim();
                    }
                    
                    if (title === 'N/A' || !title) {
                        const directTitleEl = item.querySelector('p[dir="ltr"]') || item.querySelector('span[dir="ltr"]');
                        if (directTitleEl) {
                            title = directTitleEl.innerText.trim();
                        }
                    }
                    
                    let price = 'N/A';
                    const elements = Array.from(item.querySelectorAll('*'));
                    const priceElement = elements.find(el => {
                        const text = el.innerText ? el.innerText.trim() : '';
                        return text && text.includes('€') && text.length < 15 && /\\d/.test(text) && !text.toLowerCase().includes('prix');
                    });
                    
                    if (priceElement) {
                        price = priceElement.innerText.trim();
                    } else {
                        const match = item.innerText ? item.innerText.match(/(\\d+[\\s ]*€)/) : null;
                        if (match) {
                            price = match[1].trim();
                        }
                    }
                    
                    const itemText = (item.innerText || '').toLowerCase();
                    const isSold = itemText.includes('vendu') || itemText.includes('achat en cours');
                    
                    let thumbnail_url = null;
                    const imgEl = item.querySelector('img[src]');
                    if (imgEl) thumbnail_url = imgEl.getAttribute('src');

                    let location = null;
                    const pElements = Array.from(item.querySelectorAll('p'));
                    const locEl = pElements.find(p => p.innerText && /\\b\\d{5}\\b/.test(p.innerText));
                    if (locEl) location = locEl.innerText.trim();

                    if (!isSold && !data.some(d => d.url === url)) {
                        data.push({ title, price, url, thumbnail_url, location });
                    }
                });
                return data;
            }''')
            
            logger.info(f"Successfully scraped {len(listings)} items from DOM.")
            return listings
            
        except BlockedError:
            raise  # let the caller decide how long to back off
        except Exception as e:
            logger.error(f"An error occurred during scraping: {e}")
            try:
                screenshot_path = os.path.join(os.path.dirname(__file__), "screenshot.png")
                await page.screenshot(path=screenshot_path)
                logger.info(f"Saved debug screenshot to {screenshot_path}")
            except Exception as se:
                pass
            return []
        finally:
            await page.close()

    if context:
        return await _do_scrape(context)
    else:
        async with async_playwright() as p:
            headless = config.get("headless", True)
            logger.info(f"Launching standalone browser (headless={headless})...")
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            try:
                return await _do_scrape(ctx)
            finally:
                logger.info("Closing standalone browser...")
                await browser.close()
