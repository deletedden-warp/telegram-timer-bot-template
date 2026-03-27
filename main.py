import asyncio
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import psycopg2
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= CONFIG =================

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
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
    message_id BIGINT
)
""")

# ================= FSM =================

class CreateTask(StatesGroup):
    type = State()
    days = State()
    hours = State()

class SetNick(StatesGroup):
    nick = State()

# ================= UI =================

def type_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🏗 Строим", callback_data="build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="research")
    )
    return kb

def range_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("1-30", callback_data="r:1"),
        InlineKeyboardButton("31-60", callback_data="r:31"),
        InlineKeyboardButton("61-90", callback_data="r:61")
    )
    return kb

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"d:{i}"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"h:{i}"))
    return kb

def del_kb(tid):
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del:{tid}")
    )

# ================= HELPERS =================

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= СОЗДАНИЕ =================

@dp.message_handler(commands=["create"])
async def create_cmd(msg: types.Message):
    if msg.chat.type == "private":
        return await msg.answer("Используй бота в группе")

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    if cursor.fetchone()[0] >= 2:
        return await msg.answer("У тебя уже 2 записи")

    nick = await get_nick(msg.from_user.id)
    if not nick:
        await msg.answer("Введи свой ник:")
        await SetNick.nick.set()
        return

    await msg.answer("Что делаем?", reply_markup=type_kb())
    await CreateTask.type.set()

# ================= НИК =================

@dp.message_handler(state=SetNick.nick)
async def save_nick(msg: types.Message, state: FSMContext):
    cursor.execute("REPLACE INTO users VALUES (%s,%s)",
                   (msg.from_user.id, msg.text))

    await msg.answer("Ник сохранён ✅")

    await state.finish()  # ВАЖНЫЙ ФИКС

    await msg.answer("Что делаем?", reply_markup=type_kb())
    await CreateTask.type.set()

# ================= CALLBACKS =================

@dp.callback_query_handler(state=CreateTask.type)
async def type_cb(c: CallbackQuery, state: FSMContext):
    t = "🏗 Строим" if c.data == "build" else "🔬 Исследуем"
    await state.update_data(type=t)
    await c.message.edit_text("Выбери диапазон дней:", reply_markup=range_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("r"))
async def range_cb(c: CallbackQuery):
    start = int(c.data.split(":")[1])
    await c.message.edit_text("Сколько дней?", reply_markup=days_kb(start))

@dp.callback_query_handler(lambda c: c.data.startswith("d"))
async def days_cb(c: CallbackQuery, state: FSMContext):
    await state.update_data(days=int(c.data.split(":")[1]))
    await c.message.edit_text("Сколько часов?", reply_markup=hours_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("h"))
async def hours_cb(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    hours = int(c.data.split(":")[1])
    days = data["days"]

    total = days * 24 + hours
    delete_at = datetime.utcnow() + timedelta(hours=48)

    nick = await get_nick(c.from_user.id)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (c.from_user.id, nick, data["type"], total,
          delete_at, c.message.chat.id, 0))

    tid = cursor.fetchone()[0]

    msg = await c.message.answer(
        f"👤 {nick}\n📌 {data['type']}\n⏳ {days}д {hours}ч",
        reply_markup=del_kb(tid)
    )

    cursor.execute("UPDATE tasks SET message_id=%s WHERE id=%s",
                   (msg.message_id, tid))

    await c.message.delete()
    await state.finish()

# ================= УДАЛЕНИЕ =================

@dp.callback_query_handler(lambda c: c.data.startswith("del"))
async def del_task(c: CallbackQuery):
    tid = int(c.data.split(":")[1])

    cursor.execute("SELECT chat_id,message_id,user_id FROM tasks WHERE id=%s", (tid,))
    t = cursor.fetchone()

    if not t or t[2] != c.from_user.id:
        return await c.answer("Не твоя запись", show_alert=True)

    await bot.delete_message(t[0], t[1])
    cursor.execute("DELETE FROM tasks WHERE id=%s", (tid,))

# ================= СПИСКИ =================

@dp.message_handler(commands=["my"])
async def my(msg: types.Message):
    cursor.execute("SELECT name,type,hours_left FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    rows = cursor.fetchall()

    if not rows:
        return await msg.answer("У тебя нет записей")

    text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows])
    await msg.answer(text)

@dp.message_handler(commands=["all"])
async def all_tasks(msg: types.Message):
    cursor.execute("SELECT name,type,hours_left FROM tasks")
    rows = cursor.fetchall()

    text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows]) or "Нет записей"
    await msg.answer(text)

# ================= УДАЛИТЬ ВСЁ =================

@dp.message_handler(commands=["delete_all"])
async def del_all(msg: types.Message):
    cursor.execute("SELECT chat_id,message_id FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    for chat_id, msg_id in cursor.fetchall():
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

    cursor.execute("DELETE FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))

    await msg.answer("Все записи удалены ✅")

# ================= ОБНОВЛЕНИЕ =================

async def update_tasks():
    now = datetime.utcnow()

    cursor.execute("SELECT * FROM tasks")
    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id = t

        if now >= delete_at:
            try:
                await bot.delete_message(chat_id, msg_id)
            except:
                pass
            cursor.execute("DELETE FROM tasks WHERE id=%s", (tid,))
            continue

        hours -= 4
        if hours < 0:
            hours = 0

        try:
            await bot.edit_message_text(
                f"👤 {name}\n📌 {typ}\n⏳ {hours//24}д {hours%24}ч",
                chat_id, msg_id,
                reply_markup=del_kb(tid)
            )
        except:
            pass

        cursor.execute("UPDATE tasks SET hours_left=%s WHERE id=%s",
                       (hours, tid))

# ================= START =================

async def on_startup(dp):
    scheduler.add_job(update_tasks, "interval", hours=4)
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
