import os
import random
import asyncio
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from aiogram.filters import Command
import aiosqlite
from aiogram.client.session.middlewares import UptimeMiddleware

# === Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Установите в Secrets
PHOTOS_DIR = 'photos/'
DAILY_RESET_HOUR = 0  # Ежедневный сброс попыток

# === Клавиатура ===
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Получить фото")],
        [KeyboardButton(text="Рейтинг"), KeyboardButton(text="Забей пенальти"), KeyboardButton(text="Моя коллекция")]
    ],
    resize_keyboard=True
)

# === Загрузка данных из JSON ===
def load_captions():
    try:
        with open("captions.json", "r", encoding="utf-8-sig") as f:
            content = f.read()
            if not content.strip():
                return {}
            return json.loads(content)
    except Exception as e:
        print(f"[Ошибка captions.json] {e}")
        return {}

def load_card_names():
    try:
        with open("card_names.json", "r", encoding="utf-8-sig") as f:
            content = f.read()
            if not content.strip():
                return {}
            return json.loads(content)
    except Exception as e:
        print(f"[Ошибка card_names.json] {e}")
        return {}

captions = load_captions()
card_names = load_card_names()

# === Глобальные переменные ===
user_card_list = []
current_index = 0

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
@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "User"
    full_name = message.from_user.full_name
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name)
        )
        await db.commit()
    await message.answer("Привет! Добро пожаловать в бота!", reply_markup=main_keyboard)

@dp.message(F.text == "Получить фото")
async def get_photo(message: Message):
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

@dp.message(F.text == "Забей пенальти")
async def penalty_kick(message: Message):
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

@dp.message(F.text == "Рейтинг")
async def show_rating(message: Message):
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

@dp.message(F.text == "Моя коллекция")
async def view_collection_list(message: Message):
    user_id = message.from_user.id
    all_cards = list(card_names.values())
    if not all_cards:
        await message.answer("Нет доступных карточек.")
        return

    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute("SELECT DISTINCT card_name FROM user_cards WHERE user_id = ?", (user_id,))
        rows = await cur.fetchall()
        user_cards_set = set(row[0] for row in rows)

    collection_text = "📦 Ваша коллекция:\n\n"
    for card_title in all_cards:
        if card_title in user_cards_set:
            collection_text += f"✅ {card_title}\n"
        else:
            collection_text += f"❌ {card_title}\n"

    await message.answer(collection_text)

# === Ежедневный сброс попыток ===
async def daily_scheduler():
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=DAILY_RESET_HOUR, minute=0, second=0, microsecond=0)
        delay = (next_run - now).total_seconds()
        print(f"[INFO] Следующий сброс попыток через {delay:.0f} секунд")
        await asyncio.sleep(delay)
        await reset_daily_attempts()

# === Запуск бота ===
async def main():
    await init_db()
    dp.startup.register(daily_scheduler)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())