#!/usr/bin/env python3
import os
import asyncio
import re
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = os.getenv("CHAT_ID")
BASE_URL     = os.getenv("BASE_URL", "https://dashboard-devnet4.cortensor.network/cognitive")
INTERVAL_SEC = int(os.getenv("INTERVAL_AUTO_SEC", "240"))  # default 240s = 4 menit
STUCK_MIN    = int(os.getenv("STUCK_THRESHOLD_MIN", "10")) # 10 menit

if not (BOT_TOKEN and CHAT_ID):
    print("❌ BOT_TOKEN dan CHAT_ID harus di-.env!")
    exit(1)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Scheduler ────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone=pytz.UTC)

# ─── Scraper Helpers ──────────────────────────────────────────────────────────
def parse_dt(s: str) -> datetime:
    """Parse '16/5/2025, 12.49.25' → datetime"""
    return datetime.strptime(s.strip(), "%d/%m/%Y, %H.%M.%S")

async def get_latest_session_id(playwright_page) -> str | None:
    """Load main page, grab first /cognitive/<id> link."""
    await playwright_page.goto(BASE_URL, timeout=30_000)
    await playwright_page.wait_for_selector("a[href^='/cognitive/']")
    href = await playwright_page.locator("a[href^='/cognitive/']").first.get_attribute("href")
    if not href:
        return None
    m = re.search(r"/cognitive/(\d+)", href)
    return m.group(1) if m else None

async def get_session_data(session_id: str) -> dict:
    """Scrape detail page and return dict of fields."""
    url = f"{BASE_URL}/{session_id}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=30_000)
        # Status in <h2>
        await page.wait_for_selector("h2")
        status = await page.locator("h2").inner_text()
        # Overview rows
        rows = page.locator("div.session-overview div.row")
        count = await rows.count()
        data = {"id": session_id, "status": status}
        for i in range(count):
            label = await rows.nth(i).locator("div.col-label").inner_text()
            value = await rows.nth(i).locator("div.col-value").inner_text()
            data[label.strip(": ")] = value
        await browser.close()
        return data

def format_message(data: dict) -> str:
    """Build Telegram message text from scraped data."""
    lines = [f"Session #{data['id']}", f"Status: {data['status']}"]
    for k, v in data.items():
        if k in ("id", "status"):
            continue
        lines.append(f"{k}: {v}")
    # detect stuck
    if "Started" in data and "Ended" not in data:
        start_dt = parse_dt(data["Started"])
        if datetime.utcnow() - start_dt > timedelta(minutes=STUCK_MIN):
            lines.append("\n⚠️ Session stuck >10 menit!")
    return "\n".join(lines)

# ─── Bot Handlers ─────────────────────────────────────────────────────────────
async def check_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/check – scrape sekali and reply"""
    msg = "🔍 Mengambil session terbaru…"
    await update.message.reply_text(msg)
    # use a fresh page for each check
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        sid = await get_latest_session_id(page)
        await browser.close()
    if not sid:
        await update.message.reply_text("❌ Gagal ambil session ID.")
        return
    data = await get_session_data(sid)
    text = format_message(data)
    await update.message.reply_text(text)

async def _auto_job():
    """Job that runs every INTERVAL_SEC sekunder."""
    # create a single browser+page
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        sid = await get_latest_session_id(page)
        if sid:
            data = await get_session_data(sid)
            text = "[Auto Update]\n" + format_message(data)
            # send via bot instance
            await application.bot.send_message(chat_id=CHAT_ID, text=text)
        await browser.close()

async def update_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/update – start auto-scrape"""
    if scheduler.get_job("auto_job"):
        await update.message.reply_text("⚠️ Auto-update sudah berjalan.")
    else:
        scheduler.add_job(_auto_job, "interval", seconds=INTERVAL_SEC, id="auto_job")
        scheduler.start()
        await update.message.reply_text(f"✅ Auto-update dimulai tiap {INTERVAL_SEC} detik.")

async def stop_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/stop – stop auto-scrape"""
    job = scheduler.get_job("auto_job")
    if job:
        scheduler.remove_job("auto_job")
        await update.message.reply_text("⏸️ Auto-update dihentikan.")
    else:
        await update.message.reply_text("⚠️ Belum ada auto-update berjalan.")

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # build application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("check",  check_handler))
    application.add_handler(CommandHandler("update", update_handler))
    application.add_handler(CommandHandler("stop",   stop_handler))
    logger.info("🚀 Bot started. Commands: /check /update /stop")
    application.run_polling()