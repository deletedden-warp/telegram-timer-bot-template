import asyncio
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import psycopg2
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# ================= DB =================

url = urlparse(DATABASE_URL)

conn = psycopg2.connect(
    database=url.path[1:],
    user=url.username,
    password=url.password,
    host=url.hostname,
    port=url.port
)

conn.autocommit = True
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    nickname TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    name TEXT,
    type TEXT,
    hours INTEGER
)
""")

# ================= STATE =================

user_states = {}

# ================= HELPERS =================

async def delete_safe(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except:
        pass

async def auto_delete(chat_id, msg_id, delay=30):
    await asyncio.sleep(delay)
    await delete_safe(chat_id, msg_id)

def clear_user_messages(uid):
    if uid in user_states:
        for m in user_states[uid].get("messages", []):
            asyncio.create_task(delete_safe(m["chat"], m["id"]))
        user_states[uid]["messages"] = []

def save_msg(uid, msg):
    user_states.setdefault(uid, {}).setdefault("messages", []).append({
        "chat": msg.chat.id,
        "id": msg.message_id
    })

async def get_nick(uid):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (uid,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Создать запись", callback_data="create"))
    kb.add(InlineKeyboardButton("Удалить мои записи", callback_data="del_all"))
    kb.add(InlineKeyboardButton("Посмотреть все записи", callback_data="all"))
    kb.add(InlineKeyboardButton("Изменить ник", callback_data="edit_nick"))
    kb.add(InlineKeyboardButton("Удалиться из базы", callback_data="delete_me"))
    return kb

def type_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Строим", callback_data="type_build"))
    kb.add(InlineKeyboardButton("Исследуем", callback_data="type_research"))
    kb.add(InlineKeyboardButton("Назад", callback_data="back_menu"))
    return kb

def range_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("1-30", callback_data="r_1"),
        InlineKeyboardButton("31-60", callback_data="r_31"),
    )
    kb.add(
        InlineKeyboardButton("61-90", callback_data="r_61"),
        InlineKeyboardButton("91-120", callback_data="r_91"),
    )
    kb.add(InlineKeyboardButton("Назад", callback_data="back_type"))
    return kb

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"d_{i}"))
    kb.add(InlineKeyboardButton("Назад", callback_data="back_range"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"h_{i}"))
    kb.add(InlineKeyboardButton("Назад", callback_data="back_days"))
    return kb

# ================= START =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    uid = msg.from_user.id

    nick = await get_nick(uid)

    if not nick:
        user_states[uid] = {"step": "nick"}
        m = await msg.answer("Кто ты воин? (Введи свой игровой ник)")
        save_msg(uid, m)
        return

    m = await msg.answer("Меню:", reply_markup=menu_kb())
    save_msg(uid, m)

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    # ===== регистрация =====
    if st.get("step") == "nick":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        clear_user_messages(uid)

        m = await msg.answer("Готово, воин. Теперь выбирай:", reply_markup=menu_kb())
        save_msg(uid, m)
        return

    # ===== смена ника =====
    if st.get("step") == "edit_nick":
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s",
                       (msg.text.strip(), uid))

        clear_user_messages(uid)

        m = await msg.answer(f"Теперь ты {msg.text.strip()}")
        save_msg(uid, m)
        return

# ================= CALLBACK =================

@dp.callback_query_handler(lambda c: True)
async def callbacks(c: CallbackQuery):
    uid = c.from_user.id
    data = c.data

    await c.answer()

    # ===== MENU =====
    if data == "back_menu":
        clear_user_messages(uid)
        m = await c.message.answer("Меню:", reply_markup=menu_kb())
        save_msg(uid, m)
        return

    # ===== CREATE =====
    if data == "create":
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (uid,))
        if cursor.fetchone()[0] >= 2:
            return await c.message.answer("У тебя уже есть созданные записи, удали лишнее")

        clear_user_messages(uid)

        user_states[uid] = {"step": "type"}

        m = await c.message.answer("Что делаем?", reply_markup=type_kb())
        save_msg(uid, m)
        return

    # ===== TYPE =====
    if data.startswith("type_"):
        user_states[uid]["type"] = "Строим" if "build" in data else "Исследуем"
        user_states[uid]["step"] = "range"

        clear_user_messages(uid)

        m = await c.message.answer("Сколько дней?", reply_markup=range_kb())
        save_msg(uid, m)
        return

    # ===== RANGE =====
    if data.startswith("r_"):
        start = int(data.split("_")[1])
        user_states[uid]["step"] = "day"

        clear_user_messages(uid)

        m = await c.message.answer("Выбери день", reply_markup=days_kb(start))
        save_msg(uid, m)
        return

    # ===== DAY =====
    if data.startswith("d_"):
        user_states[uid]["days"] = int(data.split("_")[1])
        user_states[uid]["step"] = "hours"

        clear_user_messages(uid)

        m = await c.message.answer("Выбери часы", reply_markup=hours_kb())
        save_msg(uid, m)
        return

    # ===== HOURS =====
    if data.startswith("h_"):
        st = user_states.get(uid)
        if not st:
            return

        hours = int(data.split("_")[1])
        total = st["days"] * 24 + hours

        nick = await get_nick(uid)

        cursor.execute("""
        INSERT INTO tasks (user_id,name,type,hours)
        VALUES (%s,%s,%s,%s)
        """, (uid, nick, st["type"], total))

        clear_user_messages(uid)

        m = await c.message.answer("Запись создана. Ты молодец!")
        save_msg(uid, m)

        user_states.pop(uid, None)
        return

    # ===== DELETE ALL =====
    if data == "del_all":
        cursor.execute("DELETE FROM tasks WHERE user_id=%s", (uid,))
        return await c.message.answer("Удалено")

    # ===== ALL =====
    if data == "all":
        cursor.execute("SELECT name,type,hours FROM tasks")

        rows = cursor.fetchall()

        if not rows:
            return await c.message.answer("Созданных записей нет, создай свою")

        text = ""
        for i, (n, t, h) in enumerate(rows, 1):
            text += f"{i}) {n} | {t} | {h//24}д {h%24}ч\n"

        return await c.message.answer(text)

    # ===== EDIT NICK =====
    if data == "edit_nick":
        user_states[uid] = {"step": "edit_nick"}
        return await c.message.answer("Введи новый ник")

    # ===== DELETE USER =====
    if data == "delete_me":
        cursor.execute("DELETE FROM users WHERE user_id=%s", (uid,))
        return await c.message.answer("Я тебя забыл...")

# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp)
