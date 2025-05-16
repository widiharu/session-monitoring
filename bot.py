# bot.py: Telegram bot to monitor Cortensor Devnet4 sessions (HTML parsing) with commands to control auto-update
# Requirements: python-telegram-bot, requests, APScheduler, python-dotenv, beautifulsoup4

import os
import logging
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup

# Load configuration
load_dotenv()
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BASE_URL = os.getenv('CORTENSOR_API_BASE', 'https://dashboard-devnet4.cortensor.network/cognitive')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL_SEC', '30'))  # detik
STUCK_THRESHOLD = int(os.getenv('STUCK_THRESHOLD_MIN', '10'))  # menit

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# In-memory session states
sessions = {}

# Initialize scheduler (job not started until /update)
scheduler = BackgroundScheduler()

# Helper to parse datetime from format '16/5/2025, 12.49.25'
def parse_dt(text: str) -> datetime:
    return datetime.strptime(text.strip(), '%d/%m/%Y, %H.%M.%S')

# Fetch current session IDs by scraping main page
def fetch_session_list():
    try:
        resp = requests.get(BASE_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        elems = soup.select('a.session-link')  # sesuaikan selector sesuai HTML
        return [e.text.strip().split('#')[-1] for e in elems]
    except Exception as e:
        logger.error(f"Failed list fetch: {e}")
        return []

# Fetch and parse detail page
def fetch_session_detail(session_id: str) -> dict:
    try:
        url = f"{BASE_URL}/{session_id}"
        resp = requests.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        status_text = soup.find(text=lambda t: t and t.startswith('Status:'))
        status = status_text.split(':',1)[1].strip() if status_text else 'Unknown'
        overview = {}
        for row in soup.select('div.session-overview div.row'):
            label = row.find('div', class_='col-label').text.strip().strip(':')
            value = row.find('div', class_='col-value').text.strip()
            overview[label] = value
        created = parse_dt(overview.get('Created')) if overview.get('Created') else None
        started = parse_dt(overview.get('Started')) if overview.get('Started') else None
        ended_text = overview.get('Ended')
        ended = parse_dt(ended_text) if ended_text else None
        return {'status': status, 'created': created, 'started': started, 'ended': ended}
    except Exception as e:
        logger.error(f"Error detail {session_id}: {e}")
        return {}

# Notification helper
def notify(msg: str):
    try:
        bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# Core check function
def check_sessions():
    now = datetime.utcnow()
    ids = fetch_session_list()
    for sid in ids:
        detail = fetch_session_detail(sid)
        if not detail or 'status' not in detail:
            continue
        st = detail['status']
        state = sessions.get(sid)
        if not state:
            sessions[sid] = {'status': st, 'last_change': now}
            notify(f"🔔 New session {sid}: {st}")
        elif st != state['status']:
            notify(f"🔄 Session {sid} status: {state['status']} → {st}")
            sessions[sid] = {'status': st, 'last_change': now}
        else:
            elapsed = now - state['last_change']
            if elapsed > timedelta(minutes=STUCK_THRESHOLD):
                notify(f"🚨 Session {sid} stuck >{STUCK_THRESHOLD}m ({st})")
                sessions[sid]['last_change'] = now

# Telegram command handlers
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Halo! Bot siap memantau sesi.\n"
        f"Gunakan /update untuk mulai auto-update, /stop untuk hentikan.\n"
        f"Interval: {POLL_INTERVAL}s, Threshold stuck: {STUCK_THRESHOLD}m."
    )

def check_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("Menjalankan pengecekan manual...")
    check_sessions()
    update.message.reply_text("Pengecekan selesai.")

def update_cmd(update: Update, context: CallbackContext):
    if not scheduler.running:
        scheduler.start()
        update.message.reply_text("✅ Auto-update dimulai.")
    else:
        update.message.reply_text("⚠️ Auto-update sudah berjalan.")


def stop_cmd(update: Update, context: CallbackContext):
    # pause the scheduled job
    try:
        scheduler.pause_job('check_job')
        update.message.reply_text("⏸️ Auto-update dihentikan.")
    except Exception:
        update.message.reply_text("⚠️ Gagal menghentikan auto-update atau belum berjalan.")

# Main entry
if __name__ == '__main__':
    bot = Bot(BOT_TOKEN)
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('check', check_cmd))
    dp.add_handler(CommandHandler('update', update_cmd))
    dp.add_handler(CommandHandler('stop', stop_cmd))

    # Schedule job but do not start until /update
    scheduler.add_job(
        func=check_sessions,
        trigger='interval',
        seconds=POLL_INTERVAL,
        id='check_job'
    )

    updater.start_polling()
    updater.idle()
