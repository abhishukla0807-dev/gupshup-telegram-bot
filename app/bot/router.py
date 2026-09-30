"""
Bot router — aggregates all handler routers into one.

When we add new command handlers (e.g. /search, /edit, /end), we import
their routers here and include them. The main.py only needs to register
this single aggregated router with the Dispatcher.
"""
from aiogram import Router

from app.bot.handlers.start import router as start_router
from app.bot.handlers.onboarding import router as onboarding_router
from app.bot.handlers.edit import router as edit_router
from app.bot.handlers.settings import router as settings_router
from app.bot.handlers.search import router as search_router
from app.bot.handlers.chat import router as chat_router
from app.bot.handlers.help import router as help_router

# Master router that collects all sub-routers
main_router = Router(name="main")
main_router.include_router(start_router)
main_router.include_router(onboarding_router)
main_router.include_router(edit_router)
main_router.include_router(settings_router)
main_router.include_router(search_router)
main_router.include_router(chat_router)
main_router.include_router(help_router)

