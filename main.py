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

def menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Создать", callback_data="menu_create"),
        InlineKeyboardButton("📋 Мои записи", callback_data="menu_my"),
    )
    kb.add(
        InlineKeyboardButton("❌ Удалить все", callback_data="menu_del"),
        InlineKeyboardButton("✏️ Изменить ник", callback_data="menu_nick"),
    )
    return kb

def type_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("🏗 Строим", callback_data="build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="research")
    )

def range_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("1-30", callback_data="r:1"),
        InlineKeyboardButton("31-60", callback_data="r:31"),
        InlineKeyboardButton("61-90", callback_data="r:61")
    )

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

async def delete_safe(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message, state: FSMContext):
    await state.finish()  # сброс зависших состояний
    await delete_safe(msg.chat.id, msg.message_id)

    await bot.send_message(
        msg.chat.id,
        "📋 Меню управления:",
        reply_markup=menu_kb()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("menu"))
async def menu_actions(c: CallbackQuery, state: FSMContext):
    await c.answer()

    if c.data == "menu_create":
        await create_cmd(c.message, state)

    elif c.data == "menu_my":
        await my(c.message)

    elif c.data == "menu_del":
        await del_all(c.message)

    elif c.data == "menu_nick":
        await edit_nick(c.message)

# ================= CREATE =================

@dp.message_handler(commands=["create"])
async def create_cmd(msg: types.Message, state: FSMContext):
    await delete_safe(msg.chat.id, msg.message_id)

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    if cursor.fetchone()[0] >= 2:
        return await msg.answer("У тебя уже 2 записи")

    nick = await get_nick(msg.from_user.id)
    if not nick:
        m = await msg.answer("Введи ник:")
        await state.update_data(msgs=[m.message_id])
        await SetNick.nick.set()
        return

    m = await msg.answer("Что делаем?", reply_markup=type_kb())
    await state.update_data(msgs=[m.message_id])
    await CreateTask.type.set()

# ================= EDIT NICK =================

async def edit_nick(msg: types.Message):
    await msg.answer("Введи новый ник:")
    await SetNick.nick.set()

@dp.message_handler(state=SetNick.nick)
async def save_nick(msg: types.Message, state: FSMContext):
    await delete_safe(msg.chat.id, msg.message_id)

    cursor.execute("""
    INSERT INTO users (user_id, nickname)
    VALUES (%s, %s)
    ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
    """, (msg.from_user.id, msg.text))

    await msg.answer("Ник обновлён ✅")
    await state.finish()

# ================= MY =================

async def my(msg: types.Message):
    cursor.execute("SELECT name,type,hours_left FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    rows = cursor.fetchall()

    if not rows:
        return await msg.answer("Нет записей")

    text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows])
    await msg.answer(text)

# ================= DELETE ALL =================

async def del_all(msg: types.Message):
    cursor.execute("SELECT chat_id,message_id FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))
    for chat_id, msg_id in cursor.fetchall():
        await delete_safe(chat_id, msg_id)

    cursor.execute("DELETE FROM tasks WHERE user_id=%s",
                   (msg.from_user.id,))

    await msg.answer("Удалено ✅")

# ================= FLOW =================

@dp.callback_query_handler(lambda c: c.data in ["build", "research"], state=CreateTask.type)
async def type_cb(c: CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()

    for m in data.get("msgs", []):
        await delete_safe(c.message.chat.id, m)

    t = "🏗 Строим" if c.data == "build" else "🔬 Исследуем"
    await state.update_data(type=t)

    m = await c.message.answer("Выбери диапазон:", reply_markup=range_kb())
    await state.update_data(msgs=[m.message_id])

    await CreateTask.days.set()

@dp.callback_query_handler(lambda c: c.data.startswith("r"), state=CreateTask.days)
async def range_cb(c: CallbackQuery):
    await c.answer()
    await c.message.edit_reply_markup(days_kb(int(c.data.split(":")[1])))

@dp.callback_query_handler(lambda c: c.data.startswith("d"), state=CreateTask.days)
async def days_cb(c: CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()

    for m in data.get("msgs", []):
        await delete_safe(c.message.chat.id, m)

    await state.update_data(days=int(c.data.split(":")[1]))

    m = await c.message.answer("Сколько часов?", reply_markup=hours_kb())
    await state.update_data(msgs=[m.message_id])

    await CreateTask.hours.set()

@dp.callback_query_handler(lambda c: c.data.startswith("h"), state=CreateTask.hours)
async def hours_cb(c: CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()

    for m in data.get("msgs", []):
        await delete_safe(c.message.chat.id, m)

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

    await state.finish()

# ================= UPDATE =================

async def update_tasks():
    now = datetime.utcnow()
    cursor.execute("SELECT * FROM tasks")

    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id = t

        if now >= delete_at:
            await delete_safe(chat_id, msg_id)
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
