import os
import random
import asyncio
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from aiogram.filters import Command
import asyncpg

# === Настройка логирования ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
PHOTOS_DIR = "photos/"
DAILY_RESET_HOUR = 0  # Ежедневный сброс попыток

if not BOT_TOKEN:
    logger.error("Не установлен BOT_TOKEN")
    exit(1)

if not os.path.exists(PHOTOS_DIR):
    os.makedirs(PHOTOS_DIR)

# === Клавиатура ===
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Получить фото")],
        [KeyboardButton(text="Рейтинг"), KeyboardButton(text="Забей пенальти"), KeyboardButton(text="Моя коллекция")]
    ],
    resize_keyboard=True
)

# === Загрузка JSON файлов ===
def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except Exception as e:
        logger.error(f"[Ошибка {filename}] {e}")
        return {}

captions = load_json("captions.json")
card_names = load_json("card_names.json")

# === Подключение к PostgreSQL ===
async def get_db():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL не установлена")
        exit(1)
    pool = await asyncpg.create_pool(db_url)
    return pool

# === Инициализация таблиц ===
async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                last_photo_time REAL DEFAULT 0,
                points INTEGER DEFAULT 0,
                daily_attempts INTEGER DEFAULT 3,
                photo_attempts INTEGER DEFAULT 3,
                daily_reset_date TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_cards (
                user_id INTEGER,
                card_name TEXT,
                PRIMARY KEY (user_id, card_name),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
    logger.info("✅ Таблицы проверены/созданы")

# === Вспомогательные функции ===
def get_photos():
    return list(captions.keys())

def get_cooldown_remaining(last_time, cooldown_seconds):
    remaining = int(cooldown_seconds - (datetime.now().timestamp() - last_time))
    return max(0, remaining)

async def reset_daily_attempts(pool):
    today = datetime.now().date().isoformat()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET daily_attempts = 3, photo_attempts = 3, daily_reset_date = $1", today)
    logger.info("🔄 Попытки пользователей сброшены")

async def daily_scheduler(pool):
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=DAILY_RESET_HOUR, minute=0, second=0, microsecond=0)
        delay = (next_run - now).total_seconds()
        logger.info(f"[INFO] Следующий сброс попыток через {delay:.0f} секунд")
        await asyncio.sleep(delay)
        await reset_daily_attempts(pool)

# === Обработчики команд ===
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: Message, pool=None):
    user_id = message.from_user.id
    username = message.from_user.username or "User"
    full_name = message.from_user.full_name

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, full_name)

    await message.answer("Привет! Добро пожаловать в бота!", reply_markup=main_keyboard)

@dp.message(F.text == "Получить фото")
async def get_photo(message: Message):
    pool = dp["pool"]
    bot = dp["bot"]  # ✅ Получаем бота из контекста

    user_id = message.from_user.id
    cooldown_seconds = 3600  # 1 час

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_photo_time FROM users WHERE user_id = $1", user_id)
        last_time = row['last_photo_time'] if row else 0

        remaining = get_cooldown_remaining(last_time, cooldown_seconds)
        if remaining > 0:
            await message.answer(f"Подождите {remaining} секунд до следующего фото.")
            return

        photo_files = get_photos()
        if not photo_files:
            await message.answer("Фотографий пока нет.")
            return

        photo_name = random.choice(photo_files)
        photo_path = os.path.join(PHOTOS_DIR, photo_name)
        description = captions.get(photo_name, "Интересное фото!")
        card_name = card_names.get(photo_name, "Неизвестная карточка")

        caption = f"{description}\n\n✨ Забирай карточку, друг! Она уже добавлена в твою коллекцию!\n\n{card_name}"

        photo = FSInputFile(photo_path)
        await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption)

        await conn.execute("""
            INSERT INTO user_cards (user_id, card_name)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
        """, user_id, card_name)

        await conn.execute("""
            UPDATE users
            SET last_photo_time = $1, points = points + 1
            WHERE user_id = $2
        """, datetime.now().timestamp(), user_id)

@dp.message(F.text == "Забей пенальти")
async def penalty_kick(message: Message, pool=None):
    user_id = message.from_user.id

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT daily_attempts FROM users WHERE user_id = $1", user_id)
        attempts_left = row['daily_attempts'] if row else 0

        if attempts_left <= 0:
            await message.answer("У вас закончились попытки на сегодня.")
            return

        dice_msg = await bot.send_dice(chat_id=message.chat.id, emoji="⚽")
        result = dice_msg.dice.value

        if result in [4, 5]:  # Гол
            await message.answer("🎉 Вы забили гол!")
            await message.answer("🎁 Вы получаете +1 попытку получить фото!")
            await conn.execute("UPDATE users SET points = points + 1 WHERE user_id = $1", user_id)
        else:
            await message.answer("😢 Мяч не в воротах. Повезёт в следующий раз!")

        await conn.execute("UPDATE users SET daily_attempts = daily_attempts - 1 WHERE user_id = $1", user_id)

@dp.message(F.text == "Рейтинг")
async def show_rating(message: Message, pool=None):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT full_name, points FROM users ORDER BY points DESC LIMIT 10")
        rating_text = "🏆 ТОП-10 игроков:\n\n"
        for i, (full_name, points) in enumerate(rows, start=1):
            rating_text += f"{i}. {full_name} — {points} очков\n"

        user_row = await conn.fetchrow("SELECT points FROM users WHERE user_id = $1", message.from_user.id)
        user_points = user_row['points'] if user_row else 0

        higher_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE points > $1", user_points)

        rating_text += f"\n📌 Вы: {higher_users + 1}-е место | Очков: {user_points}"
        await message.answer(rating_text)

@dp.message(F.text == "Моя коллекция")
async def view_collection_list(message: Message, pool=None):
    all_cards = set(card_names.values())
    if not all_cards:
        await message.answer("Нет доступных карточек.")
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT card_name FROM user_cards WHERE user_id = $1", message.from_user.id)
        user_cards_set = set(row['card_name'] for row in rows)

    collection_text = "📦 Ваша коллекция:\n\n"
    for idx, card_title in enumerate(sorted(all_cards), 1):
        status = "✅" if card_title in user_cards_set else "❌"
        collection_text += f"{status} {card_title}\n"

    await message.answer(collection_text)

# === Основной запуск ===
async def main():
    pool = await get_db()
    await init_db(pool)

    dp["pool"] = pool  # Передача пула в диспетчер
    bot = Bot(token=BOT_TOKEN)

    # ✅ Исправленная регистрация ежедневного сброса
    asyncio.create_task(daily_scheduler(pool))

    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
