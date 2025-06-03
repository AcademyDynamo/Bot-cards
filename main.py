import os
import random
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from aiogram.filters import Command
import aiosqlite

# === Настройка логирования ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Установите в Secrets
PHOTOS_DIR = 'photos/'
DAILY_RESET_HOUR = 0  # Ежедневный сброс попыток

if not BOT_TOKEN:
    logger.error("Не установлен BOT_TOKEN")
    sys.exit(1)

# Проверяем наличие необходимных файлов
for required_file in ["captions.json", "card_names.json"]:
    if not os.path.exists(required_file):
        logger.error(f"Файл {required_file} не найден!")
        sys.exit(1)

if not os.path.exists(PHOTOS_DIR) or not os.listdir(PHOTOS_DIR):
    logger.warning(f"Папка {PHOTOS_DIR} отсутствует или пуста")

# === Клавиатура ===
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Получить фото")],
        [KeyboardButton(text="Рейтинг"), KeyboardButton(text="Забей пенальти"), KeyboardButton(text="Моя коллекция")]
    ],
    resize_keyboard=True
)

# === Загрузка данных из JSON ===
def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8-sig") as f:
            content = f.read()
            if not content.strip():
                logger.warning(f"{filename} пустой")
                return {}
            return json.loads(content)
    except Exception as e:
        logger.error(f"[Ошибка {filename}] {e}")
        return {}

captions = load_json("captions.json")
card_names = load_json("card_names.json")

# === Инициализация БД ===
async def init_db():
    async with aiosqlite.connect("database.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                last_photo_time REAL DEFAULT 0,
                points INTEGER DEFAULT 0,
                daily_attempts INTEGER DEFAULT 3,
                photo_attempts INTEGER DEFAULT 3,
                daily_reset_date TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_cards (
                user_id INTEGER,
                card_name TEXT,
                PRIMARY KEY (user_id, card_name),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()

# === Вспомогательные функции ===
def get_photos():
    return list(captions.keys())

def get_cooldown_remaining(last_time, cooldown_seconds):
    remaining = int(cooldown_seconds - (datetime.now().timestamp() - last_time))
    return max(0, remaining)

async def reset_daily_attempts():
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect("database.db") as db:
        await db.execute("UPDATE users SET daily_attempts = 3, photo_attempts = 3, daily_reset_date = ?", (today,))
        await db.commit()

# === Обработчики команд ===
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "User"
        full_name = message.from_user.full_name
        logger.info(f"Новый пользователь: {full_name} ({user_id})")
        async with aiosqlite.connect("database.db") as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                (user_id, username, full_name)
            )
            await db.commit()
        await message.answer("Привет! Добро пожаловать в бота!", reply_markup=main_keyboard)
    except Exception as e:
        logger.error(f"[/start] Ошибка: {e}")

@dp.message(F.text == "Получить фото")
async def get_photo(message: Message):
    try:
        user_id = message.from_user.id
        cooldown_seconds = 3600  # Теперь кулдаун 1 час
        async with aiosqlite.connect("database.db") as db:
            cur = await db.execute("SELECT last_photo_time FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            last_time = row[0] if row else 0

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

            await db.execute(
                "INSERT OR IGNORE INTO user_cards (user_id, card_name) VALUES (?, ?)",
                (user_id, card_name)
            )

            await db.execute(
                "UPDATE users SET last_photo_time = ?, points = points + 1 WHERE user_id = ?",
                (datetime.now().timestamp(), user_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[Получить фото] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка. Сообщите администратору.")

@dp.message(F.text == "Забей пенальти")
async def penalty_kick(message: Message):
    try:
        user_id = message.from_user.id
        async with aiosqlite.connect("database.db") as db:
            cur = await db.execute("SELECT daily_attempts FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            attempts_left = row[0] if row else 0

            if attempts_left <= 0:
                await message.answer("У вас закончились попытки на сегодня.")
                return

            dice_msg = await bot.send_dice(chat_id=message.chat.id, emoji="⚽")
            result = dice_msg.dice.value

            if result in [4, 5]:  # Гол
                await message.answer("🎉 Вы забили гол!")
                await message.answer("🎁 Вы получаете +1 попытку получить фото!")
                await db.execute("UPDATE users SET points = points + 1 WHERE user_id = ?", (user_id,))
            else:
                await message.answer("😢 Мяч не в воротах. Повезёт в следующий раз!")

            await db.execute("UPDATE users SET daily_attempts = daily_attempts - 1 WHERE user_id = ?", (user_id,))
            await db.commit()
    except Exception as e:
        logger.error(f"[Забей пенальти] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка. Сообщите администратору.")

@dp.message(F.text == "Рейтинг")
async def show_rating(message: Message):
    try:
        user_id = message.from_user.id
        async with aiosqlite.connect("database.db") as db:
            cur = await db.execute("SELECT full_name, points FROM users ORDER BY points DESC LIMIT 10")
            rows = await cur.fetchall()
            rating_text = "🏆 ТОП-10 игроков:\n\n"
            for i, (full_name, points) in enumerate(rows, start=1):
                rating_text += f"{i}. {full_name} — {points} очков\n"

            cur = await db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            user_points = row[0] if row else 0

            cur = await db.execute("SELECT COUNT(*) FROM users WHERE points > ?", (user_points,))
            higher_users = (await cur.fetchone())[0]

            rating_text += f"\n📌 Вы: {higher_users + 1}-е место | Очков: {user_points}"
            await message.answer(rating_text)
    except Exception as e:
        logger.error(f"[Рейтинг] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка. Сообщите администратору.")

@dp.message(F.text == "Моя коллекция")
async def view_collection_list(message: Message):
    try:
        user_id = message.from_user.id
        all_cards = set(card_names.values())
        if not all_cards:
            await message.answer("Нет доступных карточек.")
            return

        async with aiosqlite.connect("database.db") as db:
            cur = await db.execute("SELECT DISTINCT card_name FROM user_cards WHERE user_id = ?", (user_id,))
            rows = await cur.fetchall()
            user_cards_set = set(row[0] for row in rows)

        collection_text = "📦 Ваша коллекция:\n\n"
        for idx, card_title in enumerate(sorted(all_cards), 1):
            status = "✅" if card_title in user_cards_set else "❌"
            collection_text += f"{status} {card_title}\n"

        await message.answer(collection_text)
    except Exception as e:
        logger.error(f"[Моя коллекция] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка. Сообщите администратору.")

# === Ежедневный сброс попыток ===
async def daily_scheduler():
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=DAILY_RESET_HOUR, minute=0, second=0, microsecond=0)
        delay = (next_run - now).total_seconds()
        logger.info(f"[INFO] Следующий сброс попыток через {delay:.0f} секунд")
        await asyncio.sleep(delay)
        await reset_daily_attempts()

# === Обработка SIGTERM ===
def handle_sigterm(*args):
    logger.info("Получен сигнал SIGTERM. Завершаем работу...")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

# === Запуск бота ===
async def main():
    logger.info("Инициализация базы данных...")
    await init_db()
    dp.startup.register(daily_scheduler)
    bot = Bot(token=BOT_TOKEN)
    logger.info("Бот запущен. Начинаем опрос сообщений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        logger.info("Запуск бота...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал CTRL+C. Завершение работы.")
