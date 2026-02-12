"""Registration ConversationHandler — sign up for events step by step."""

from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import db

logger = logging.getLogger(__name__)

# Conversation states
ASK_NAME, ASK_PHONE, ASK_LEVEL, CONFIRM = range(4)


# ---------------------------------------------------------------------------
# Entry point: button click "reg_start:<event_id>"
# ---------------------------------------------------------------------------

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    event_id = int(query.data.split(":")[1])
    event = await db.get_event(event_id)

    if not event:
        await query.edit_message_text("Мероприятие не найдено.")
        return ConversationHandler.END

    if event.max_participants > 0:
        count = await db.count_event_registrations(event_id)
        if count >= event.max_participants:
            await query.edit_message_text(
                f"К сожалению, мест на «{event.title}» больше нет."
            )
            return ConversationHandler.END

    context.user_data["reg_event_id"] = event_id
    context.user_data["reg_event_title"] = event.title

    # Pre-fill name from DB
    user = await db.get_user(update.effective_user.id)
    if user and user.full_name:
        context.user_data["reg_name"] = user.full_name
        await query.edit_message_text(
            f"Запись на «{event.title}»\n\n"
            f"Ваше имя: {user.full_name}\n"
            "Если хотите изменить, отправьте другое имя.\n"
            "Или отправьте /skip чтобы оставить как есть."
        )
    else:
        await query.edit_message_text(
            f"Запись на «{event.title}»\n\nВведите ваше полное имя:"
        )

    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text and text != "/skip":
        context.user_data["reg_name"] = text

    await update.message.reply_text(
        "Введите номер телефона (или /skip чтобы пропустить):"
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text and text != "/skip":
        context.user_data["reg_phone"] = text
    else:
        context.user_data["reg_phone"] = None

    await update.message.reply_text(
        "Укажите ваш уровень/опыт (или /skip чтобы пропустить):"
    )
    return ASK_LEVEL


async def ask_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text and text != "/skip":
        context.user_data["reg_level"] = text
    else:
        context.user_data["reg_level"] = None

    # Show confirmation
    name = context.user_data.get("reg_name", "—")
    phone = context.user_data.get("reg_phone") or "не указан"
    level = context.user_data.get("reg_level") or "не указан"
    event_title = context.user_data.get("reg_event_title", "")

    await update.message.reply_text(
        f"Подтвердите запись на «{event_title}»:\n\n"
        f"Имя: {name}\n"
        f"Телефон: {phone}\n"
        f"Уровень: {level}\n\n"
        "Всё верно? (да/нет)",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Да", callback_data="reg_confirm:yes"),
                InlineKeyboardButton("Нет, отмена", callback_data="reg_confirm:no"),
            ]
        ]),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "reg_confirm:no":
        await query.edit_message_text("Запись отменена.")
        return ConversationHandler.END

    tg = update.effective_user
    event_id = context.user_data["reg_event_id"]
    name = context.user_data.get("reg_name", tg.full_name or "")
    phone = context.user_data.get("reg_phone")
    level = context.user_data.get("reg_level")

    try:
        await db.register_for_event(
            event_id=event_id,
            full_name=name,
            username=tg.username,
            telegram_id=tg.id,
            phone=phone,
            level=level,
        )
        await query.edit_message_text(
            f"Вы записаны на «{context.user_data['reg_event_title']}»!"
        )
    except Exception:
        logger.exception("Registration failed")
        await query.edit_message_text("Произошла ошибка при записи. Попробуйте позже.")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Запись отменена.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(reg_start, pattern=r"^reg_start:\d+$"),
        ],
        states={
            ASK_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name),
                CommandHandler("skip", ask_name),
            ],
            ASK_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone),
                CommandHandler("skip", ask_phone),
            ],
            ASK_LEVEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_level),
                CommandHandler("skip", ask_level),
            ],
            CONFIRM: [
                CallbackQueryHandler(confirm, pattern=r"^reg_confirm:(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv)
