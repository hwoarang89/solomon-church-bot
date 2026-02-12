"""Google Sheets export — push PostgreSQL data to a spreadsheet."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SHEETS_ID, GOOGLE_CREDENTIALS_JSON
import db

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_sheets_client() -> gspread.Client:
    """Build an authorized gspread client from the JSON env var."""
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _serialize(val) -> str:
    """Convert a value to a string suitable for Google Sheets."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


async def export_all() -> str:
    """Export all key tables from PostgreSQL to Google Sheets.

    Returns a summary string.
    """
    if not GOOGLE_SHEETS_ID or not GOOGLE_CREDENTIALS_JSON:
        return "Google Sheets не настроен (нет GOOGLE_SHEETS_ID или GOOGLE_CREDENTIALS_JSON)."

    gc = _get_sheets_client()
    sh = gc.open_by_key(GOOGLE_SHEETS_ID)

    counts: dict[str, int] = {}

    # --- Users ---
    users = await db.get_all_users()
    _write_sheet(sh, "Users", [
        ["telegram_id", "username", "full_name", "phone", "role", "created_at"],
        *[[_serialize(u.telegram_id), u.username or "", u.full_name,
           u.phone or "", u.role.value, _serialize(u.created_at)] for u in users],
    ])
    counts["Users"] = len(users)

    # --- Events ---
    rows_events = await db.pool.fetch("SELECT * FROM events ORDER BY id")
    header = ["id", "title", "type", "date_start", "date_end", "time",
              "place", "description", "max_participants", "status",
              "created_by", "created_at"]
    _write_sheet(sh, "Events", [
        header,
        *[[_serialize(r[h]) for h in header] for r in rows_events],
    ])
    counts["Events"] = len(rows_events)

    # --- Registrations ---
    rows_reg = await db.pool.fetch(
        "SELECT * FROM event_registrations ORDER BY id"
    )
    header_r = ["id", "event_id", "username", "telegram_id", "full_name",
                "phone", "level", "comment", "registered_at"]
    _write_sheet(sh, "Registrations", [
        header_r,
        *[[_serialize(r[h]) for h in header_r] for r in rows_reg],
    ])
    counts["Registrations"] = len(rows_reg)

    # --- Info ---
    rows_info = await db.pool.fetch("SELECT * FROM info ORDER BY id")
    header_i = ["id", "category", "title", "content", "updated_at"]
    _write_sheet(sh, "Info", [
        header_i,
        *[[_serialize(r[h]) for h in header_i] for r in rows_info],
    ])
    counts["Info"] = len(rows_info)

    summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
    logger.info("Exported to Google Sheets: %s", summary)
    return f"Экспорт завершён. {summary}"


def _write_sheet(sh: gspread.Spreadsheet, title: str, data: list[list[str]]) -> None:
    """Write data to a worksheet, creating it if necessary."""
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=max(len(data), 1), cols=len(data[0]) if data else 1)
    ws.clear()
    if data:
        ws.update(range_name="A1", values=data)
