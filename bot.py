import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import phonenumbers
from phonenumbers import PhoneNumberFormat
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = Path(os.getenv("DB_PATH", "search_history.db"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "5"))

LANGUAGES = {
    "uk": {
        "start": "Вітаю! Я бот для безпечного пошуку *публічно доступної* інформації за номером телефону.\n"
        "Використання: `/search +380501234567`",
        "help": "Команди:\n"
        "`/start` — вітання\n"
        "`/search <номер>` — пошук за номером у міжнародному форматі\n"
        "`/history` — історія запитів\n"
        "`/export` — експорт останнього результату у .txt\n"
        "`/lang <uk|ru|en>` — змінити мову",
        "invalid_phone": "Некоректний номер. Використовуйте міжнародний формат, напр. +380501234567",
        "rate_limited": "Забагато запитів. Спробуйте трохи пізніше.",
        "no_history": "Історія порожня.",
        "set_lang": "Мову змінено.",
        "bad_lang": "Невідома мова. Доступно: uk, ru, en",
        "search_usage": "Використання: `/search <номер>`",
        "nothing_to_export": "Немає результатів для експорту.",
    },
    "ru": {
        "start": "Привет! Я бот для безопасного поиска *публично доступной* информации по номеру телефона.\n"
        "Использование: `/search +380501234567`",
        "help": "Команды:\n"
        "`/start` — приветствие\n"
        "`/search <номер>` — поиск по номеру в международном формате\n"
        "`/history` — история запросов\n"
        "`/export` — экспорт последнего результата в .txt\n"
        "`/lang <uk|ru|en>` — сменить язык",
        "invalid_phone": "Некорректный номер. Используйте международный формат, напр. +380501234567",
        "rate_limited": "Слишком много запросов. Попробуйте позже.",
        "no_history": "История пуста.",
        "set_lang": "Язык изменен.",
        "bad_lang": "Неизвестный язык. Доступно: uk, ru, en",
        "search_usage": "Использование: `/search <номер>`",
        "nothing_to_export": "Нет результатов для экспорта.",
    },
    "en": {
        "start": "Hello! I am a bot for safe lookup of *publicly available* information by phone number.\n"
        "Usage: `/search +380501234567`",
        "help": "Commands:\n"
        "`/start` — greeting\n"
        "`/search <phone>` — lookup by international number\n"
        "`/history` — request history\n"
        "`/export` — export the latest result as .txt\n"
        "`/lang <uk|ru|en>` — change language",
        "invalid_phone": "Invalid phone number. Use international format, e.g. +380501234567",
        "rate_limited": "Too many requests. Try again later.",
        "no_history": "History is empty.",
        "set_lang": "Language updated.",
        "bad_lang": "Unknown language. Available: uk, ru, en",
        "search_usage": "Usage: `/search <phone>`",
        "nothing_to_export": "No results to export.",
    },
}

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phone_lookup_bot")


