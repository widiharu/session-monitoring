import os
import requests
import re
import pytz
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

# Load .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
BASE_URL = "https://dashboard-devnet4.cortensor.network/cognitive/"

# Scheduler
scheduler = BackgroundScheduler(timezone=pytz.UTC)

# Global state
last_session_id = None
auto_update = False


def get_latest_session_id():
    try:
        r = requests.get("https://dashboard-devnet4.cortensor.network/cognitive", timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        latest_link = soup.find("a", href=re.compile(r"/cognitive/\d+"))
        if latest_link:
            session_id = re.search(r"/cognitive/(\d+)", latest_link["href"]).group(1)
            return session_id
    except Exception as e:
        print(f"[ERROR] Failed to fetch latest session: {e}")
    return None


def get_session_info(session_id):
    try:
        url = f"{BASE_URL}{session_id}"
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        overview = soup.find("div", class_="session-overview")
        status_text = soup.find("h2").text.strip() if soup.find("h2") else "Unknown"
        details = {h3.text.strip(): div.text.strip() for h3, div in zip(soup.find_all("h3"), soup.find_all("div", class_="value"))}
        return status_text, details
    except Exception as e:
        print(f"[ERROR] Failed to fetch session info: {e}")
        return None, {}


def is_session_stuck(session_details):
    try:
        if "Started:" in session_details:
            start_time = datetime.strptime(session_details["Started:"], "%d/%m/%Y, %H.%M.%S")
            if "Ended:" not in session_details:
                return datetime.utcnow() - start_time > timedelta(minutes=10)
    except Exception as e:
        print(f"[ERROR] Failed to parse session time: {e}")
    return False


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_session_id

    session_id = get_latest_session_id()
    if not session_id:
        await update.message.reply_text("Gagal mengambil session ID.")
        return

    last_session_id = session_id
    status, details = get_session_info(session_id)

    msg = f"Session #{session_id}\nStatus: {status}\n"
    for key, value in details.items():
        msg += f"{key} {value}\n"

    if is_session_stuck(details):
        msg += "\n⚠️ Session terdeteksi STUCK (lebih dari 10 menit belum selesai)."

    await update.message.reply_text(msg)


async def start_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_update

    if auto_update:
        await update.message.reply_text("Auto update sudah berjalan.")
        return

    def job():
        session_id = get_latest_session_id()
        if not session_id:
            return
        status, details = get_session_info(session_id)
        message = f"[Auto Update]\nSession #{session_id}\nStatus: {status}\n"
        for key, value in details.items():
            message += f"{key} {value}\n"
        if is_session_stuck(details):
            message += "\n⚠️ Session terdeteksi STUCK (lebih dari 10 menit belum selesai)."
        requests.get(f"https://api.telegram.org/bot{TOKEN}/sendMessage", params={
            "chat_id": CHAT_ID,
            "text": message
        })

    scheduler.add_job(job, 'interval', seconds=30, id='check_job')
    scheduler.start()
    auto_update = True
    await update.message.reply_text("Auto update aktif setiap 30 detik.")


async def stop_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_update

    try:
        scheduler.remove_job('check_job')
        auto_update = False
        await update.message.reply_text("Auto update dihentikan.")
    except Exception:
        await update.message.reply_text("Tidak ada auto update yang berjalan.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("update", start_auto))
    app.add_handler(CommandHandler("stop", stop_auto))
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
