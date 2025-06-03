import os
import random
import logging
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

import aiosqlite

# ------- Конфигурация -------
BOT_TOKEN = "YOUR_BOT_TOKEN"  # Заменить на свой токен
PHOTOS_DIR = "photos"
DATABASE_PATH = "database.db"
COOLDOWN_HOURS = 1  # Кулдаун между получением фото
# ----------------------------

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Создание кнопок меню
def get_main_menu():
    buttons = [
        [InlineKeyboardButton(text="📸 Получить фото", callback_data="get_photo")],
        [InlineKeyboardButton(text="📦 Моя коллекция", callback_data="my_collection")],
        [InlineKeyboardButton(text="🏆 Рейтинг", callback_data="rating")],
        [InlineKeyboardButton(text="⚽ Забей пенальти", callback_data="penalty_game")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Работа с базой данных ---
async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                points INTEGER DEFAULT 0,
                last_photo_time DATETIME DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                card_name TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()

async def add_user_if_not_exists(user_id, full_name):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, full_name) VALUES (?, ?)",
            (user_id, full_name)
        )
        await db.commit()

async def can_get_photo(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT last_photo_time FROM users WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return True
            last_time = row[0]
            if last_time is None:
                return True
            cooldown = datetime.now() - datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
            return cooldown > timedelta(hours=COOLDOWN_HOURS)

async def update_last_photo_time(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET last_photo_time = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
        )
        await db.commit()

async def add_card_to_user(user_id, card_name):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO user_cards (user_id, card_name) VALUES (?, ?)",
            (user_id, card_name)
        )
        await db.commit()

async def get_user_cards(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT card_name FROM user_cards WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_user_rank(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("""
            SELECT user_id, COUNT(*) as total
            FROM user_cards
            GROUP BY user_id
            ORDER BY total DESC
        """) as cursor:
            ranks = await cursor.fetchall()
            for idx, (uid, _) in enumerate(ranks, start=1):
                if uid == user_id:
                    return idx
            return "—"

# --- Обработчики команд ---

@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    await add_user_if_not_exists(user_id, full_name)
    logger.info(f"User {user_id} started the bot.")
    await message.answer("Привет! Выбери действие:", reply_markup=get_main_menu())

@dp.callback_query(F.data == "get_photo")
async def handle_get_photo(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await can_get_photo(user_id):
        await callback.answer("Вы сможете получить новое фото позже.", show_alert=True)
        return

    photo_files = [f for f in os.listdir(PHOTOS_DIR) if f.endswith(".jpg")]
    if not photo_files:
        await callback.answer("Фотографии закончились.", show_alert=True)
        return

    photo_name = random.choice(photo_files)
    caption = f"Описание карточки: {photo_name.replace('.jpg', '')}"

    photo_path = os.path.join(PHOTOS_DIR, photo_name)
    with open(photo_path, "rb") as photo_file:
        await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo_file,
            caption=caption
        )

    await add_card_to_user(user_id, photo_name.replace(".jpg", ""))
    await update_last_photo_time(user_id)
    await callback.answer("Карточка добавлена в коллекцию!")

@dp.callback_query(F.data == "my_collection")
async def handle_my_collection(callback: CallbackQuery):
    user_id = callback.from_user.id
    cards = await get_user_cards(user_id)
    if not cards:
        await callback.answer("Коллекция пуста!", show_alert=True)
        return

    response = "✅ Ваша коллекция:\n\n" + "\n".join(cards)
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "rating")
async def handle_rating(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("""
            SELECT u.user_id, u.full_name, COUNT(c.card_name) AS count
            FROM users u LEFT JOIN user_cards c ON u.user_id = c.user_id
            GROUP BY u.user_id
            ORDER BY count DESC LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()
            rank = await get_user_rank(user_id)
            top_text = "🏆 Топ игроков:\n\n"
            for idx, (u_id, name, cnt) in enumerate(rows, start=1):
                top_text += f"{idx}. {name} — {cnt} карточек\n"
            top_text += f"\nВаше место: {rank}"
            await callback.message.answer(top_text)
    await callback.answer()

@dp.callback_query(F.data == "penalty_game")
async def handle_penalty(callback: CallbackQuery):
    result = await bot.send_dice(callback.message.chat.id, emoji="⚽")
    dice_value = result.dice.value
    if dice_value in [4, 5, 6]:
        user_id = callback.from_user.id
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "UPDATE users SET points = points + 1 WHERE user_id = ?", 
                (user_id,)
            )
            await db.commit()
        await callback.answer("ГОЛ! Вы получаете 1 бонусное очко!")
    else:
        await callback.answer("Мимо... Попробуйте ещё раз.")

# --- Запуск бота ---
async def main():
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
    await init_db()
    logger.info("База данных инициализирована.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