@dataclass
class LookupResult:
    phone: str
    owner_name: str | None
    social_profiles: Dict[str, str]
    contacts: Dict[str, str]
    location: str | None
    notes: List[str]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'uk'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def get_lang(self, user_id: int) -> str:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT language FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0] if row else "uk"

    def set_lang(self, user_id: int, lang: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO user_settings(user_id, language)
                VALUES(?, ?)
                ON CONFLICT(user_id) DO UPDATE SET language=excluded.language
                """,
                (user_id, lang),
            )
            conn.commit()

    def save_search(self, user_id: int, phone: str, result: dict) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO searches(user_id, phone, result_json, created_at) VALUES (?, ?, ?, ?)",
                (user_id, phone, json.dumps(result, ensure_ascii=False), int(time.time())),
            )
            conn.commit()

    def get_history(self, user_id: int, limit: int = 10) -> List[tuple]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT phone, created_at FROM searches WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return rows

    def get_last_result(self, user_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT result_json FROM searches WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return json.loads(row[0]) if row else None


class RateLimiter:
    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._events: dict[int, list[float]] = {}

    def allow(self, user_id: int) -> bool:
        now = time.time()
        events = self._events.setdefault(user_id, [])
        self._events[user_id] = [ts for ts in events if now - ts < 60]
        if len(self._events[user_id]) >= self.max_per_minute:
            return False
        self._events[user_id].append(now)
        return True


def t(user_id: int, key: str, db: Database) -> str:
    lang = db.get_lang(user_id)
    return LANGUAGES.get(lang, LANGUAGES["uk"]).get(key, key)


def normalize_phone(raw: str) -> str | None:
    try:
        parsed = phonenumbers.parse(raw, None)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    except Exception:
        return None


async def search_public_info(phone: str) -> LookupResult:
    """Safe demo lookup: replace with compliant data providers and consent-based sources."""
    await asyncio.sleep(0.2)
    return LookupResult(
        phone=phone,
        owner_name=None,
        social_profiles={
            "Google": f"https://www.google.com/search?q={phone}",
            "Telegram": f"https://t.me/s/{re.sub(r'^\+', '', phone)}",
        },
        contacts={},
        location=None,
        notes=[
            "Only publicly indexed links are returned.",
            "Do not use this bot for stalking, harassment, or privacy violations.",
        ],
    )


def format_result(result: LookupResult) -> str:
    lines = [
        f"📞 *Phone:* `{result.phone}`",
        f"👤 *Owner:* {result.owner_name or 'N/A'}",
        f"📍 *Location:* {result.location or 'N/A'}",
        "",
        "🔗 *Profiles / links:*",
    ]
    if result.social_profiles:
        lines.extend([f"- {k}: {v}" for k, v in result.social_profiles.items()])
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("📬 *Contacts:*")
    if result.contacts:
        lines.extend([f"- {k}: {v}" for k, v in result.contacts.items()])
    else:
        lines.append("- N/A")

    if result.notes:
        lines.append("")
        lines.append("ℹ️ *Notes:*")
        lines.extend([f"- {note}" for note in result.notes])

    return "\n".join(lines)


db = Database(DB_PATH)
limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(t(user_id, "start", db), parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(t(user_id, "help", db), parse_mode=ParseMode.MARKDOWN)


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(t(user_id, "bad_lang", db))
        return
    lang = context.args[0].lower()
    if lang not in LANGUAGES:
        await update.message.reply_text(t(user_id, "bad_lang", db))
        return
    db.set_lang(user_id, lang)
    await update.message.reply_text(t(user_id, "set_lang", db))


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not limiter.allow(user_id):
        await update.message.reply_text(t(user_id, "rate_limited", db))
        return

    if not context.args:
        await update.message.reply_text(t(user_id, "search_usage", db), parse_mode=ParseMode.MARKDOWN)
        return

    phone = normalize_phone(context.args[0])
    if not phone:
        await update.message.reply_text(t(user_id, "invalid_phone", db))
        return

    logger.info("Search requested by user_id=%s phone=%s", user_id, phone)
    result = await search_public_info(phone)
    result_dict = {
        "phone": result.phone,
        "owner_name": result.owner_name,
        "social_profiles": result.social_profiles,
        "contacts": result.contacts,
        "location": result.location,
        "notes": result.notes,
    }
    db.save_search(user_id, phone, result_dict)
    await update.message.reply_text(format_result(result), parse_mode=ParseMode.MARKDOWN)


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = db.get_history(user_id)
    if not rows:
        await update.message.reply_text(t(user_id, "no_history", db))
        return

    text = "\n".join([f"- {phone} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))})" for phone, ts in rows])
    await update.message.reply_text(text)


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    result = db.get_last_result(user_id)
    if not result:
        await update.message.reply_text(t(user_id, "nothing_to_export", db))
        return

    export_path = Path(f"export_{user_id}_{int(time.time())}.txt")
    with export_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2))

    with export_path.open("rb") as f:
        await update.message.reply_document(document=f, filename=export_path.name)

    export_path.unlink(missing_ok=True)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN environment variable")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("export", export_cmd))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
