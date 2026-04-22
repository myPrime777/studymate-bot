import os
import logging
import asyncio
import threading
from dotenv import load_dotenv
from flask import Flask
from time import time

import google.genai as genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ── CONFIG ──────────────────────────────────────────────────────────────
os.environ['WEB_CONCURRENCY'] = '1'

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_ID = "gemini-2.5-flash"

client = genai.Client(api_key=GEMINI_API_KEY)

# Rate limiting
_last_request_time = {}
_RATE_LIMIT_SEC = 30

# ── FLASK ───────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ StudyMate AI (Text Only) is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ── SESSION (ብቻ የውይይት ታሪክ) ───────────────────────────────────────
user_sessions = {}

def get_chat_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {"history": []}
    return user_sessions[user_id]

# ── SYSTEM PROMPT ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are StudyMate AI — an academic assistant.
You respond only to text messages. You cannot see images or files.
Answer questions clearly in the user's language (Amharic or English).
Use Markdown and emojis where helpful."""

# ── TEXT HANDLER ONLY ───────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    session = get_chat_session(user_id)
    
    # Rate limiting
    now = time()
    last = _last_request_time.get(user_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        wait = int(_RATE_LIMIT_SEC - (now - last))
        await update.message.reply_text(f"⏳ እባክህ ለ{wait} ሰከንድ ቆይ")
        return
    _last_request_time[user_id] = now
    
    history = session["history"]
    history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=history,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=2000
            )
        )
        
        bot_reply = response.text
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=bot_reply)]))
        
        if len(history) > 20:
            session["history"] = history[-20:]
        
        # መልስ ከ4000 ቁምፊ በላይ ከሆነ ቁርጥርጥሮ ላክ
        for chunk in [bot_reply[i:i+4000] for i in range(0, len(bot_reply), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except:
                await update.message.reply_text(chunk)
                
    except Exception as e:
        logger.error(f"AI Error: {e}")
        await update.message.reply_text("😔 ይቅርታ፣ መልስ ለመስጠት አልቻልኩም። እባክህ ከ30 ሰከንድ በኋላ ሞክር።")

# ── COMMANDS ───────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": []}
    await update.message.reply_text(
        "👋 ሰላም! እኔ StudyMate AI ነኝ።\n\n"
        "💬 ማንኛውንም ጥያቄ በጽሁፍ ልክልኝ።\n"
        "🧠 ያወራነውን ሁሉ አስታውሳለሁ።\n\n"
        "⛔ ምስል፣ ኦዲዮ ወይም ፋይል አልቀበልም።\n"
        "🗑️ ትውስታን ለማጥፋት /clear ይጠቀሙ።"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": []}
    await update.message.reply_text("🗑️ ትውስታዬ ተሰርዟል። አዲስ ውይይት እንጀምር!")

async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ማንኛውም ፋይል፣ ምስል፣ ኦዲዮ ቢላክ ይህ ምላሽ ይሰጣል"""
    await update.message.reply_text("⛔ ይቅርታ፣ እኔ ጽሁፍ ብቻ ነው የምሰራው። እባክህ በጽሁፍ ጥያቄህን ላክልኝ።")

# ── BOT RUNNER ─────────────────────────────────────────────────────────
async def run_bot():
    logger.info("🚀 StudyMate AI (Text Only) እየተጀመረ ነው...")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # ትዕዛዞች
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    
    # ጽሁፍ ብቻ
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # ሌላ ማንኛውም ነገር (ምስል፣ ፋይል፣ ኦዲዮ፣ ቪዲዮ፣ ወዘተ) ይህን መልእክት ይላክላቸዋል
    app.add_handler(MessageHandler(filters.ALL, unsupported))
    
    async with app:
        await app.start()
        await app.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(1)
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ ቦት በስኬት ተጀምሯል! ጽሁፍ ብቻ እየሰራ ነው።")
        while True:
            await asyncio.sleep(1)

# ── MAIN ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server ተጀምሯል...")
    asyncio.run(run_bot())
