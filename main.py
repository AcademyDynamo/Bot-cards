import os
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from aiogram.filters import Command
import aiosqlite

# === Настройки ===
BOT_TOKEN = '7923361349:AAGTPCue8uRWM99CwX2cFNIQX1M46WRFKJY'  # Замени на свой токен
PHOTOS_DIR = 'photos/'
ADMIN_ID = 642749210  # ID администратора
DAILY_RESET_HOUR = 0   # Не используется в этом примере

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === Клавиатура ===
main_keyboard = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="Получить фото")],
    [KeyboardButton(text="Рейтинг"), KeyboardButton(text="Забей пенальти"), KeyboardButton(text="Моя коллекция")]
], resize_keyboard=True)

collection_keyboard = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="⬅️ Предыдущая"), KeyboardButton(text="➡️ Следующая")],
    [KeyboardButton(text="Вернуться в меню")]
], resize_keyboard=True)

# === JSON загрузка (замените на свои) ===
def load_captions():
    return {
        "photo1.jpg": "Красивый закат над морем",
        "photo2.jpg": "Милый котик спит"
    }

def load_card_names():
    return {
        "photo1.jpg": "Закат над океаном",
        "photo2.jpg": "Милый котик на подоконнике"
    }

captions = load_captions()
card_names = load_card_names()

all_cards = list(card_names.values())

# === Глобальные переменные для просмотра коллекции ===
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
                daily_attempts INTEGER DEFAULT 3
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
        card_title = card_names.get(photo_name, "Неизвестная карточка")

        caption = f"{description}\n\n✨ Забирай карточку, друг! Она уже добавлена в твою коллекцию!\n\n{card_title}"

        if not os.path.exists(photo_path):
            await message.answer("Фото не найдено.")
            return

        photo = FSInputFile(photo_path)
        await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption)

        await db.execute(
            "INSERT OR IGNORE INTO user_cards (user_id, card_name) VALUES (?, ?)",
            (user_id, card_title)
        )

        await db.execute(
            "UPDATE users SET last_photo_time = ?, points = points + 1 WHERE user_id = ?",
            (datetime.now().timestamp(), user_id)
        )

        await db.commit()

# === Игра "Забей пенальти" через эмодзи мяча ⚽ ===
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
        result = dice_msg.dice.value  # значение от 1 до 6

        if result in [4, 5]:  # Гол
            await message.answer("🎉 Отличный удар! Вы забили гол!")
            await message.answer("🎁 Вы получаете +1 попытку получить фото!")

            await db.execute("UPDATE users SET daily_attempts = daily_attempts + 1 WHERE user_id = ?", (user_id,))
        else:
            await message.answer("😢 Мяч не в воротах. Повезёт в следующий раз!")

        await db.execute("UPDATE users SET daily_attempts = daily_attempts - 1 WHERE user_id = ?", (user_id,))
        await db.commit()

# === Рейтинг пользователей по очкам ===
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

# === Просмотр коллекции списком с галочками ✅ / ❌ ===
@dp.message(F.text == "Моя коллекция")
async def view_collection(message: Message):
    user_id = message.from_user.id
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

# === Навигация по коллекции (листание) ===
@dp.message(F.text == "⬅️ Предыдущая")
async def prev_card(message: Message):
    global current_index, user_card_list
    if not user_card_list:
        await message.answer("Вы ещё не загрузили коллекцию.")
        return

    current_index = (current_index - 1) % len(user_card_list)
    card_name = user_card_list[current_index]
    photo_path = os.path.join(PHOTOS_DIR, card_name)
    description = card_names.get(card_name, "Описание недоступно")

    if not os.path.exists(photo_path):
        await message.answer(f"Фото '{card_name}' не найдено.")
        return

    caption = f"{card_name}\n\n{description}"
    photo = FSInputFile(photo_path)
    await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption)

@dp.message(F.text == "➡️ Следующая")
async def next_card(message: Message):
    global current_index, user_card_list
    if not user_card_list:
        await message.answer("Вы ещё не загрузили коллекцию.")
        return

    current_index = (current_index + 1) % len(user_card_list)
    card_name = user_card_list[current_index]
    photo_path = os.path.join(PHOTOS_DIR, card_name)
    description = card_names.get(card_name, "Описание недоступно")

    if not os.path.exists(photo_path):
        await message.answer(f"Фото '{card_name}' не найдено.")
        return

    caption = f"{card_name}\n\n{description}"
    photo = FSInputFile(photo_path)
    await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption)

@dp.message(F.text == "Вернуться в меню")
async def back_to_menu(message: Message):
    global user_card_list, current_index
    user_card_list = []
    current_index = 0
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_keyboard)

# === Ежедневный сброс попыток (можно раскомментировать при необходимости) ===
# async def reset_daily_attempts():
#     pass

# === Запуск бота ===
async def main():
    await init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
