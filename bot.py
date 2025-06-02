import os
import random
from datetime import datetime, timedelta
import telebot
from telebot import types
import sqlite3
import json

# === Настройки ===
BOT_TOKEN = '7923361349:AAGTPCue8uRWM99CwX2cFNIQX1M46WRFKJY'  # Замените на свой токен
PHOTOS_DIR = 'photos/'
DAILY_RESET_HOUR = 0  # Ежедневный сброс попыток

# === Инициализация бота ===
bot = telebot.TeleBot(BOT_TOKEN)

# === Клавиатура ===
main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
main_keyboard.add(types.KeyboardButton("Получить фото"))
main_keyboard.add(
    types.KeyboardButton("Рейтинг"),
    types.KeyboardButton("Забей пенальти"),
    types.KeyboardButton("Моя коллекция")
)

collection_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
collection_keyboard.add(
    types.KeyboardButton("⬅️ Предыдущая"),
    types.KeyboardButton("➡️ Следующая")
)
collection_keyboard.add(types.KeyboardButton("Вернуться в меню"))

# === JSON загрузка ===
def load_captions():
    try:
        with open("captions.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def load_card_names():
    try:
        with open("card_names.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

captions = load_captions()
card_names = load_card_names()

all_cards = list(card_names.values())

# === База данных ===
def init_db():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            last_photo_time REAL DEFAULT 0,
            points INTEGER DEFAULT 0,
            daily_attempts INTEGER DEFAULT 3
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id INTEGER,
            card_name TEXT,
            PRIMARY KEY (user_id, card_name)
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()

# === Вспомогательные функции ===
def get_photos():
    return list(captions.keys())

def get_cooldown_remaining(last_time, cooldown_seconds):
    remaining = int(cooldown_seconds - (datetime.now().timestamp() - last_time))
    return max(0, remaining)

# === Команды бота ===
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "User"
    full_name = message.from_user.full_name
    cur = db_conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user_id, username, full_name))
    db_conn.commit()
    bot.send_message(message.chat.id, "Привет! Добро пожаловать в бота!", reply_markup=main_keyboard)

