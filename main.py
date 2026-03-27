import asyncio
import os
from urllib.parse import urlparse

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

async def delete_safe(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except:
        pass

async def clear_msgs(uid):
    for chat_id, msg_id in user_states.get(uid, {}).get("msgs", []):
        await delete_safe(chat_id, msg_id)

def track(uid, msg):
    user_states.setdefault(uid, {}).setdefault("msgs", []).append((msg.chat.id, msg.message_id))

def set_timer(uid):
    st = user_states.get(uid)
    if not st:
        return

    if "timer" in st:
        st["timer"].cancel()

    st["timer"] = asyncio.create_task(timeout(uid))

async def timeout(uid):
    await asyncio.sleep(60)

    st = user_states.get(uid)
    if not st:
        return

    await clear_msgs(uid)

    try:
        await bot.send_message(st["chat"], "⏱ Время вышло. Начни заново: /menu")
    except:
        pass

    user_states.pop(uid, None)

async def get_nick(uid):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (uid,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Создать", callback_data="create"))
    kb.add(InlineKeyboardButton("❌ Удалить мои", callback_data="del_all"))
    kb.add(InlineKeyboardButton("📊 Все записи", callback_data="all"))
    kb.add(InlineKeyboardButton("✏️ Ник", callback_data="edit_nick"))
    return kb

def type_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("🏗 Строим", callback_data="type_build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="type_research")
    )

def range_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("1-30", callback_data="r_1"),
        InlineKeyboardButton("31-60", callback_data="r_31"),
        InlineKeyboardButton("61-90", callback_data="r_61"),
        InlineKeyboardButton("91-120", callback_data="r_91")
    )

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"d_{i}"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"h_{i}"))
    return kb

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    uid = msg.from_user.id

    user_states[uid] = {"msgs": [], "chat": msg.chat.id}

    nick = await get_nick(uid)

    if not nick:
        user_states[uid]["step"] = "nick"
        m = await msg.answer("⚔️ Введи свой ник:")
        track(uid, m)
        return

    m = await msg.answer("📋 Меню:", reply_markup=menu_kb())
    track(uid, m)

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    set_timer(uid)

    # регистрация
    if st.get("step") == "nick":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        await clear_msgs(uid)

        m = await msg.answer("✅ Готово!", reply_markup=menu_kb())
        track(uid, m)

# ================= CREATE FLOW =================

@dp.callback_query_handler(Text(equals="create"))
async def create(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (uid,))
    if cursor.fetchone()[0] >= 2:
        return await c.message.answer("⚠️ Уже есть 2 записи")

    user_states[uid]["step"] = "type"
    set_timer(uid)

    m = await c.message.answer("❓ Что делаем?", reply_markup=type_kb())
    track(uid, m)

# TYPE
@dp.callback_query_handler(Text(startswith="type_"))
async def type_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st or st.get("step") != "type":
        return

    st["type"] = "🏗 Строим" if "build" in c.data else "🔬 Исследуем"
    st["step"] = "range"
    set_timer(uid)

    m = await c.message.answer("📅 Диапазон:", reply_markup=range_kb())
    track(uid, m)

# RANGE
@dp.callback_query_handler(Text(startswith="r_"))
async def range_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st or st.get("step") != "range":
        return

    start = int(c.data.split("_")[1])
    st["step"] = "day"
    set_timer(uid)

    m = await c.message.answer("📆 День:", reply_markup=days_kb(start))
    track(uid, m)

# DAY
@dp.callback_query_handler(Text(startswith="d_"))
async def day_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st or st.get("step") != "day":
        return

    st["days"] = int(c.data.split("_")[1])
    st["step"] = "hours"
    set_timer(uid)

    m = await c.message.answer("⏳ Часы:", reply_markup=hours_kb())
    track(uid, m)

# HOURS (ФИНАЛ)
@dp.callback_query_handler(Text(startswith="h_"))
async def hours_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st or st.get("step") != "hours":
        return

    hours = int(c.data.split("_")[1])
    total = st["days"] * 24 + hours

    nick = await get_nick(uid)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours)
    VALUES (%s,%s,%s,%s)
    """, (uid, nick, st["type"], total))

    await clear_msgs(uid)

    await c.message.answer("🎉 Запись создана!")

    user_states.pop(uid, None)

# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp)
