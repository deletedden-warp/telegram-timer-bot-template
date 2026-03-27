import asyncio
import os
from urllib.parse import urlparse
from datetime import datetime, timedelta

import psycopg2
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from aiogram.dispatcher.filters import Text

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

def init_user(uid, chat_id):
    user_states[uid] = {
        "step": None,
        "msgs": [],
        "chat": chat_id,
        "start_time": datetime.utcnow()
    }

def track(uid, msg):
    user_states[uid]["msgs"].append((msg.chat.id, msg.message_id))

async def clear_msgs(uid):
    for chat_id, msg_id in user_states.get(uid, {}).get("msgs", []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

def check_timeout(uid):
    st = user_states.get(uid)
    if not st:
        return False
    return datetime.utcnow() - st["start_time"] > timedelta(minutes=3)

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
    kb.add(InlineKeyboardButton("Изменить мой никнейм", callback_data="edit_nick"))
    kb.add(InlineKeyboardButton("Удалиться из базы", callback_data="delete_me"))
    return kb

def type_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Строим", callback_data="type_build"),
        InlineKeyboardButton("Исследуем", callback_data="type_research")
    )
    kb.add(InlineKeyboardButton("Назад", callback_data="back_menu"))
    return kb

def range_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("1-30", callback_data="r_1"),
        InlineKeyboardButton("31-60", callback_data="r_31")
    )
    kb.add(
        InlineKeyboardButton("61-90", callback_data="r_61"),
        InlineKeyboardButton("91-120", callback_data="r_91")
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
    kb.add(InlineKeyboardButton("Назад", callback_data="back_day"))
    return kb

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    uid = msg.from_user.id

    init_user(uid, msg.chat.id)

    nick = await get_nick(uid)

    if not nick:
        user_states[uid]["step"] = "nick"
        m = await msg.answer("Кто ты воин? (Введи свой игровой ник)")
        track(uid, m)
        return

    user_states[uid]["step"] = "menu"
    m = await msg.answer("Меню", reply_markup=menu_kb())
    track(uid, m)

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    if check_timeout(uid):
        await clear_msgs(uid)
        user_states.pop(uid, None)
        return await msg.answer("Время вышло. Введи /menu")

    # регистрация
    if st["step"] == "nick":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s,%s)
        ON CONFLICT (user_id) DO UPDATE SET nickname=EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        await clear_msgs(uid)

        init_user(uid, msg.chat.id)
        user_states[uid]["step"] = "menu"

        m = await msg.answer("Меню", reply_markup=menu_kb())
        track(uid, m)

    # смена ника
    elif st["step"] == "edit_nick":
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s",
                       (msg.text.strip(), uid))

        await clear_msgs(uid)

        m = await msg.answer(f"Отлично! Я переписал твой ник, теперь ты записан как: {msg.text.strip()}")
        asyncio.create_task(delete_after(m, 60))

        user_states.pop(uid, None)

# ================= CALLBACK =================

@dp.callback_query_handler()
async def callbacks(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    if check_timeout(uid):
        await clear_msgs(uid)
        user_states.pop(uid, None)
        return await c.message.answer("Время вышло. Введи /menu")

    # ===== CREATE =====
    if c.data == "create" and st["step"] == "menu":
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (uid,))
        if cursor.fetchone()[0] >= 2:
            return await c.message.answer("у тебя уже есть созданные записи, удали лишнее")

        st["step"] = "type"
        m = await c.message.answer("Что делаем?", reply_markup=type_kb())
        track(uid, m)

    elif c.data.startswith("type_") and st["step"] == "type":
        st["type"] = "Строим" if "build" in c.data else "Исследуем"
        st["step"] = "range"

        m = await c.message.answer("Сколько осталось дней до завершения?", reply_markup=range_kb())
        track(uid, m)

    elif c.data.startswith("r_") and st["step"] == "range":
        st["range"] = int(c.data.split("_")[1])
        st["step"] = "day"

        m = await c.message.answer("Выбери день", reply_markup=days_kb(st["range"]))
        track(uid, m)

    elif c.data.startswith("d_") and st["step"] == "day":
        st["days"] = int(c.data.split("_")[1])
        st["step"] = "hours"

        m = await c.message.answer("Сколько осталось часов до завершения?", reply_markup=hours_kb())
        track(uid, m)

    elif c.data.startswith("h_") and st["step"] == "hours":
        hours = int(c.data.split("_")[1])
        total = st["days"] * 24 + hours

        nick = await get_nick(uid)

        cursor.execute("""
        INSERT INTO tasks (user_id,name,type,hours)
        VALUES (%s,%s,%s,%s)
        """, (uid, nick, st["type"], total))

        await clear_msgs(uid)

        m = await c.message.answer("Запись создана. Ты молодец!")
        asyncio.create_task(delete_after(m, 60))

        user_states.pop(uid, None)

# ================= DELETE AFTER =================

async def delete_after(msg, sec):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp)
