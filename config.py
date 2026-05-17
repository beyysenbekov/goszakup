import os

# Токен Telegram-бота (получить у @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Ваш chat_id (узнать у @userinfobot)
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

# Интервал проверки в часах
CHECK_INTERVAL_HOURS = 12

# Ключевые слова для фильтра (запасной вариант, основной — КАТО-код "63...")
DELIVERY_REGIONS = [
    "Абай",
    "Мақаншы",
    "Маканшы",
    "Abay",
    "Makanshi",
]
