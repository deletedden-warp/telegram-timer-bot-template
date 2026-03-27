import asyncio
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import psycopg2
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler()

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
    hours_left INTEGER,
    delete_at TIMESTAMP,
    chat_id BIGINT,
    message_id BIGINT,
    thread_id BIGINT
)
""")

# ================= STATE =================
user_states = {}

# ================= HELPERS =================

async def delete_safe(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def send_step(chat_id, text, kb, thread_id):
    msg = await bot.send_message(chat_id, text, reply_markup=kb, message_thread_id=thread_id)
    return msg

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Создать", callback_data="create"))
    kb.add(InlineKeyboardButton("📋 Мои записи", callback_data="my"))
    kb.add(InlineKeyboardButton("❌ Удалить мои записи", callback_data="del_all"))
    return kb

def type_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("🏗 Строим", callback_data="type_build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="type_research")
    )

def range_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("1-30", callback_data="range_1"),
        InlineKeyboardButton("31-60", callback_data="range_31"),
        InlineKeyboardButton("61-90", callback_data="range_61")
    )

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"day_{i}"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"hour_{i}"))
    return kb

def del_kb(tid):
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del_{tid}")
    )

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    await delete_safe(msg.chat.id, msg.message_id)

    nick = await get_nick(msg.from_user.id)
    if not nick:
        user_states[msg.from_user.id] = {"step": "nick"}
        return await msg.answer("Введите свой ник")

    await msg.answer("Меню:", reply_markup=menu_kb())

# ================= CREATE =================

@dp.callback_query_handler(lambda c: c.data == "create")
async def create(c: CallbackQuery):
    uid = c.from_user.id
    thread = c.message.message_thread_id

    await c.answer()

    cursor.execute("SELECT id FROM tasks WHERE user_id=%s", (uid,))
    if len(cursor.fetchall()) >= 2:
        return await c.message.answer("У тебя уже есть 2 записи")

    msg = await send_step(c.message.chat.id, "Что делаем?", type_kb(), thread)

    user_states[uid] = {
        "step": "type",
        "msg_id": msg.message_id,
        "thread": thread
    }

# ================= TYPE =================

@dp.callback_query_handler(lambda c: c.data.startswith("type_"))
async def choose_type(c: CallbackQuery):
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    await c.answer()

    st["type"] = "🏗 Строим" if "build" in c.data else "🔬 Исследуем"

    await delete_safe(c.message.chat.id, st["msg_id"])

    msg = await send_step(c.message.chat.id, "Выбери диапазон", range_kb(), st["thread"])
    st["msg_id"] = msg.message_id

# ================= RANGE =================

@dp.callback_query_handler(lambda c: c.data.startswith("range_"))
async def choose_range(c: CallbackQuery):
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    await c.answer()

    start = int(c.data.split("_")[1])

    await delete_safe(c.message.chat.id, st["msg_id"])

    msg = await send_step(c.message.chat.id, "Выбери день", days_kb(start), st["thread"])
    st["msg_id"] = msg.message_id

# ================= DAY =================

@dp.callback_query_handler(lambda c: c.data.startswith("day_"))
async def choose_day(c: CallbackQuery):
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    await c.answer()

    st["days"] = int(c.data.split("_")[1])

    await delete_safe(c.message.chat.id, st["msg_id"])

    msg = await send_step(c.message.chat.id, "Выбери часы", hours_kb(), st["thread"])
    st["msg_id"] = msg.message_id

# ================= HOURS =================

@dp.callback_query_handler(lambda c: c.data.startswith("hour_"))
async def choose_hours(c: CallbackQuery):
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st or "days" not in st:
        return await c.answer("Ошибка, начни заново", show_alert=True)

    await c.answer()

    hours = int(c.data.split("_")[1])
    days = st["days"]
    total = days * 24 + hours

    nick = await get_nick(uid)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id,thread_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (uid, nick, st["type"], total,
          datetime.utcnow() + timedelta(hours=48),
          c.message.chat.id, 0, st["thread"]))

    tid = cursor.fetchone()[0]

    msg = await bot.send_message(
        c.message.chat.id,
        f"{nick}\n{st['type']}\n{days}д {hours}ч",
        reply_markup=del_kb(tid),
        message_thread_id=st["thread"]
    )

    cursor.execute("UPDATE tasks SET message_id=%s WHERE id=%s",
                   (msg.message_id, tid))

    await delete_safe(c.message.chat.id, st["msg_id"])
    user_states.pop(uid, None)

# ================= START =================

async def on_startup(dp):
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
