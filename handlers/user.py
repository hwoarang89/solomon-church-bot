"""User handlers — /start, /help, /events, /contact, free-text Q&A."""

from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import db
from claude_ai import answer_user_question

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg = update.effective_user
    username = tg.username  # may be None
    full_name = tg.full_name or tg.first_name or "Unknown"

    user = await db.upsert_user(
        telegram_id=tg.id,
        username=username,
        full_name=full_name,
    )

    await update.message.reply_text(
        f"Добро пожаловать, {user.full_name}!\n\n"
        "Я — Соломон, помощник нашей общины.\n\n"
        "Вот что я умею:\n"
        "/events — список мероприятий\n"
        "/help — справка\n"
        "/contact — контакты\n"
        "/apply_admin — подать заявку на админство\n\n"
        "Или просто напишите мне вопрос!"
    )


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n"
        "/start — начало работы\n"
        "/events — список мероприятий\n"
        "/contact — контакты общины\n"
        "/apply_admin — подать заявку на админство\n"
        "/admin — панель админа (для админов)\n\n"
        "Вы также можете просто написать мне любой вопрос, "
        "и я постараюсь помочь!"
    )


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------

async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = await db.get_active_events()
    if not events:
        await update.message.reply_text("Сейчас нет активных мероприятий.")
        return

    lines = ["Активные мероприятия:\n"]
    buttons = []
    for e in events:
        info = f"  {e.title}"
        if e.date_start:
            info += f" | {e.date_start}"
        if e.time:
            info += f" | {e.time}"
        if e.place:
            info += f" | {e.place}"
        lines.append(info)
        buttons.append([
            InlineKeyboardButton(
                f"Записаться: {e.title}", callback_data=f"reg_start:{e.id}"
            )
        ])

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text("\n".join(lines), reply_markup=markup)


# ---------------------------------------------------------------------------
# /contact
# ---------------------------------------------------------------------------

async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    infos = await db.get_info_by_category("contact")
    if not infos:
        await update.message.reply_text(
            "Контактная информация пока не добавлена. "
            "Обратитесь к служителям лично."
        )
        return
    parts = []
    for i in infos:
        parts.append(f"{i['title']}: {i['content']}")
    await update.message.reply_text("\n".join(parts))


# ---------------------------------------------------------------------------
# Free-text messages → Claude
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message via Claude AI."""
    tg = update.effective_user
    text = update.message.text
    if not text:
        return

    # Ensure user exists
    user = await db.get_user(tg.id)
    if not user:
        user = await db.upsert_user(tg.id, tg.username, tg.full_name or "")

    reply = await answer_user_question(text, user.full_name)

    # Check if Claude wants to trigger registration
    if "ЗАПИСЬ_ТРЕБУЕТСЯ" in reply:
        # Extract event hint after the marker
        marker_line = ""
        clean_lines = []
        for line in reply.split("\n"):
            if "ЗАПИСЬ_ТРЕБУЕТСЯ" in line:
                marker_line = line.split("ЗАПИСЬ_ТРЕБУЕТСЯ")[-1].strip(": ")
            else:
                clean_lines.append(line)

        events = await db.get_active_events()
        if events:
            buttons = [
                [InlineKeyboardButton(
                    e.title, callback_data=f"reg_start:{e.id}"
                )]
                for e in events
            ]
            text_reply = "\n".join(clean_lines).strip() or "Выберите мероприятие для записи:"
            await update.message.reply_text(
                text_reply,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            await update.message.reply_text(
                "Сейчас нет активных мероприятий для записи."
            )
    else:
        await update.message.reply_text(reply)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("contact", cmd_contact))
    # Free-text handler — lowest priority (group=10)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        group=10,
    )