@bot.message_handler(func=lambda m: m.text == "Получить фото")
def get_photo(message):
    user_id = message.from_user.id
    cooldown_seconds = 3600  # 1 час

    cur = db_conn.cursor()
    cur.execute("SELECT last_photo_time FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    last_time = row[0] if row else 0

    remaining = get_cooldown_remaining(last_time, cooldown_seconds)
    if remaining > 0:
        bot.reply_to(message, f"Подождите {remaining} секунд до следующего фото.")
        return

    photo_files = get_photos()
    if not photo_files:
        bot.reply_to(message, "Фотографий пока нет.")
        return

    photo_name = random.choice(photo_files)
    photo_path = os.path.join(PHOTOS_DIR, photo_name)
    card_title = card_names.get(photo_name, "Неизвестная карточка")

    caption = f"{card_title}\n\n"
    caption += captions.get(photo_name, "Интересное фото!")
    caption += "\n\n✨ Забирай карточку, друг! Она уже добавлена в твою коллекцию!"

    if not os.path.exists(photo_path):
        bot.reply_to(message, "Файл не найден.")
        return

    with open(photo_path, "rb") as photo:
        bot.send_photo(message.chat.id, photo, caption=caption)

    cur.execute("INSERT OR IGNORE INTO user_cards (user_id, card_name) VALUES (?, ?)", (user_id, card_title))
    cur.execute("UPDATE users SET last_photo_time = ?, points = points + 1 WHERE user_id = ?", 
                (datetime.now().timestamp(), user_id))
    db_conn.commit()

@bot.message_handler(func=lambda m: m.text == "Рейтинг")
def show_rating(message):
    cur = db_conn.cursor()
    cur.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 10")
    rows = cur.fetchall()
    rating_text = "🏆 ТОП-10 игроков:\n"
    for i, (username, points) in enumerate(rows, start=1):
        rating_text += f"{i}. @{username} — {points} очков\n"

    cur.execute("SELECT points FROM users WHERE user_id = ?", (message.from_user.id,))
    user_points = cur.fetchone()[0] if cur.fetchone() else 0
    cur.execute("SELECT COUNT(*) FROM users WHERE points > ?", (user_points,))
    higher_users = cur.fetchone()[0]

    rating_text += f"\n📌 Вы: {higher_users + 1}-е место | Очков: {user_points}"
    bot.reply_to(message, rating_text)

@bot.message_handler(func=lambda m: m.text == "Забей пенальти")
def penalty_kick(message):
    user_id = message.from_user.id
    cur = db_conn.cursor()
    cur.execute("SELECT daily_attempts FROM users WHERE user_id = ?", (user_id,))
    attempts_left = cur.fetchone()[0]
    if attempts_left <= 0:
        bot.reply_to(message, "У вас закончились попытки на сегодня.")
        return

    dice_msg = bot.send_dice(message.chat.id, emoji="⚽")
    result = dice_msg.dice.value

    if result in [4, 5]:
        bot.send_message(message.chat.id, "🎉 Отличный удар! Гол!")
        bot.send_message(message.chat.id, "🎁 Вы получаете +1 попытку получить фото!")
        cur.execute("UPDATE users SET points = points + 1 WHERE user_id = ?", (user_id,))
    else:
        bot.send_message(message.chat.id, "😢 Мяч не в воротах. Повезёт в следующий раз!")

    cur.execute("UPDATE users SET daily_attempts = daily_attempts - 1 WHERE user_id = ?", (user_id,))
    db_conn.commit()

# === Просмотр коллекции ===
user_card_list = []
current_index = 0

@bot.message_handler(func=lambda m: m.text == "Моя коллекция")
def view_collection(message):
    global user_card_list, current_index
    user_id = message.from_user.id
    cur = db_conn.cursor()
    cur.execute("SELECT card_name FROM user_cards WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    if not rows:
        bot.reply_to(message, "У вас пока нет карточек в коллекции.")
        return

    user_card_list = [row[0] for row in rows]
    current_index = 0
    card_name = user_card_list[current_index]
    photo_path = os.path.join(PHOTOS_DIR, card_name)
    description = card_names.get(card_name, "Описание недоступно")

    if not os.path.exists(photo_path):
        bot.reply_to(message, f"Фото '{card_name}' не найдено.")
        return

    bot.send_photo(message.chat.id, open(photo_path, "rb"), caption=f"{card_name}\n\n{description}")
    bot.send_message(message.chat.id, "📖 Перелистывайте карточки:", reply_markup=collection_keyboard)

@bot.message_handler(func=lambda m: m.text == "⬅️ Предыдущая")
def prev_card(message):
    global user_card_list, current_index
    if not user_card_list:
        bot.reply_to(message, "Вы ещё не загрузили коллекцию.")
        return
    current_index = (current_index - 1) % len(user_card_list)
    card_name = user_card_list[current_index]
    photo_path = os.path.join(PHOTOS_DIR, card_name)
    description = card_names.get(card_name, "Описание недоступно")
    if not os.path.exists(photo_path):
        bot.reply_to(message, f"Фото '{card_name}' не найдено.")
        return
    bot.send_photo(message.chat.id, open(photo_path, "rb"), caption=f"{card_name}\n\n{description}")

@bot.message_handler(func=lambda m: m.text == "➡️ Следующая")
def next_card(message):
    global user_card_list, current_index
    if not user_card_list:
        bot.reply_to(message, "Вы ещё не загрузили коллекцию.")
        return
    current_index = (current_index + 1) % len(user_card_list)
    card_name = user_card_list[current_index]
    photo_path = os.path.join(PHOTOS_DIR, card_name)
    description = card_names.get(card_name, "Описание недоступно")
    if not os.path.exists(photo_path):
        bot.reply_to(message, f"Фото '{card_name}' не найдено.")
        return
    bot.send_photo(message.chat.id, open(photo_path, "rb"), caption=f"{card_name}\n\n{description}")

@bot.message_handler(func=lambda m: m.text == "Вернуться в меню")
def back_to_menu(message):
    global user_card_list, current_index
    user_card_list = []
    current_index = 0
    bot.send_message(message.chat.id, "Вы вернулись в главное меню.", reply_markup=main_keyboard)

# === Коллекция пользователя списком с галочками ✅ / ❌ ===
@bot.message_handler(func=lambda m: m.text == "Все карточки")
def show_all_cards(message):
    user_id = message.from_user.id
    cur = db_conn.cursor()
    cur.execute("SELECT DISTINCT card_name FROM user_cards WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    user_cards_set = set(row[0] for row in rows)

    collection_text = "📦 Ваша коллекция:\n\n"
    for card_title in all_cards:
        if card_title in user_cards_set:
            collection_text += f"✅ {card_title}\n"
        else:
            collection_text += f"❌ {card_title}\n"

    bot.reply_to(message, collection_text)

# === Запуск бота ===
if __name__ == "__main__":
    bot.polling(none_stop=True)
