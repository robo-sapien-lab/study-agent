# bot.py (Group-Compatible with Menu)

import os
import logging
import sqlite3
import httpx
import asyncio
import datetime
import re
from dotenv import load_dotenv
from collections import defaultdict, Counter
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

# -----------------------------#
# ENV & Logging
# -----------------------------#
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
BOT_USERNAME = "robotutor_bot"  # without @

logging.basicConfig(level=logging.INFO)

# -----------------------------#
# DB Setup
# -----------------------------#
conn = sqlite3.connect("schedule.db", check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS chat_history (
    user_id INTEGER,
    timestamp TEXT,
    user_message TEXT,
    bot_reply TEXT
)""")

conn.commit()

# -----------------------------#
# Memory Store
# -----------------------------#
user_sessions = defaultdict(list)

# -----------------------------#
# /start
# -----------------------------#
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Hi {user.first_name}! 👋 I’m your study buddy. Ask me any academic question!"
    )

# -----------------------------#
# /progress
# -----------------------------#
async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

    c.execute("""
        SELECT user_message FROM chat_history
        WHERE user_id = ? AND timestamp > ?
    """, (user_id, seven_days_ago))
    rows = c.fetchall()

    if not rows:
        await update.message.reply_text("😕 No progress to show yet. Start asking questions!")
        return

    topic_keywords = []
    for row in rows:
        msg = row[0].lower()
        words = re.findall(r'\b[a-z]{4,}\b', msg)
        topic_keywords.extend(words)

    common = Counter(topic_keywords).most_common(5)
    if not common:
        await update.message.reply_text("😅 Couldn't detect topics, but you're asking good questions!")
        return

    msg = "📊 *Your Study Progress (Last 7 Days)*\n\n"
    for word, count in common:
        msg += f"• *{word.title()}* – {count} question{'s' if count > 1 else ''}\n"

    msg += "\n✅ Great work! Keep the streak alive."
    await update.message.reply_text(msg, parse_mode="Markdown")

# -----------------------------#
# /history
# -----------------------------#
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("""
        SELECT timestamp, user_message, bot_reply FROM chat_history
        WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT 10
    """, (user_id,))
    rows = c.fetchall()

    if not rows:
        await update.message.reply_text("No history found yet. Start chatting!")
        return

    for t, user_msg, bot_reply in reversed(rows):
        await update.message.reply_text(
            f"🕓 *{t.split('T')[1][:5]}*\n👤 You: {user_msg}\n🤖 Bot: {bot_reply}",
            parse_mode="Markdown"
        )
# -----------------------------#
# Handle DM Questions (No Command)
# -----------------------------#
async def handle_private_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return  # Skip group chats here

    user = update.effective_user
    user_id = user.id
    question = update.message.text.strip()

    if not question:
        await update.message.reply_text("🤔 Please send a valid question.")
        return

    await update.message.reply_text("🤖 Thinking...")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama3-8b-8192",
                    "messages": [
                        {"role": "system", "content": "You're an intelligent tutor for grades 6–12. Be clear and friendly."},
                        {"role": "user", "content": question}
                    ]
                },
                timeout=30
            )
            result = response.json()
            reply = result["choices"][0]["message"]["content"]

            # Save to DB
            c.execute("""INSERT INTO chat_history (user_id, timestamp, user_message, bot_reply)
                         VALUES (?, ?, ?, ?)""",
                      (user_id, datetime.datetime.now().isoformat(), question, reply))
            conn.commit()

            await update.message.reply_text(f"📬 Answer:\n\n{reply}")

    except Exception as e:
        await update.message.reply_text("❌ Error fetching answer.")
        print("Error:", e)

# -----------------------------#
# Group Message Handler
# -----------------------------#
async def handle_group_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_mention = f"@{user.username}" if user.username else user.first_name
    text = update.message.text.lower()

    if BOT_USERNAME not in text and "john" not in text:
        return  # Ignore if bot is not mentioned

    context.user_data["group_question"] = update.message.text

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 In Group", callback_data="group"),
         InlineKeyboardButton("📬 In DM", callback_data="dm")]
    ])

    await update.message.reply_text(
        f"👋 Hey {user_mention}! Where should I send the answer?",
        reply_markup=keyboard
    )

# -----------------------------#
# Handle Answer Destination
# -----------------------------#
async def answer_destination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    user_id = user.id
    question = context.user_data.get("group_question")

    if not question:
        await query.message.reply_text("⚠️ No question found. Try again.")
        return

    # Call LLM
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama3-8b-8192",
                    "messages": [
                        {"role": "system", "content": "You're an intelligent tutor for grades 6–12. Be clear and friendly."},
                        {"role": "user", "content": question}
                    ]
                },
                timeout=30
            )
            result = response.json()
            reply = result["choices"][0]["message"]["content"]

            # Save to DB
            c.execute("""INSERT INTO chat_history (user_id, timestamp, user_message, bot_reply)
                         VALUES (?, ?, ?, ?)""",
                      (user_id, datetime.datetime.now().isoformat(), question, reply))
            conn.commit()

            if query.data == "group":
                await query.message.reply_text(f"📢 Answer:\n{reply}")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"📬 Here's your answer:\n\n{reply}")

    except Exception as e:
        await query.message.reply_text("❌ Error fetching answer.")
        print("Error:", e)

# -----------------------------#
# App Setup
# -----------------------------#
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("progress", show_progress))
app.add_handler(CommandHandler("history", show_history))
app.add_handler(CallbackQueryHandler(answer_destination_callback))
app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, handle_group_mention))
app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_private_question))

# -----------------------------#
# Run
# -----------------------------#
if __name__ == "__main__":
    app.run_polling()


