"""Register all handlers with the Application."""

from telegram.ext import Application

from handlers.user import register as register_user
from handlers.registration import register as register_registration
from handlers.admin import register as register_admin
from handlers.super_admin import register as register_super_admin


def register_handlers(app: Application) -> None:
    register_user(app)
    register_registration(app)
    register_admin(app)
    register_super_admin(app)
