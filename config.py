"""Configuration — all settings from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Google Sheets (optional — only needed for export)
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

# First super-admin username (without @), set once on first deploy
SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "")

# Claude model
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
