"""Super-admin handlers — approve/reject requests, global broadcasts."""

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
from models import UserRole
from roles import require_role

logger = logging.getLogger(__name__)

# Broadcast conversation states
BC_TEXT, BC_CONFIRM = range(50, 52)


# ---------------------------------------------------------------------------
# Approve / Reject callbacks
# ---------------------------------------------------------------------------

@require_role(UserRole.SUPER_ADMIN)
async def handle_request_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    # "sa:approve:123" or "sa:reject:123"
    action = parts[1]
    request_id = int(parts[2])

    db_user = context.user_data.get("db_user")
    reviewer = db_user.username if db_user else "super_admin"

    if action == "approve":
        req = await db.approve_request(request_id, reviewer)
    else:
        req = await db.reject_request(request_id, reviewer)

    if not req:
        await query.edit_message_text(f"Заявка #{request_id} уже обработана или не найдена.")
        return

    status_text = "одобрена" if action == "approve" else "отклонена"

    # Perform side effects on approval
    if action == "approve":
        await _apply_approval(req, context)

    await query.edit_message_text(
        f"Заявка #{request_id} ({req.request_type}) {status_text}.\n"
        f"Заявитель: @{req.username}"
    )

    # Notify the requester
    if req.telegram_id:
        try:
            await context.bot.send_message(
                req.telegram_id,
                f"Ваша заявка #{req.id} ({req.request_type}) была {status_text}.",
            )
        except Exception:
            logger.exception("Failed to notify requester %s", req.telegram_id)


async def _apply_approval(req, context) -> None:
    """Execute side-effects when a request is approved."""
    if req.request_type == "admin_access":
        # Grant admin role
        await db.set_user_role(req.username, UserRole.ADMIN)
        # Grant access to requested table (if any)
        if req.requested_table:
            await db.grant_table_access(req.username, req.requested_table)
        logger.info("Granted admin role to @%s", req.username)

    elif req.request_type in ("event_creation", "event_activation"):
        # Activate the event
        payload = req.payload_json or {}
        event_id = payload.get("event_id")
        if event_id:
            await db.activate_event(int(event_id))
            logger.info("Activated event #%s", event_id)

    elif req.request_type == "broadcast":
        # Send broadcast
        payload = req.payload_json or {}
        message = payload.get("message", "")
        scope = payload.get("scope", "all")
        if message:
            ids = await db.get_all_telegram_ids()
            sent = 0
            for tid in ids:
                try:
                    await context.bot.send_message(tid, message)
                    sent += 1
                except Exception:
                    pass
            logger.info("Broadcast sent to %d/%d users", sent, len(ids))


# ---------------------------------------------------------------------------
# /pending — view pending requests
# ---------------------------------------------------------------------------

@require_role(UserRole.SUPER_ADMIN)
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reqs = await db.get_pending_requests()
    if not reqs:
        await update.message.reply_text("Нет ожидающих заявок.")
        return

    for req in reqs:
        text = (
            f"Заявка #{req.id}\n"
            f"Тип: {req.request_type}\n"
            f"От: {req.full_name or '—'} (@{req.username})\n"
            f"Телефон: {req.phone or '—'}\n"
            f"Комментарий: {req.comment or '—'}\n"
            f"Дата: {req.created_at}"
        )
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Одобрить", callback_data=f"sa:approve:{req.id}"),
                    InlineKeyboardButton("Отклонить", callback_data=f"sa:reject:{req.id}"),
                ]
            ]),
        )


# ---------------------------------------------------------------------------
# /broadcast — global broadcast (ConversationHandler)
# ---------------------------------------------------------------------------

@require_role(UserRole.SUPER_ADMIN)
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введите текст рассылки (будет отправлен всем пользователям):"
    )
    return BC_TEXT


async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bc_text"] = update.message.text.strip()
    await update.message.reply_text(
        f"Текст рассылки:\n\n{context.user_data['bc_text']}\n\n"
        "Отправить?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Да, отправить", callback_data="bc_confirm:yes"),
                InlineKeyboardButton("Отмена", callback_data="bc_confirm:no"),
            ]
        ]),
    )
    return BC_CONFIRM


async def broadcast_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "bc_confirm:no":
        await query.edit_message_text("Рассылка отменена.")
        return ConversationHandler.END

    message = context.user_data.get("bc_text", "")
    ids = await db.get_all_telegram_ids()
    sent = 0
    for tid in ids:
        try:
            await context.bot.send_message(tid, message)
            sent += 1
        except Exception:
            pass

    await query.edit_message_text(f"Рассылка отправлена {sent}/{len(ids)} пользователям.")
    return ConversationHandler.END


async def broadcast_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await update.message.reply_text("Рассылка отменена.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /set_role <username> <role> — quick role change
# ---------------------------------------------------------------------------

@require_role(UserRole.SUPER_ADMIN)
async def cmd_set_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Использование: /set_role <username> <user|admin|super_admin>"
        )
        return

    username = args[0].lstrip("@")
    role_str = args[1].lower()
    try:
        role = UserRole(role_str)
    except ValueError:
        await update.message.reply_text(
            f"Неизвестная роль: {role_str}. Допустимые: user, admin, super_admin"
        )
        return

    ok = await db.set_user_role(username, role)
    if ok:
        await update.message.reply_text(
            f"Роль @{username} изменена на {role.value}."
        )
    else:
        await update.message.reply_text(f"Пользователь @{username} не найден.")


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(
        handle_request_decision,
        pattern=r"^sa:(approve|reject):\d+$",
    ))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("set_role", cmd_set_role))

    bc_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text)],
            BC_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm, pattern=r"^bc_confirm:(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(bc_conv)
