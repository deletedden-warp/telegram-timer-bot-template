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

async def auto_delete(chat_id, msg_id, delay=30):
    await asyncio.sleep(delay)
    await delete_safe(chat_id, msg_id)

def track_msg(uid, msg):
    user_states.setdefault(uid, {}).setdefault("msgs", []).append((msg.chat.id, msg.message_id))

async def clear_all(uid):
    for chat_id, msg_id in user_states.get(uid, {}).get("msgs", []):
        await delete_safe(chat_id, msg_id)
    user_states[uid]["msgs"] = []

async def get_nick(uid):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (uid,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Создать запись", callback_data="create"))
    kb.add(InlineKeyboardButton("❌ Удалить мои записи", callback_data="del_all"))
    kb.add(InlineKeyboardButton("📊 Все записи", callback_data="all"))
    kb.add(InlineKeyboardButton("✏️ Изменить ник", callback_data="edit_nick"))
    kb.add(InlineKeyboardButton("💀 Удалиться", callback_data="delete_me"))
    return kb

def type_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🏗 Строим", callback_data="type_build"))
    kb.add(InlineKeyboardButton("🔬 Исследуем", callback_data="type_research"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_menu"))
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
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_type"))
    return kb

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"d_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_range"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"h_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_day"))
    return kb

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    uid = msg.from_user.id

    nick = await get_nick(uid)

    if not nick:
        user_states[uid] = {"step": "nick"}
        m = await msg.answer("⚔️ Кто ты воин? Введи свой ник:")
        track_msg(uid, m)
        return

    m = await msg.answer("📋 Меню:", reply_markup=menu_kb())
    track_msg(uid, m)

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    # регистрация
    if st.get("step") == "nick":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        await clear_all(uid)

        m = await msg.answer("✅ Отлично! Теперь выбери действие:", reply_markup=menu_kb())
        track_msg(uid, m)
        return

    # смена ника
    if st.get("step") == "edit_nick":
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s",
                       (msg.text.strip(), uid))

        await clear_all(uid)

        m = await msg.answer(f"✅ Теперь ты: {msg.text.strip()}")
        track_msg(uid, m)
        return

# ================= CREATE =================

@dp.callback_query_handler(Text(equals="create"))
async def create(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (uid,))
    if cursor.fetchone()[0] >= 2:
        return await c.message.answer("⚠️ У тебя уже 2 записи")

    user_states[uid] = {"step": "type", "msgs": []}

    m = await c.message.answer("❓ Что делаем?", reply_markup=type_kb())
    track_msg(uid, m)

# TYPE
@dp.callback_query_handler(Text(startswith="type_"))
async def type_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    user_states[uid]["type"] = "🏗 Строим" if "build" in c.data else "🔬 Исследуем"

    m = await c.message.answer("📅 Выбери диапазон:", reply_markup=range_kb())
    track_msg(uid, m)

# RANGE
@dp.callback_query_handler(Text(startswith="r_"))
async def range_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    start = int(c.data.split("_")[1])

    m = await c.message.answer("📆 Выбери день:", reply_markup=days_kb(start))
    track_msg(uid, m)

# DAY
@dp.callback_query_handler(Text(startswith="d_"))
async def day_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    user_states[uid]["days"] = int(c.data.split("_")[1])

    m = await c.message.answer("⏳ Выбери часы:", reply_markup=hours_kb())
    track_msg(uid, m)

# HOURS (ФИНАЛ — теперь стабилен)
@dp.callback_query_handler(Text(startswith="h_"))
async def hours_handler(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    hours = int(c.data.split("_")[1])
    total = st["days"] * 24 + hours

    nick = await get_nick(uid)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours)
    VALUES (%s,%s,%s,%s)
    """, (uid, nick, st["type"], total))

    await clear_all(uid)

    m = await c.message.answer("🎉 Запись создана! Ты красавчик!")
    track_msg(uid, m)

    user_states.pop(uid, None)

# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp)
