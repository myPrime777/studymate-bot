import os
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv

import google.genai as genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ── 1. CONFIG & SETUP ──────────────────────────────────────────────────
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_ID = "gemini-2.5-flash" # User requested 2.5

client = genai.Client(api_key=GEMINI_API_KEY)

# ── 2. MEMORY & SESSION ────────────────────────────────────────────────
# እያንዳንዱ ተጠቃሚ የራሱ የሆነ የንግግር ታሪክ (History) እንዲኖረው
user_sessions = {}

def get_chat_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "history": [], # የንግግር ታሪክ
            "active_files": [] # የተላኩ ፋይሎች (Uris)
        }
    return user_sessions[user_id]

# ── 3. SYSTEM PROMPT ──────────────────────────────────────────────────
SYSTEM_PROMPT = """You are StudyMate AI — an advanced academic assistant.
Your capabilities:
1. Remember past conversation context.
2. Analyze PDF, Word, Excel, PPT, Audio, and Images.
3. Provide step-by-step detailed explanations.
4. If a user sends multiple files, treat them as a combined study material.

Formatting: Use Markdown, emojis, and clear structures. 
Language: Respond in the language used by the user (Amharic or English).
"""

# ── 4. UTILS ──────────────────────────────────────────────────────────
async def safe_reply(update: Update, text: str):
    """ረጅም መልሶችን ቆራርጦ ይልካል (4000 characters limit)"""
    if not text: return
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)

# ── 5. FILE HANDLER ───────────────────────────────────────────────────
async def handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_chat_session(user_id)
    
    msg = await update.message.reply_text("📥 ፋይሉን እያነበብኩ ነው... ⏳")
    
    try:
        # ፋይሉን ከቴሌግራም ዳውንሎድ ማድረግ
        file = await context.bot.get_file(update.message.effective_attachment().file_id)
        ext = Path(file.file_path).suffix
        local_path = f"temp_{user_id}{ext}"
        await file.download_to_drive(local_path)

        # ወደ Gemini File API መጫን
        uploaded_file = client.files.upload(path=local_path)
        session["active_files"].append(uploaded_file.uri)
        
        await msg.edit_text(f"✅ {ext.upper()} ፋይል ተቀብያለሁ! አሁን ስለ ፋይሉ መጠየቅ ትችላለህ።")
        
        # የቆሻሻ ፋይል ማጽዳት
        if os.path.exists(local_path): os.remove(local_path)
        
    except Exception as e:
        logger.error(f"File Error: {e}")
        await msg.edit_text("❌ ፋይሉን ማንበብ አልቻልኩም። እባክህ በPDF ወይም በምስል ሞክር።")

# ── 6. CHAT HANDLER (With Memory) ─────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    session = get_chat_session(user_id)

    # ታሪክን ማደራጀት (Memory)
    history = session["history"]
    history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))

    # ፋይሎች ካሉ ማካተት
    content_parts = [types.Part.from_text(text=user_input)]
    for file_uri in session["active_files"]:
        # ፋይሉን እንደ አባሪ ማከል (ለአሁኑ ጥያቄ ብቻ ሳይሆን ለታሪኩም እንዲሆን)
        content_parts.append(types.Part.from_uri(file_uri=file_uri, mime_type="application/pdf")) 

    try:
        # ለጊዜው አኒሜሽን ማሳየት
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=history, # ታሪኩን እዚህ እንልካለን (Memory)
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=4000
            )
        )

        bot_response = response.text
        # የታሪክ ማከማቻ (ቦቱ የመለሰውንም እንዲያስታውስ)
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=bot_response)]))
        
        # ታሪክ በጣም እንዳይረዝም (የመጨረሻ 20 ንግግሮችን ብቻ መያዝ)
        if len(history) > 20: session["history"] = history[-20:]

        await safe_reply(update, bot_response)

    except Exception as e:
        logger.error(f"GenAI Error: {e}")
        await update.message.reply_text("😔 ይቅርታ፣ መልስ ለመስጠት ተቸግሬያለሁ።")

# ── 7. COMMANDS ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "active_files": []} # Reset memory
    await update.message.reply_text("👋 ሰላም! እኔ StudyMate AI ነኝ።\n\nPDF, Audio, Image ወይም ማንኛውንም ፋይል መላክ ትችላለህ። ያወራነውን ሁሉ አስታውሳለሁ!")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "active_files": []}
    await update.message.reply_text("🗑️ ትውስታዬ ተሰርዟል። አዲስ ጥናት እንጀምር!")

# ── 8. MAIN ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Advanced StudyMate AI ስራ ጀምሯል...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    
    # ፋይሎችን ለመቀበል (Document, Photo, Audio)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.VOICE, handle_any_file))
    
    # ለጽሁፍ መልዕክት
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()