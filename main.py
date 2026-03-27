import asyncio
import sqlite3
from datetime import datetime, timedelta
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# ================= DB =================

conn = sqlite3.connect("data.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    nickname TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    type TEXT,
    hours_left INTEGER,
    delete_at TEXT,
    chat_id INTEGER,
    message_id INTEGER
)
""")

conn.commit()

# ================= FSM =================

class CreateTask(StatesGroup):
    type = State()
    days = State()
    hours = State()

class SetNick(StatesGroup):
    nick = State()

# ================= UI =================

def main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Создать запись")
    kb.add("📋 Мои записи", "🌍 Все записи")
    kb.add("✏️ Изменить ник", "❌ Удалить все мои записи")
    return kb

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
    for i in range(start, start+30):
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

# ================= СТАРТ =================

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    if msg.chat.type == "private":
        return await msg.answer("Используй бота в группе")
    await msg.answer("Выбери:", reply_markup=main_kb())

# ================= НИК =================

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

@dp.message_handler(lambda m: m.text == "✏️ Изменить ник")
async def change_nick(msg: types.Message):
    await msg.answer("Введи ник:")
    await SetNick.nick.set()

@dp.message_handler(state=SetNick.nick)
async def save_nick(msg: types.Message, state: FSMContext):
    cursor.execute("REPLACE INTO users VALUES (?,?)",
                   (msg.from_user.id, msg.text))
    conn.commit()
    await msg.answer("Сохранено ✅")
    await state.finish()

# ================= СОЗДАНИЕ =================

@dp.message_handler(lambda m: m.text == "➕ Создать запись")
async def create(msg: types.Message):
    if msg.chat.type == "private":
        return await msg.answer("Только в группе")

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?",
                   (msg.from_user.id,))
    if cursor.fetchone()[0] >= 2:
        return await msg.answer("Максимум 2 записи")

    nick = await get_nick(msg.from_user.id)
    if not nick:
        await msg.answer("Сначала введи ник")
        return

    await msg.answer("Тип:", reply_markup=type_kb())
    await CreateTask.type.set()

# ================= CALLBACKS =================

@dp.callback_query_handler(state=CreateTask.type)
async def type_cb(c: CallbackQuery, state: FSMContext):
    t = "🏗 Строим" if c.data == "build" else "🔬 Исследуем"
    await state.update_data(type=t)
    await c.message.edit_text("Диапазон:", reply_markup=range_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("r"))
async def range_cb(c: CallbackQuery):
    start = int(c.data.split(":")[1])
    await c.message.edit_text("Дни:", reply_markup=days_kb(start))

@dp.callback_query_handler(lambda c: c.data.startswith("d"))
async def days_cb(c: CallbackQuery, state: FSMContext):
    await state.update_data(days=int(c.data.split(":")[1]))
    await c.message.edit_text("Часы:", reply_markup=hours_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("h"))
async def hours_cb(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    hours = int(c.data.split(":")[1])
    days = data["days"]

    total = days*24 + hours
    delete_at = datetime.utcnow() + timedelta(hours=48)

    nick = await get_nick(c.from_user.id)

    cursor.execute("""
    INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id)
    VALUES (?,?,?,?,?,?,?)
    """, (c.from_user.id, nick, data["type"], total,
          delete_at.isoformat(), c.message.chat.id, 0))

    tid = cursor.lastrowid
    conn.commit()

    msg = await c.message.answer(
        f"{nick} | {data['type']} | {days}д {hours}ч",
        reply_markup=del_kb(tid)
    )

    cursor.execute("UPDATE tasks SET message_id=? WHERE id=?",
                   (msg.message_id, tid))
    conn.commit()

    await c.message.delete()
    await state.finish()

# ================= УДАЛЕНИЕ =================

@dp.callback_query_handler(lambda c: c.data.startswith("del"))
async def del_task(c: CallbackQuery):
    tid = int(c.data.split(":")[1])

    cursor.execute("SELECT chat_id,message_id,user_id FROM tasks WHERE id=?", (tid,))
    t = cursor.fetchone()

    if not t or t[2] != c.from_user.id:
        return await c.answer("Не твоя", show_alert=True)

    await bot.delete_message(t[0], t[1])

    cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
    conn.commit()

# ================= СПИСКИ =================

@dp.message_handler(lambda m: m.text == "📋 Мои записи")
async def my(msg: types.Message):
    cursor.execute("SELECT name,type,hours_left FROM tasks WHERE user_id=?",
                   (msg.from_user.id,))
    rows = cursor.fetchall()

    if not rows:
        return await msg.answer("Нет записей")

    text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows])
    await msg.answer(text)

@dp.message_handler(lambda m: m.text == "🌍 Все записи")
async def all_tasks(msg: types.Message):
    cursor.execute("SELECT name,type,hours_left FROM tasks")
    rows = cursor.fetchall()

    text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows]) or "Пусто"
    await msg.answer(text)

# ================= УДАЛИТЬ ВСЁ =================

@dp.message_handler(lambda m: m.text == "❌ Удалить все мои записи")
async def del_all(msg: types.Message):
    cursor.execute("SELECT chat_id,message_id FROM tasks WHERE user_id=?",
                   (msg.from_user.id,))
    for chat_id, msg_id in cursor.fetchall():
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

    cursor.execute("DELETE FROM tasks WHERE user_id=?",
                   (msg.from_user.id,))
    conn.commit()

    await msg.answer("Удалено ✅")

# ================= ОБНОВЛЕНИЕ =================

async def update_tasks():
    now = datetime.utcnow()

    cursor.execute("SELECT * FROM tasks")
    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id = t
        delete_at = datetime.fromisoformat(delete_at)

        if now >= delete_at:
            try:
                await bot.delete_message(chat_id, msg_id)
            except:
                pass
            cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
            continue

        hours -= 4
        if hours < 0:
            hours = 0

        try:
            await bot.edit_message_text(
                f"{name} | {typ} | {hours//24}д {hours%24}ч",
                chat_id, msg_id,
                reply_markup=del_kb(tid)
            )
        except:
            pass

        cursor.execute("UPDATE tasks SET hours_left=? WHERE id=?",
                       (hours, tid))

    conn.commit()

# ================= СТАРТ =================

async def on_startup(dp):
    scheduler.add_job(update_tasks, "interval", hours=4)
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
