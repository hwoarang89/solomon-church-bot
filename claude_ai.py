"""Claude AI integration — user Q&A and admin command parsing."""

from __future__ import annotations

import json
import logging

import anthropic

from config import CLAUDE_API_KEY, CLAUDE_MODEL
import db

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)

# ---------------------------------------------------------------------------
# User Q&A
# ---------------------------------------------------------------------------

SOLOMON_SYSTEM = """Ты — Соломон, дружелюбный помощник церковной общины.
Отвечай кратко, по-русски.
Если пользователь хочет записаться на мероприятие, ответь маркером ЗАПИСЬ_ТРЕБУЕТСЯ
и укажи название мероприятия, например:
ЗАПИСЬ_ТРЕБУЕТСЯ: Библейская школа
Если не знаешь ответа, вежливо скажи, что не можешь помочь, и предложи обратиться к служителям."""


async def answer_user_question(message: str, user_name: str) -> str:
    """Answer a free-text user question using DB context."""
    context = await db.get_claude_context()
    try:
        resp = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SOLOMON_SYSTEM + "\n\n" + context,
            messages=[
                {"role": "user", "content": f"[{user_name}]: {message}"},
            ],
        )
        return resp.content[0].text
    except Exception:
        logger.exception("Claude API error")
        return "Извините, произошла ошибка при обработке запроса."


# ---------------------------------------------------------------------------
# Admin command parsing
# ---------------------------------------------------------------------------

ADMIN_SYSTEM = """Ты помощник администратора церковной общины.
Тебе приходят текстовые команды на естественном языке.
Определи действие и верни JSON (без markdown-обёртки).

Возможные действия:
- create_event: создать мероприятие
- update_event: обновить мероприятие
- archive_event: архивировать мероприятие
- create_info: добавить информацию
- update_info: обновить информацию
- delete_info: удалить информацию
- broadcast: разослать сообщение
- unknown: не удалось распознать

Формат ответа — строго JSON:
{
  "action": "create_event",
  "params": {
    "title": "...",
    "date_start": "2025-01-15",
    "time": "18:00",
    "place": "...",
    "description": "..."
  },
  "confirmation": "Создать мероприятие «...» на 15 января?"
}

Если значение неизвестно, не включай его в params.
Дату всегда в формате YYYY-MM-DD."""


async def parse_admin_command(
    text: str,
    admin_username: str,
    admin_tables: list[str],
) -> dict:
    """Parse a free-text admin command using Claude. Returns dict with action/params."""
    tables_str = ", ".join(admin_tables) if admin_tables else "все"
    try:
        resp = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=(
                ADMIN_SYSTEM
                + f"\n\nАдмин: @{admin_username}, доступ к таблицам: {tables_str}"
            ),
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        # Strip possible markdown code fence
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw[:-3]
        return json.loads(raw)
    except (json.JSONDecodeError, IndexError, KeyError):
        logger.exception("Failed to parse Claude admin response")
        return {
            "action": "unknown",
            "params": {},
            "confirmation": "Не удалось распознать команду. Попробуйте иначе.",
        }
    except Exception:
        logger.exception("Claude API error in admin parsing")
        return {
            "action": "unknown",
            "params": {},
            "confirmation": "Ошибка при обработке команды.",
        }
