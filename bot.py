import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import BOT_TOKEN, CHECK_INTERVAL_HOURS
from database import Database
from parser import GoszakupParser

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

db = Database()
parser = GoszakupParser()
scheduler = AsyncIOScheduler()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_subscriber(update.effective_chat.id)
    text = (
        "👋 *Бот мониторинга Госзакупок запущен!*\n\n"
        "📍 Регион: *Область Абай, Район Мақаншы*\n"
        f"⏰ Проверка каждые *{CHECK_INTERVAL_HOURS} часов*\n"
        "🔓 Работает *без API токена*\n\n"
        "📋 *Команды:*\n"
        "/start — запустить / возобновить\n"
        "/stop — остановить уведомления\n"
        "/status — статус подписки\n"
        "/check — проверить прямо сейчас\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.deactivate_subscriber(update.effective_chat.id)
    await update.message.reply_text("⏸ Уведомления остановлены. /start — возобновить.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sub = db.get_subscriber(update.effective_chat.id)
    if sub and sub["active"]:
        text = (
            f"✅ *Подписка активна*\n"
            f"📍 Регион: Область Абай / Район Мақаншы\n"
            f"⏰ Проверка каждые {CHECK_INTERVAL_HOURS} ч."
        )
    else:
        text = "❌ Подписка не активна. Нажмите /start"
    await update.message.reply_text(text, parse_mode="Markdown")


async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Проверяю новые объявления...")
    count = await fetch_and_notify(context.application)
    await update.message.reply_text(
        f"✅ Готово! Новых объявлений по вашему региону: *{count}*",
        parse_mode="Markdown"
    )


async def fetch_and_notify(app: Application) -> int:
    logger.info("Проверка новых объявлений...")
    try:
        announcements = await parser.get_new_announcements()
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return 0

    subscribers = db.get_active_subscribers()
    new_count = 0

    for ann in announcements:
        ann_id = str(ann.get("id", ""))
        if db.is_sent(ann_id):
            continue
        db.mark_sent(ann_id)
        new_count += 1
        for sub in subscribers:
            try:
                await send_announcement(app.bot, sub["chat_id"], ann)
            except Exception as e:
                logger.error(f"Ошибка отправки {sub['chat_id']}: {e}")

    logger.info(f"Новых: {new_count}")
    return new_count


async def send_announcement(bot, chat_id: int, ann: dict):
    ann_id = ann.get("id", "")
    name = ann.get("name_ru") or ann.get("name_kz") or f"Объявление #{ann_id}"
    number = ann.get("number_anno", ann_id)
    end_date = (ann.get("end_date") or "")[:10] or "не указана"
    publish_date = (ann.get("publish_date") or "")[:10] or ""
    delivery = parser.format_delivery(ann)

    try:
        amount_str = f"{float(ann.get('total_sum', 0)):,.0f} ₸".replace(",", " ")
    except (ValueError, TypeError):
        amount_str = "не указана"

    url = f"https://goszakup.gov.kz/ru/announce/index/{ann_id}"
    text = (
        f"📢 *Новое объявление №{number}*\n\n"
        f"📋 {name}\n\n"
        f"💰 Сумма: *{amount_str}*\n"
        f"📍 Место доставки: {delivery}\n"
        f"📅 Срок подачи: {end_date}\n"
    )
    if publish_date:
        text += f"🗓 Опубликовано: {publish_date}\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Открыть на сайте", url=url)]
    ])
    await bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode="Markdown", reply_markup=keyboard,
        disable_web_page_preview=True
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("check", check_now))

    scheduler.add_job(fetch_and_notify, "interval",
                      hours=CHECK_INTERVAL_HOURS, args=[app], id="check_job")
    scheduler.start()
    logger.info(f"Бот запущен. Без токена API. Проверка каждые {CHECK_INTERVAL_HOURS} ч.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
