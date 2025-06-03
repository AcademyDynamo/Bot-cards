import os
import random
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from aiogram.filters import Command
import aiosqlite


# === Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
PHOTOS_DIR = 'photos/'

# === Клавиатура ===
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📸 Получить фото")],
        [KeyboardButton(text="⚽ Забей пенальти"), KeyboardButton(text="📦 Моя коллекция")],
        [KeyboardButton(text="🏆 Рейтинг")]
    ],
    resize_keyboard=True
)

# === Загрузка данных из JSON ===
def load_captions():
    try:
        with open("captions.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ошибка captions.json] {e}")
        return {}

def load_card_names():
    try:
        with open("card_names.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ошибка card_names.json] {e}")
        return {}

captions = load_captions()
card_names = load_card_names()


# === Инициализация БД ===
async def init_db():
    async with aiosqlite.connect("database.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                points INTEGER DEFAULT 0
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
    print("[INFO] База данных инициализирована")


# === Вспомогательные функции ===
def get_photos():
    return list(captions.keys())

async def get_or_create_user(user_id, full_name):
    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            await db.execute(
                "INSERT INTO users (user_id, full_name) VALUES (?, ?)",
                (user_id, full_name)
            )
            await db.commit()
    print(f"[DEBUG] Пользователь {full_name} ({user_id}) зарегистрирован")


# === Обработчики команд ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    await get_or_create_user(user_id, full_name)
    await message.answer("Привет! Добро пожаловать в бота!", reply_markup=keyboard)


@dp.message(F.text == "📸 Получить фото")
async def get_photo(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    await get_or_create_user(user_id, full_name)

    photo_files = get_photos()
    if not photo_files:
        await message.answer("Фотографий пока нет.")
        return

    photo_name = random.choice(photo_files)
    photo_path = os.path.join(PHOTOS_DIR, photo_name)
    caption_text = captions.get(photo_name, "Интересное фото!")
    card_name = card_names.get(photo_name, "Неизвестная карточка")

    final_caption = f"{caption_text}\n\n✨ Карточка '{card_name}' добавлена в вашу коллекцию!"

    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT OR IGNORE INTO user_cards (user_id, card_name) VALUES (?, ?)", (user_id, card_name))
        await db.execute("UPDATE users SET points = points + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    photo = FSInputFile(photo_path)
    await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=final_caption)


@dp.message(F.text == "⚽ Забей пенальти")
async def penalty_kick(message: Message):
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.full_name)

    dice_msg = await bot.send_dice(chat_id=message.chat.id, emoji="⚽")
    result = dice_msg.dice.value

    if result in [4, 5]:  # Гол
        await message.answer("🎉 Ура! Вы забили гол!")
        await message.answer("🎁 Получите +1 к вашим очкам!")
        async with aiosqlite.connect("database.db") as db:
            await db.execute("UPDATE users SET points = points + 1 WHERE user_id = ?", (user_id,))
            await db.commit()
    else:
        await message.answer("😢 Не повезло. Попробуйте снова!")


@dp.message(F.text == "📦 Моя коллекция")
async def my_collection(message: Message):
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.full_name)

    all_cards = list(card_names.values())
    if not all_cards:
        await message.answer("Нет доступных карточек.")
        return

    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute("SELECT DISTINCT card_name FROM user_cards WHERE user_id = ?", (user_id,))
        rows = await cur.fetchall()
        user_cards = set(row[0] for row in rows)

    text = "📂 Ваша коллекция:\n\n"
    for card in all_cards:
        status = "✅" if card in user_cards else "❌"
        text += f"{status} {card}\n"

    await message.answer(text)


@dp.message(F.text == "🏆 Рейтинг")
async def rating(message: Message):
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.full_name)

    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute("SELECT full_name, points FROM users ORDER BY points DESC LIMIT 10")
        top_users = await cur.fetchall()

        cur = await db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        user_points = row[0] if row else 0

        cur = await db.execute("SELECT COUNT(*) FROM users WHERE points > ?", (user_points,))
        higher_count = (await cur.fetchone())[0]

    text = "🏆 ТОП-10 игроков:\n\n"
    for idx, (name, score) in enumerate(top_users, start=1):
        text += f"{idx}. {name} — {score} очков\n"

    text += f"\n📌 Вы: {higher_count + 1}-е место | Очков: {user_points}"
    await message.answer(text)


# === Ежедневный сброс попыток (пример) ===
async def daily_scheduler():
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        delay = (next_run - now).total_seconds()
        print(f"[INFO] Следующий сброс через {delay:.0f} секунд")
        await asyncio.sleep(delay)


# === Запуск бота ===
async def main():
    print("=== Бот стартовал ===")
    if not BOT_TOKEN:
        print("[ERROR] BOT_TOKEN не установлен")
        return

    await init_db()
    dp.startup.register(daily_scheduler)
    print("[INFO] Подключение к Telegram...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
