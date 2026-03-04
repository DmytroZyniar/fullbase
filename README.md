# Phone Lookup Telegram Bot (Python)

Асинхронний Telegram-бот на `python-telegram-bot` з командами `/start`, `/help`, `/search`, `/history`, `/export`, `/lang`.

## Важливо
Цей приклад призначений для **безпечного використання**: бот повертає лише публічно індексовані посилання та не виконує прихований збір персональних даних.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="<YOUR_TELEGRAM_BOT_TOKEN>"
python bot.py
```

## Можливості
- Асинхронні команди
- Валідація номерів у міжнародному форматі
- Anti-spam ліміт на користувача (за хвилину)
- Логування
- Збереження історії пошуків у SQLite
- Експорт останнього результату в TXT
- Перемикання мови інтерфейсу (`uk/ru/en`)

## Змінні середовища
- `BOT_TOKEN` — токен Telegram-бота (обов'язково)
- `LOG_LEVEL` — рівень логування, за замовчуванням `INFO`
- `DB_PATH` — шлях до SQLite БД, за замовчуванням `search_history.db`
- `MAX_REQUESTS_PER_MINUTE` — ліміт запитів на користувача, за замовчуванням `5`
