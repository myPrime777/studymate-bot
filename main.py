import os
import logging
import asyncio
import threading
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask

import google.genai as genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ── 1. CONFIG & SETUP ──────────────────────────────────────────────────
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_ID = "gemini-2.5-flash"

client = genai.Client(api_key=GEMINI_API_KEY)

# ── 2. FLASK ───────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ StudyMate AI is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ── 3. MEMORY & SESSION ────────────────────────────────────────────────
user_sessions = {}

def get_chat_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "history": [],
            "active_files": []
        }
    return user_sessions[user_id]

# ── 4. SYSTEM PROMPT ──────────────────────────────────────────────────
SYSTEM_PROMPT = """You are StudyMate AI — an advanced academic assistant.
Your capabilities:
1. Remember past conversation context.
2. Analyze PDF, Word, Excel, PPT, Audio, and Images.
3. Provide step-by-step detailed explanations.
4. If a user sends multiple files, treat them as a combined study material.

Formatting: Use Markdown, emojis, and clear structures.
Language: Respond in the language used by the user (Amharic or English).
"""

# ── 5. UTILS ──────────────────────────────────────────────────────────
async def safe_reply(update: Update, text: str):
    if not text:
        return
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)

# ── 6. FILE HANDLER ───────────────────────────────────────────────────
async def handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_chat_session(user_id)

    msg = await update.message.reply_text("📥 ፋይሉን እያነበብኩ ነው... ⏳")

    try:
        attachment = update.message.effective_attachment
        if isinstance(attachment, list):
            attachment = attachment[-1]

        file = await context.bot.get_file(attachment.file_id)
        ext = Path(file.file_path).suffix.lower()
        local_path = f"temp_{user_id}{ext}"
        await file.download_to_drive(local_path)

        mime_map = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        uploaded_file = client.files.upload(
            file=local_path,
            config={"mime_type": mime_type}
        )
        session["active_files"].append(
            types.Part.from_uri(
                file_uri=uploaded_file.uri,
                mime_type=mime_type
            )
        )

        await msg.edit_text(f"✅ {ext.upper()} ፋይል ተቀብያለሁ! አሁን ስለ ፋይሉ መጠየቅ ትችላለህ።")

        if os.path.exists(local_path):
            os.remove(local_path)

    except Exception as e:
        logger.error(f"File Error: {e}")
        await msg.edit_text("❌ ፋይሉን ማንበብ አልቻልኩም። እባክህ እንደገና ሞክር።")

# ── 7. CHAT HANDLER ────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    session = get_chat_session(user_id)

    history = session["history"]

    content_parts = [types.Part.from_text(text=user_input)]
    for file_part in session["active_files"]:
        content_parts.append(file_part)

    history.append(types.Content(role="user", parts=content_parts))

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        response = client.models.generate_content(
            model=MODEL_ID,
            contents=history,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=4000
            )
        )

        bot_response = response.text
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=bot_response)]))

        if len(history) > 20:
            session["history"] = history[-20:]

        await safe_reply(update, bot_response)

    except Exception as e:
        logger.error(f"GenAI Error: {e}")
        await update.message.reply_text("😔 ይቅርታ፣ መልስ ለመስጠት ተቸግሬያለሁ።")

# ── 8. COMMANDS ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "active_files": []}
    await update.message.reply_text(
        "👋 ሰላም! እኔ StudyMate AI ነኝ።\n\n"
        "PDF, Audio, Image ወይም ማንኛውንም ፋይል መላክ ትችላለህ።\n"
        "ያወራነውን ሁሉ አስታውሳለሁ! 🧠"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "active_files": []}
    await update.message.reply_text("🗑️ ትውስታዬ ተሰርዟል። አዲስ ጥናት እንጀምር!")

# ── 9. BOT RUNNER ─────────────────────────────────────────────────────
async def run_bot():
    logger.info("🚀 StudyMate AI Bot ጀምሯል...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.VOICE,
        handle_any_file
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Polling ጀምሯል!")
    await app.run_polling(
        drop_pending_updates=True,
        close_loop=False
    )

# ── 10. ENTRY POINT ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server ጀምሯል...")
    
    try:
        asyncio.run(run_bot())
    except RuntimeError as e:
        if "already running" in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_bot())
    
