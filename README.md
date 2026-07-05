# Leboncoin Scraper Dashboard

A comprehensive web scraping dashboard and background monitoring tool for Leboncoin, built with FastAPI, Playwright, and Vanilla JS. It enables you to track new listings based on sophisticated keywords, prices, and query filters, notifying you instantly on Discord and your Desktop.

## Features
- **Headless Scraping:** Bypasses basic bot protections using Playwright.
- **Fast Data Extraction:** Extracts structured metadata (`__NEXT_DATA__`) directly from Leboncoin without rendering heavy images and ads.
- **Dynamic Dashboard:** A lightweight vanilla JS frontend to configure queries and view a database of listings.
- **Discord Integration:** Sends batched, nicely-formatted Discord notifications to a webhook of your choice.

## Prerequisites
- Python 3.11+
- Playwright Chromium browsers installed

## Installation

1. Install the required Python packages:
```bash
pip install -r requirements.txt
```

2. Install the Playwright Chromium browser:
```bash
playwright install chromium
```

## Running the App

To start the scraper and the web dashboard, start the Uvicorn server:
```bash
uvicorn app:app
```
*(Note: Do not run with multiple workers or `--reload`, as the scraper background task and in-memory state rely on a single process instance).*

Once started, open your web browser and navigate to:
`http://127.0.0.1:8000`

## Configuration

All scraper configurations (queries, intervals, webhook URLs, headless mode) are managed directly from the Web Dashboard's **Settings** tab. These settings are persisted to `config.json`.
Listings data is stored in `listings.json`. Both files will be generated automatically on first run if they don't exist.
