#!/usr/bin/env python3
import os
import re
import logging
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = os.getenv("CHAT_ID")
BASE_URL     = os.getenv("BASE_URL", "https://dashboard-devnet4.cortensor.network/cognitive")
INTERVAL_SEC = int(os.getenv("INTERVAL_AUTO_SEC", "240"))  # 4 menit
STUCK_MIN    = int(os.getenv("STUCK_THRESHOLD_MIN", "10")) # 10 menit

if not (BOT_TOKEN and CHAT_ID):
    print("❌ BOT_TOKEN dan CHAT_ID harus di-.env!")
    exit(1)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Scheduler ────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()

# ─── Scraper Helpers ──────────────────────────────────────────────────────────
def parse_dt(s: str) -> datetime:
    """Parse '16/5/2025, 12.49.25' → datetime"""
    return datetime.strptime(s.strip(), "%d/%m/%Y, %H.%M.%S")


def get_latest_session_id() -> str | None:
    """Ambil session ID terbaru dari halaman utama."""
    try:
        r = requests.get(BASE_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"^/cognitive/\d+$"))
        ids = []
        for a in links:
            m = re.match(r"/cognitive/(\d+)", a["href"])
            if m:
                ids.append(int(m.group(1)))
        if not ids:
            return None
        return str(max(ids))
    except Exception as e:
        logger.error(f"Error fetching latest session ID: {e}")
        return None


def get_session_data(session_id: str) -> dict:
    """Scrape detail sesi: status + overview."""
    url = f"{BASE_URL}/{session_id}"
    data = {"id": session_id, "status": "Unknown"}
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Status
        h2 = soup.find("h2")
        if h2:
            data["status"] = h2.text.strip()

        # Overview
        overview_div = soup.find("div", class_="session-overview")
        if overview_div:
            rows = overview_div.find_all("div", class_="row")
            for row in rows:
                label_div = row.find("div", class_="col-label")
                value_div = row.find("div", class_="col-value")
                if label_div and value_div:
                    label = label_div.text.strip().rstrip(":")
                    value = value_div.text.strip()
                    data[label] = value
    except Exception as e:
        logger.error(f"Error fetching session {session_id}: {e}")
    return data


def format_message(data: dict) -> str:
    """Bentuk teks pesan Telegram dari data scraping."""
    lines = [f"Session #{data['id']}", f"Status: {data.get('status','')}\n"]
    for k, v in data.items():
        if k in ("id", "status"): continue
        lines.append(f"{k}: {v}")
    # cek stuck
    if "Started" in data and "Ended" not in data:
        try:
            start_dt = parse_dt(data["Started"])
            if datetime.utcnow() - start_dt > timedelta(minutes=STUCK_MIN):
                lines.append("\n⚠️ Session STUCK lebih dari {STUCK_MIN} menit!")
        except:
            pass
    return "\n".join(lines)

# ─── Bot Handlers ─────────────────────────────────────────────────────────────
async def check_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ /check – scrape sekali dan kirim hasil. """
    await update.message.reply_text("🔍 Mengambil session terbaru…")
    sid = get_latest_session_id()
    if not sid:
        await update.message.reply_text("❌ Gagal ambil session ID.")
        return
    data = get_session_data(sid)
    await update.message.reply_text(format_message(data))

async def auto_job():
    """Job auto-scrape tiap INTERVAL_SEC detik."""
    sid = get_latest_session_id()
    if not sid:
        return
    data = get_session_data(sid)
    text = "[Auto Update]\n" + format_message(data)
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text}
    )

async def update_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ /update – start auto-scrape """
    if scheduler.get_job("auto_job"):
        await update.message.reply_text("⚠️ Auto-update sudah berjalan.")
    else:
        scheduler.add_job(auto_job, "interval", seconds=INTERVAL_SEC, id="auto_job")
        scheduler.start()
        await update.message.reply_text(f"✅ Auto-update tiap {INTERVAL_SEC} detik aktif.")

async def stop_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ /stop – stop auto-scrape """
    if scheduler.get_job("auto_job"):
        scheduler.remove_job("auto_job")
        await update.message.reply_text("⏸️ Auto-update dihentikan.")
    else:
        await update.message.reply_text("⚠️ Belum ada auto-update berjalan.")

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("check",  check_handler))
    app.add_handler(CommandHandler("update", update_handler))
    app.add_handler(CommandHandler("stop",   stop_handler))
    logger.info("🚀 Bot started. Commands: /check /update /stop")
    app.run_polling()