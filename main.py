import os
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

import asyncpg

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set")

bot = Bot(token=BOT_TOKEN.strip())
dp = Dispatcher(storage=MemoryStorage())

GROUP_CHAT_ID = -1003672834247
TOPIC_ID = 5239

pool = None

# ================= DB =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id BIGINT PRIMARY KEY,
            nickname TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            action_type TEXT,
            end_time TIMESTAMP
        );
        """)

# ================= FSM =================

class Form(StatesGroup):
    nickname = State()
    action = State()
    days = State()
    boost_type = State()
    boost_target = State()
    boost_percent = State()
    delete_select = State()
    confirm_delete = State()

# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📊 Рейтинг"), KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="⚡ Буст")],
            [KeyboardButton(text="🗑 Удалить запись"), KeyboardButton(text="❌ Удалиться из базы")]
        ],
        resize_keyboard=True
    )

# ================= UTILS =================

def seconds_left(end):
    return max(0, int((end - datetime.utcnow()).total_seconds()))

def days_left(end):
    return seconds_left(end) // 86400

# ================= HELPERS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)

async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.*, u.nickname
        FROM tasks t JOIN users u ON u.tg_id=t.user_id
        """)

async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM tasks WHERE user_id=$1", tg_id)

# ================= TIMER =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)

# ================= RATING =================

async def send_rating():
    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    text = "📊 Рейтинг\n\n"

    text += "🏗 Стройка\n"
    for t in build:
        text += f"{t['nickname']} 🏗 — {days_left(t['end_time'])} д\n"

    text += "\n🔬 Исследования\n"
    for t in research:
        text += f"{t['nickname']} 🔬 — {days_left(t['end_time'])} д\n"

    # Удаляем последнее сообщение (если есть) и отправляем новое
    try:
        async with pool.acquire() as conn:
            msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    except Exception as e:
        logging.error(f"Ошибка отправки рейтинга: {e}")

async def rating_loop():
    while True:
        await send_rating()
        await asyncio.sleep(14400)

# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("Меню", reply_markup=main_menu())

@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, nickname) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            message.from_user.id, message.text
        )

    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()

# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.action)

@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)

@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи число")
        return

    data = await state.get_data()
    end = datetime.utcnow() + timedelta(days=int(message.text))

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            message.from_user.id, data["action"], end
        )

    await message.answer("Создано")
    await send_rating()
    await state.clear()

# ================= BOOST (FIXED RACE) =================

@dp.message(F.text == "⚡ Буст")
async def boost_start(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Стройка"), KeyboardButton(text="🔬 Исследования")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.boost_type)

@dp.message(Form.boost_type)
async def boost_type(message: Message, state: FSMContext):
    await state.update_data(boost_type=message.text)

    tasks = await get_tasks()

    filtered = [
        t for t in tasks
        if ("Стро" in message.text and "Стро" in t["action_type"]) or
           ("Исслед" in message.text and "Исслед" in t["action_type"])
    ]

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"ID {t['id']} | {t['nickname']} ({days_left(t['end_time'])} д)")]
            for t in filtered
        ] + [[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )

    await state.update_data(filtered_tasks=filtered)
    await message.answer("Выбери игрока", reply_markup=kb)
    await state.set_state(Form.boost_target)

@dp.message(Form.boost_target)
async def boost_target(message: Message, state: FSMContext):
    data = await state.get_data()
    tasks = data["filtered_tasks"]

    selected = None
    for t in tasks:
        if f"ID {t['id']}" in message.text:
            selected = t
            break

    await state.update_data(target_task=selected)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Уровень 1: 5%")],
            [KeyboardButton(text="Уровень 2: 10%")],
            [KeyboardButton(text="Уровень 3: 15%")]
        ],
        resize_keyboard=True
    )

    await message.answer("Выбери уровень", reply_markup=kb)
    await state.set_state(Form.boost_percent)

@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):
    percent_map = {
        "Уровень 1: 5%": 0.05,
        "Уровень 2: 10%": 0.10,
        "Уровень 3: 15%": 0.15
    }

    percent = percent_map.get(message.text)
    data = await state.get_data()
    target = data["target_task"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            target_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id=$1 FOR UPDATE",
                target["id"]
            )

            left = seconds_left(target_task["end_time"])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))

            await conn.execute("UPDATE tasks SET end_time=$1 WHERE id=$2", new_time, target_task["id"])

    await message.answer("Буст выполнен ✅")
    await send_rating()
    await state.clear()

# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(rating_loop())
    asyncio.create_task(cleanup_tasks())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
