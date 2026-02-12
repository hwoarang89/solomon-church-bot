"""Admin handlers — /admin panel, CRUD, text commands, apply_admin, sheets export."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

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
from models import UserRole, EventStatus
from roles import require_role, get_super_admin_ids
from claude_ai import parse_admin_command
import sheets_sync

logger = logging.getLogger(__name__)

# Conversation states for admin text commands
WAIT_TEXT_CMD, WAIT_CONFIRM = range(2)

# Conversation states for manual event creation
(
    EVT_TITLE, EVT_DATE, EVT_TIME, EVT_PLACE,
    EVT_DESC, EVT_MAX, EVT_CONFIRM,
) = range(10, 17)

# Conversation states for info creation
INFO_CATEGORY, INFO_TITLE, INFO_CONTENT, INFO_CONFIRM = range(20, 24)


# ---------------------------------------------------------------------------
# /admin — main panel
# ---------------------------------------------------------------------------

@require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = [
        [InlineKeyboardButton("Создать мероприятие", callback_data="adm:create_event")],
        [InlineKeyboardButton("Список мероприятий", callback_data="adm:list_events")],
        [InlineKeyboardButton("Добавить информацию", callback_data="adm:create_info")],
        [InlineKeyboardButton("Список информации", callback_data="adm:list_info")],
        [InlineKeyboardButton("Текстовая команда (AI)", callback_data="adm:text_cmd")],
        [InlineKeyboardButton("Выгрузить в Google Sheets", callback_data="adm:export_sheets")],
    ]
    await update.message.reply_text(
        "Панель администратора:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ---------------------------------------------------------------------------
# /apply_admin — request admin access
# ---------------------------------------------------------------------------

async def cmd_apply_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg = update.effective_user
    user = await db.get_user(tg.id)
    if not user:
        await update.message.reply_text("Сначала отправьте /start.")
        return
    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        await update.message.reply_text("Вы уже являетесь администратором.")
        return

    req = await db.create_admin_request(
        username=tg.username or str(tg.id),
        request_type="admin_access",
        telegram_id=tg.id,
        full_name=user.full_name,
        phone=user.phone,
        comment="Заявка на получение прав админа",
    )

    await update.message.reply_text(
        f"Ваша заявка #{req.id} отправлена на рассмотрение. Ожидайте."
    )

    # Notify super-admins
    sa_ids = await get_super_admin_ids()
    for sa_id in sa_ids:
        try:
            await context.bot.send_message(
                sa_id,
                f"Новая заявка на админство #{req.id}\n"
                f"От: {user.full_name} (@{tg.username or '—'})\n"
                f"Телефон: {user.phone or '—'}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "Одобрить", callback_data=f"sa:approve:{req.id}"
                        ),
                        InlineKeyboardButton(
                            "Отклонить", callback_data=f"sa:reject:{req.id}"
                        ),
                    ]
                ]),
            )
        except Exception:
            logger.exception("Failed to notify super_admin %s", sa_id)


# ---------------------------------------------------------------------------
# Callback router for admin panel buttons
# ---------------------------------------------------------------------------

@require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm:list_events":
        await _list_events(query, context)
    elif data == "adm:list_info":
        await _list_info(query, context)
    elif data == "adm:export_sheets":
        await _export_sheets(query, context)
    elif data.startswith("adm:event_detail:"):
        await _event_detail(query, context)
    elif data.startswith("adm:event_activate:"):
        await _event_activate(query, context)
    elif data.startswith("adm:event_archive:"):
        await _event_archive(query, context)
    elif data.startswith("adm:event_regs:"):
        await _event_registrations(query, context)
    elif data.startswith("adm:info_delete:"):
        await _info_delete(query, context)


# ---------------------------------------------------------------------------
# List events
# ---------------------------------------------------------------------------

async def _list_events(query, context) -> None:
    events_active = await db.get_events_by_status(EventStatus.ACTIVE)
    events_pending = await db.get_events_by_status(EventStatus.PENDING)
    all_events = events_pending + events_active

    if not all_events:
        await query.edit_message_text("Нет мероприятий.")
        return

    buttons = []
    for e in all_events:
        label = f"{'[ожид]' if e.status == EventStatus.PENDING else ''} {e.title} ({e.date_start})"
        buttons.append([InlineKeyboardButton(label.strip(), callback_data=f"adm:event_detail:{e.id}")])

    await query.edit_message_text(
        "Мероприятия:", reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _event_detail(query, context) -> None:
    event_id = int(query.data.split(":")[-1])
    event = await db.get_event(event_id)
    if not event:
        await query.edit_message_text("Мероприятие не найдено.")
        return

    count = await db.count_event_registrations(event_id)
    text = (
        f"Мероприятие: {event.title}\n"
        f"Статус: {event.status.value}\n"
        f"Дата: {event.date_start}\n"
        f"Время: {event.time or '—'}\n"
        f"Место: {event.place or '—'}\n"
        f"Описание: {event.description or '—'}\n"
        f"Макс. участников: {event.max_participants or 'без ограничений'}\n"
        f"Зарегистрировано: {count}\n"
        f"Создал: @{event.created_by or '—'}"
    )

    buttons = []
    if event.status == EventStatus.PENDING:
        buttons.append([InlineKeyboardButton(
            "Активировать", callback_data=f"adm:event_activate:{event_id}"
        )])
    if event.status == EventStatus.ACTIVE:
        buttons.append([InlineKeyboardButton(
            "Архивировать", callback_data=f"adm:event_archive:{event_id}"
        )])
    buttons.append([InlineKeyboardButton(
        "Список записей", callback_data=f"adm:event_regs:{event_id}"
    )])

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def _event_activate(query, context) -> None:
    event_id = int(query.data.split(":")[-1])
    db_user = context.user_data.get("db_user")

    # Only super_admin can directly activate; admins create a request
    if db_user and db_user.role == UserRole.SUPER_ADMIN:
        event = await db.activate_event(event_id)
        if event:
            await query.edit_message_text(f"Мероприятие «{event.title}» активировано.")
        else:
            await query.edit_message_text("Ошибка активации.")
    else:
        # Admin — create request for super_admin approval
        event = await db.get_event(event_id)
        if not event:
            await query.edit_message_text("Мероприятие не найдено.")
            return
        req = await db.create_admin_request(
            username=db_user.username or str(db_user.telegram_id),
            request_type="event_activation",
            telegram_id=db_user.telegram_id,
            full_name=db_user.full_name,
            payload_json={"event_id": event_id, "event_title": event.title},
            comment=f"Запрос на активацию мероприятия «{event.title}»",
        )
        await query.edit_message_text(
            f"Заявка #{req.id} на активацию «{event.title}» отправлена супер-админу."
        )
        # Notify super-admins
        sa_ids = await get_super_admin_ids()
        for sa_id in sa_ids:
            try:
                await context.bot.send_message(
                    sa_id,
                    f"Заявка #{req.id}: активация мероприятия «{event.title}»\n"
                    f"От: @{db_user.username or '—'}",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("Одобрить", callback_data=f"sa:approve:{req.id}"),
                            InlineKeyboardButton("Отклонить", callback_data=f"sa:reject:{req.id}"),
                        ]
                    ]),
                )
            except Exception:
                logger.exception("Failed to notify super_admin %s", sa_id)


async def _event_archive(query, context) -> None:
    event_id = int(query.data.split(":")[-1])
    event = await db.archive_event(event_id)
    if event:
        await query.edit_message_text(f"Мероприятие «{event.title}» архивировано.")
    else:
        await query.edit_message_text("Ошибка архивации.")


async def _event_registrations(query, context) -> None:
    event_id = int(query.data.split(":")[-1])
    regs = await db.get_event_registrations(event_id)
    event = await db.get_event(event_id)
    if not regs:
        await query.edit_message_text(
            f"На «{event.title if event else event_id}» пока нет записей."
        )
        return

    lines = [f"Записи на «{event.title}» ({len(regs)}):\n"]
    for i, r in enumerate(regs, 1):
        lines.append(
            f"{i}. {r.full_name} | @{r.username or '—'} | "
            f"тел: {r.phone or '—'} | ур: {r.level or '—'}"
        )
    await query.edit_message_text("\n".join(lines))


# ---------------------------------------------------------------------------
# List info
# ---------------------------------------------------------------------------

async def _list_info(query, context) -> None:
    infos = await db.get_all_info()
    if not infos:
        await query.edit_message_text("Информация не добавлена.")
        return

    lines = ["Информация:\n"]
    buttons = []
    for i in infos:
        lines.append(f"[{i['category']}] {i['title']}: {i['content'][:80]}")
        buttons.append([InlineKeyboardButton(
            f"Удалить: {i['title'][:30]}", callback_data=f"adm:info_delete:{i['id']}"
        )])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def _info_delete(query, context) -> None:
    info_id = int(query.data.split(":")[-1])
    ok = await db.delete_info(info_id)
    if ok:
        await query.edit_message_text("Информация удалена.")
    else:
        await query.edit_message_text("Не удалось удалить.")


# ---------------------------------------------------------------------------
# Export to Google Sheets
# ---------------------------------------------------------------------------

async def _export_sheets(query, context) -> None:
    await query.edit_message_text("Экспорт в Google Sheets...")
    try:
        result = await sheets_sync.export_all()
        await query.edit_message_text(result)
    except Exception:
        logger.exception("Sheets export error")
        await query.edit_message_text("Ошибка экспорта. Проверьте настройки.")


# ---------------------------------------------------------------------------
# Manual event creation (ConversationHandler)
# ---------------------------------------------------------------------------

@require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
async def evt_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите название мероприятия:")
    return EVT_TITLE


async def evt_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["evt_title"] = update.message.text.strip()
    await update.message.reply_text("Введите дату начала (ГГГГ-ММ-ДД):")
    return EVT_DATE


async def evt_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        context.user_data["evt_date"] = date.fromisoformat(text)
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Используйте ГГГГ-ММ-ДД:")
        return EVT_DATE
    await update.message.reply_text("Введите время (или /skip):")
    return EVT_TIME


async def evt_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["evt_time"] = text if text != "/skip" else None
    await update.message.reply_text("Введите место (или /skip):")
    return EVT_PLACE


async def evt_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["evt_place"] = text if text != "/skip" else None
    await update.message.reply_text("Введите описание (или /skip):")
    return EVT_DESC


async def evt_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["evt_desc"] = text if text != "/skip" else None
    await update.message.reply_text(
        "Введите макс. количество участников (0 = без ограничений, или /skip):"
    )
    return EVT_MAX


async def evt_max(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/skip":
        context.user_data["evt_max"] = 0
    else:
        try:
            context.user_data["evt_max"] = int(text)
        except ValueError:
            context.user_data["evt_max"] = 0

    title = context.user_data["evt_title"]
    d = context.user_data["evt_date"]
    t = context.user_data.get("evt_time") or "—"
    p = context.user_data.get("evt_place") or "—"

    await update.message.reply_text(
        f"Подтвердите создание:\n\n"
        f"Название: {title}\nДата: {d}\nВремя: {t}\nМесто: {p}\n\n"
        "Создать? (да/нет)",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Да", callback_data="evt_confirm:yes"),
                InlineKeyboardButton("Нет", callback_data="evt_confirm:no"),
            ]
        ]),
    )
    return EVT_CONFIRM


async def evt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "evt_confirm:no":
        await query.edit_message_text("Создание отменено.")
        return ConversationHandler.END

    db_user = context.user_data.get("db_user")
    username = db_user.username if db_user else None

    event = await db.create_event(
        title=context.user_data["evt_title"],
        date_start=context.user_data["evt_date"],
        time=context.user_data.get("evt_time"),
        place=context.user_data.get("evt_place"),
        description=context.user_data.get("evt_desc"),
        max_participants=context.user_data.get("evt_max", 0),
        status=EventStatus.PENDING,
        created_by=username,
    )

    # If super_admin — auto-activate
    if db_user and db_user.role == UserRole.SUPER_ADMIN:
        await db.activate_event(event.id)
        await query.edit_message_text(
            f"Мероприятие «{event.title}» создано и активировано (#{event.id})."
        )
    else:
        # Notify super-admins
        req = await db.create_admin_request(
            username=username or str(db_user.telegram_id),
            request_type="event_creation",
            telegram_id=db_user.telegram_id,
            full_name=db_user.full_name,
            payload_json={"event_id": event.id, "event_title": event.title},
            comment=f"Новое мероприятие «{event.title}»",
        )
        sa_ids = await get_super_admin_ids()
        for sa_id in sa_ids:
            try:
                await context.bot.send_message(
                    sa_id,
                    f"Заявка #{req.id}: новое мероприятие «{event.title}»\n"
                    f"Дата: {event.date_start}\nОт: @{username or '—'}",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("Одобрить", callback_data=f"sa:approve:{req.id}"),
                            InlineKeyboardButton("Отклонить", callback_data=f"sa:reject:{req.id}"),
                        ]
                    ]),
                )
            except Exception:
                logger.exception("Failed to notify super_admin %s", sa_id)

        await query.edit_message_text(
            f"Мероприятие «{event.title}» создано (#{event.id}). "
            "Ожидает одобрения супер-админа."
        )

    return ConversationHandler.END


async def evt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Создание мероприятия отменено.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Info creation (ConversationHandler)
# ---------------------------------------------------------------------------

@require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
async def info_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Введите категорию (например: contact, schedule, about):"
    )
    return INFO_CATEGORY


async def info_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["info_cat"] = update.message.text.strip()
    await update.message.reply_text("Введите заголовок:")
    return INFO_TITLE


async def info_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["info_title"] = update.message.text.strip()
    await update.message.reply_text("Введите содержание:")
    return INFO_CONTENT


async def info_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["info_content"] = update.message.text.strip()

    cat = context.user_data["info_cat"]
    title = context.user_data["info_title"]
    content = context.user_data["info_content"]

    await update.message.reply_text(
        f"Категория: {cat}\nЗаголовок: {title}\nСодержание: {content}\n\n"
        "Сохранить?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Да", callback_data="info_confirm:yes"),
                InlineKeyboardButton("Нет", callback_data="info_confirm:no"),
            ]
        ]),
    )
    return INFO_CONFIRM


async def info_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "info_confirm:no":
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END

    info_id = await db.create_info(
        category=context.user_data["info_cat"],
        title=context.user_data["info_title"],
        content=context.user_data["info_content"],
    )
    await query.edit_message_text(f"Информация #{info_id} сохранена.")
    return ConversationHandler.END


async def info_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Добавление информации отменено.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# AI text command (ConversationHandler)
# ---------------------------------------------------------------------------

@require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
async def text_cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Введите текстовую команду на естественном языке.\n"
        "Например: «Создай мероприятие Библейская школа на 20 января в 18:00»"
    )
    return WAIT_TEXT_CMD


async def text_cmd_parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    db_user = context.user_data.get("db_user")
    username = db_user.username if db_user else ""
    tables = await db.get_admin_tables(username) if username else []

    result = await parse_admin_command(text, username, tables)
    context.user_data["ai_cmd_result"] = result

    await update.message.reply_text(
        f"Распознано: {result.get('confirmation', '—')}\n\n"
        "Выполнить?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Да", callback_data="ai_confirm:yes"),
                InlineKeyboardButton("Нет", callback_data="ai_confirm:no"),
            ]
        ]),
    )
    return WAIT_CONFIRM


async def text_cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "ai_confirm:no":
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END

    result = context.user_data.get("ai_cmd_result", {})
    action = result.get("action", "unknown")
    params = result.get("params", {})
    db_user = context.user_data.get("db_user")

    try:
        if action == "create_event":
            date_str = params.get("date_start")
            d = date.fromisoformat(date_str) if date_str else date.today()
            event = await db.create_event(
                title=params.get("title", "Без названия"),
                date_start=d,
                time=params.get("time"),
                place=params.get("place"),
                description=params.get("description"),
                max_participants=params.get("max_participants", 0),
                status=EventStatus.PENDING,
                created_by=db_user.username if db_user else None,
            )
            await query.edit_message_text(
                f"Мероприятие «{event.title}» создано (#{event.id}), ожидает одобрения."
            )
        elif action == "create_info":
            info_id = await db.create_info(
                category=params.get("category", "general"),
                title=params.get("title", ""),
                content=params.get("content", ""),
            )
            await query.edit_message_text(f"Информация #{info_id} создана.")
        elif action == "archive_event":
            eid = params.get("event_id")
            if eid:
                await db.archive_event(int(eid))
                await query.edit_message_text(f"Мероприятие #{eid} архивировано.")
            else:
                await query.edit_message_text("Не удалось определить мероприятие.")
        else:
            await query.edit_message_text(
                f"Действие «{action}» не реализовано или не распознано."
            )
    except Exception:
        logger.exception("AI command execution error")
        await query.edit_message_text("Ошибка при выполнении команды.")

    return ConversationHandler.END


async def text_cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Текстовая команда отменена.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("apply_admin", cmd_apply_admin))

    # Admin panel button callbacks (non-conversation)
    app.add_handler(CallbackQueryHandler(
        admin_callback,
        pattern=r"^adm:(list_events|list_info|export_sheets|event_detail:\d+|event_activate:\d+|event_archive:\d+|event_regs:\d+|info_delete:\d+)$",
    ))

    # Event creation conversation
    evt_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(evt_start, pattern=r"^adm:create_event$"),
        ],
        states={
            EVT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, evt_title)],
            EVT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, evt_date)],
            EVT_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, evt_time),
                CommandHandler("skip", evt_time),
            ],
            EVT_PLACE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, evt_place),
                CommandHandler("skip", evt_place),
            ],
            EVT_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, evt_desc),
                CommandHandler("skip", evt_desc),
            ],
            EVT_MAX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, evt_max),
                CommandHandler("skip", evt_max),
            ],
            EVT_CONFIRM: [
                CallbackQueryHandler(evt_confirm, pattern=r"^evt_confirm:(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", evt_cancel)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(evt_conv)

    # Info creation conversation
    info_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(info_start, pattern=r"^adm:create_info$"),
        ],
        states={
            INFO_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, info_category)],
            INFO_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, info_title)],
            INFO_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, info_content)],
            INFO_CONFIRM: [
                CallbackQueryHandler(info_confirm, pattern=r"^info_confirm:(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", info_cancel)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(info_conv)

    # AI text command conversation
    ai_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(text_cmd_start, pattern=r"^adm:text_cmd$"),
        ],
        states={
            WAIT_TEXT_CMD: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_cmd_parse)],
            WAIT_CONFIRM: [
                CallbackQueryHandler(text_cmd_confirm, pattern=r"^ai_confirm:(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", text_cmd_cancel)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(ai_conv)
