"""Solomon Bot — entry point."""

import logging

from telegram.ext import Application

from config import TELEGRAM_BOT_TOKEN, SUPER_ADMIN_USERNAME
import db
from handlers import register_handlers

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Called after the Application is built — init DB pool and schema."""
    await db.init_db()
    logger.info("DB pool ready")

    # Bootstrap: ensure the first super-admin exists
    if SUPER_ADMIN_USERNAME:
        user = await db.get_user_by_username(SUPER_ADMIN_USERNAME)
        if user and user.role.value != "super_admin":
            await db.set_user_role(SUPER_ADMIN_USERNAME, db.UserRole.SUPER_ADMIN)
            logger.info("Set %s as super_admin", SUPER_ADMIN_USERNAME)


async def post_shutdown(app: Application) -> None:
    """Called when the Application shuts down — close DB pool."""
    await db.close_db()
    logger.info("DB pool closed")


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(app)

    logger.info("Starting Solomon bot…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
