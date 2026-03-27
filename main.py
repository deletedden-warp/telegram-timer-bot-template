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

# ================= HELPERS =================

async def delete_safe(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def auto_delete(chat_id, message_id, delay=60):
    await asyncio.sleep(delay)
    await delete_safe(chat_id, message_id)

async def safe_send(chat_id, text, reply_markup=None, thread_id=None):
    try:
        return await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            message_thread_id=thread_id
        )
    except:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

def progress_bar(percent):
    total = 10
    filled = int(percent / 10)
    return "█" * filled + "░" * (total - filled)

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Создать", callback_data="menu_create"),
        InlineKeyboardButton("📋 Мои записи", callback_data="menu_my"),
    )
    kb.add(
        InlineKeyboardButton("📊 Все записи", callback_data="menu_all"),
        InlineKeyboardButton("❌ Удалить все", callback_data="menu_del"),
    )
    kb.add(
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

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message, state: FSMContext):
    await state.finish()
    await delete_safe(msg.chat.id, msg.message_id)

    await safe_send(
        msg.chat.id,
        "📋 Меню:",
        reply_markup=menu_kb(),
        thread_id=msg.message_thread_id
    )

# ================= CREATE =================

@dp.message_handler(commands=["create"])
async def create_cmd(msg: types.Message, state: FSMContext):
    await state.finish()

    nick = await get_nick(msg.from_user.id)

    if not nick:
        m = await safe_send(msg.chat.id, "Введи ник:")
        asyncio.create_task(auto_delete(msg.chat.id, m.message_id))
        await state.update_data(creating=True)
        await SetNick.nick.set()
        return

    m = await safe_send(msg.chat.id, "Что делаем?", reply_markup=type_kb())
    await state.update_data(msgs=[m.message_id])
    await CreateTask.type.set()

# ================= NICK =================

@dp.message_handler(state=SetNick.nick)
async def save_nick(msg: types.Message, state: FSMContext):
    cursor.execute("""
    INSERT INTO users (user_id, nickname)
    VALUES (%s, %s)
    ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
    """, (msg.from_user.id, msg.text.strip()))

    m = await msg.answer("Ник сохранён ✅")
    asyncio.create_task(auto_delete(msg.chat.id, m.message_id))

    data = await state.get_data()

    if data.get("creating"):
        m = await msg.answer("Что делаем?", reply_markup=type_kb())
        await state.update_data(msgs=[m.message_id])
        await CreateTask.type.set()
    else:
        await state.finish()

# ================= FLOW =================

@dp.callback_query_handler(lambda c: c.data in ["build", "research"], state=CreateTask.type)
async def type_cb(c: CallbackQuery, state: FSMContext):
    t = "🏗 Строим" if c.data == "build" else "🔬 Исследуем"
    await state.update_data(type=t)

    m = await safe_send(c.message.chat.id, "Выбери диапазон:", range_kb())
    await state.update_data(msgs=[m.message_id])

    await CreateTask.days.set()

@dp.callback_query_handler(lambda c: c.data.startswith("d"), state=CreateTask.days)
async def days_cb(c: CallbackQuery, state: FSMContext):
    await state.update_data(days=int(c.data.split(":")[1]))

    m = await safe_send(c.message.chat.id, "Сколько часов?", hours_kb())
    await state.update_data(msgs=[m.message_id])

    await CreateTask.hours.set()

@dp.callback_query_handler(lambda c: c.data.startswith("h"), state=CreateTask.hours)
async def hours_cb(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    hours = int(c.data.split(":")[1])
    days = data["days"]
    total = days * 24 + hours

    nick = await get_nick(c.from_user.id)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (c.from_user.id, nick, data["type"], total,
          datetime.utcnow() + timedelta(hours=48),
          c.message.chat.id, 0))

    tid = cursor.fetchone()[0]

    msg = await safe_send(
        c.message.chat.id,
        f"👤 {nick}\n📌 {data['type']}\n⏳ {days}д {hours}ч",
        del_kb(tid)
    )

    cursor.execute("UPDATE tasks SET message_id=%s WHERE id=%s",
                   (msg.message_id, tid))

    await state.finish()

# ================= UPDATE =================

async def update_tasks():
    cursor.execute("SELECT * FROM tasks")

    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id = t

        hours = max(0, hours - 4)

        try:
            await bot.edit_message_text(
                f"👤 {name}\n📌 {typ}\n⏳ {hours//24}д {hours%24}ч",
                chat_id,
                msg_id,
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
