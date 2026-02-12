"""Role-checking decorators and helpers."""

from __future__ import annotations

import functools
import logging
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

import db
from models import UserRole

logger = logging.getLogger(__name__)


def require_role(*allowed: UserRole) -> Callable:
    """Decorator: only allow users whose DB role is in *allowed*."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            *args, **kwargs,
        ):
            user_tg = update.effective_user
            if not user_tg:
                return

            db_user = await db.get_user(user_tg.id)
            if db_user is None:
                await update.effective_message.reply_text(
                    "Пожалуйста, сначала отправьте /start чтобы зарегистрироваться."
                )
                return
            if db_user.role not in allowed:
                await update.effective_message.reply_text(
                    "У вас нет доступа к этой функции."
                )
                return

            # Cache the user in context for the handler
            context.user_data["db_user"] = db_user
            return await func(update, context, *args, **kwargs)

        return wrapper
    return decorator


async def is_admin_or_above(telegram_id: int) -> bool:
    user = await db.get_user(telegram_id)
    return user is not None and user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


async def get_super_admin_ids() -> list[int]:
    return await db.get_super_admin_ids()
