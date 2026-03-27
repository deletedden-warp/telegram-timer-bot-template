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

def clear_msgs(uid):
    if uid in user_states:
        for m in user_states[uid].get("msgs", []):
            asyncio.create_task(delete_safe(m["chat"], m["id"]))
        user_states[uid]["msgs"] = []

def save_msg(uid, msg):
    user_states.setdefault(uid, {}).setdefault("msgs", []).append({
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
    return kb

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

    nick = await get_nick(uid)

    if not nick:
        user_states[uid] = {"step": "nick"}
        m = await msg.answer("⚔️ Кто ты воин? Введи ник:")
        save_msg(uid, m)
        return

    m = await msg.answer("📋 Меню:", reply_markup=menu_kb())
    save_msg(uid, m)

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    if st.get("step") == "nick":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        clear_msgs(uid)

        m = await msg.answer("✅ Готово!", reply_markup=menu_kb())
        save_msg(uid, m)
        return

# ================= CREATE =================

@dp.callback_query_handler(Text(equals="create"))
async def create(c: CallbackQuery):
    uid = c.from_user.id

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (uid,))
    if cursor.fetchone()[0] >= 2:
        return await c.message.answer("⚠️ У тебя уже 2 записи")

    clear_msgs(uid)

    user_states[uid] = {}

    m = await c.message.answer("❓ Что делаем?", reply_markup=type_kb())
    save_msg(uid, m)

# ================= TYPE =================

@dp.callback_query_handler(Text(startswith="type_"))
async def type_handler(c: CallbackQuery):
    uid = c.from_user.id

    user_states[uid]["type"] = "🏗 Строим" if "build" in c.data else "🔬 Исследуем"

    clear_msgs(uid)

    m = await c.message.answer("📅 Выбери диапазон:", reply_markup=range_kb())
    save_msg(uid, m)

# ================= RANGE =================

@dp.callback_query_handler(Text(startswith="r_"))
async def range_handler(c: CallbackQuery):
    uid = c.from_user.id

    start = int(c.data.split("_")[1])

    clear_msgs(uid)

    m = await c.message.answer("📆 Выбери день:", reply_markup=days_kb(start))
    save_msg(uid, m)

# ================= DAY =================

@dp.callback_query_handler(Text(startswith="d_"))
async def day_handler(c: CallbackQuery):
    uid = c.from_user.id

    user_states[uid]["days"] = int(c.data.split("_")[1])

    clear_msgs(uid)

    m = await c.message.answer("⏳ Выбери часы:", reply_markup=hours_kb())
    save_msg(uid, m)

# ================= HOURS =================

@dp.callback_query_handler(Text(startswith="h_"))
async def hours_handler(c: CallbackQuery):
    uid = c.from_user.id
    st = user_states.get(uid)

    if not st:
        return await c.answer("Ошибка", show_alert=True)

    hours = int(c.data.split("_")[1])
    total = st["days"] * 24 + hours

    nick = await get_nick(uid)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours)
    VALUES (%s,%s,%s,%s)
    """, (uid, nick, st["type"], total))

    clear_msgs(uid)

    await c.message.answer("🎉 Запись создана!")

    user_states.pop(uid, None)

# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp)
