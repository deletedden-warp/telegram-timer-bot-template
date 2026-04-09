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
last_rating_message_id = None
last_boost_stat_message_id = None


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

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS boost_stats (
            user_id BIGINT PRIMARY KEY,
            boost_count INT DEFAULT 0
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
    confirm_user_delete = State()


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📊 Рейтинг"), KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="⚡ Буст")],
            [KeyboardButton(text="📈 Статистика бустов")],
            [KeyboardButton(text="🗑 Удалить запись"), KeyboardButton(text="❌ Удалиться из базы")]
        ],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end):
    return max(0, int((end - datetime.utcnow()).total_seconds()))


def days_left(end):
    return seconds_left(end) // 86400


def icon(t):
    return "🏗" if "Стро" in t else "🔬"


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


async def delete_message_later(chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass


# ================= RATING =================

async def send_rating():
    global last_rating_message_id

    tasks = await get_tasks()

    build = sorted(
        [t for t in tasks if "Стро" in t["action_type"]],
        key=lambda x: days_left(x["end_time"]),
        reverse=True
    )

    research = sorted(
        [t for t in tasks if "Исслед" in t["action_type"]],
        key=lambda x: days_left(x["end_time"]),
        reverse=True
    )

    text = "📊 Рейтинг\n\n"

    text += "🏗 Стройка\n"
    for t in build:
        text += f"{t['nickname']} 🏗 — {days_left(t['end_time'])} д\n"

    text += "\n🔬 Исследования\n"
    for t in research:
        text += f"{t['nickname']} 🔬 — {days_left(t['end_time'])} д\n"

    try:
        if last_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_rating_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_rating_message_id = msg.message_id


# ================= BOOST STATS =================

async def send_boost_stats():
    global last_boost_stat_message_id

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT u.nickname, b.boost_count
        FROM boost_stats b
        JOIN users u ON u.tg_id=b.user_id
        ORDER BY b.boost_count DESC
        """)

    if not rows:
        return

    text = "🚀 Статистика ускорений\n\n"

    for i, r in enumerate(rows, start=1):
        text += f"{i}) {r['nickname']} ускорил союзников {r['boost_count']} раз ⚡\n"

    try:
        if last_boost_stat_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_boost_stat_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_boost_stat_message_id = msg.message_id


async def boost_stats_loop():
    while True:
        await send_boost_stats()
        await asyncio.sleep(14400)


@dp.message(F.text == "📈 Статистика бустов")
async def boost_stats_pm(message: Message):

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT u.nickname, b.boost_count
        FROM boost_stats b
        JOIN users u ON u.tg_id=b.user_id
        ORDER BY b.boost_count DESC
        """)

    if not rows:
        await message.answer("Пока никто не делал бусты")
        return

    text = "🚀 Статистика ускорений\n\n"

    for i, r in enumerate(rows, start=1):
        text += f"{i}) {r['nickname']} ускорил союзников {r['boost_count']} раз ⚡\n"

    await message.answer(text)


# ================= BOOST =================

@dp.message(F.text == "⚡ Буст")
async def boost_start(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Стройка")],
            [KeyboardButton(text="🔬 Исследования")]
        ], resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.boost_type)


@dp.message(Form.boost_type)
async def boost_type(message: Message, state: FSMContext):

    tasks = await get_tasks()

    filtered = [
        t for t in tasks
        if t["user_id"] != message.from_user.id and (
            ("Стро" in message.text and "Стро" in t["action_type"]) or
            ("Исслед" in message.text and "Исслед" in t["action_type"])
        )
    ]

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=f"ID {t['id']} {t['nickname']}")] for t in filtered],
        resize_keyboard=True
    )

    await state.update_data(filtered_tasks=filtered)
    await message.answer("Выбери цель", reply_markup=kb)
    await state.set_state(Form.boost_target)


@dp.message(Form.boost_target)
async def boost_target(message: Message, state: FSMContext):

    data = await state.get_data()

    for t in data['filtered_tasks']:
        if f"ID {t['id']}" in message.text:
            await state.update_data(target=t)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Уровень 1: 5%")],
            [KeyboardButton(text="Уровень 2: 10%")],
            [KeyboardButton(text="Уровень 3: 15%")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

    await message.answer("Выбери уровень", reply_markup=kb)
    await state.set_state(Form.boost_percent)


@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):

    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Меню", reply_markup=main_menu())
        return

    percent_map = {
        "Уровень 1: 5%": 0.05,
        "Уровень 2: 10%": 0.10,
        "Уровень 3: 15%": 0.15
    }

    if message.text not in percent_map:
        await message.answer("Выбери кнопку")
        return

    percent = percent_map[message.text]
    data = await state.get_data()
    target = data['target']

    async with pool.acquire() as conn:
        async with conn.transaction():

            target_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id=$1 FOR UPDATE",
                target['id']
            )

            left = seconds_left(target_task['end_time'])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))

            await conn.execute(
                "UPDATE tasks SET end_time=$1 WHERE id=$2",
                new_time, target_task['id']
            )

            await conn.execute("""
            INSERT INTO boost_stats (user_id, boost_count)
            VALUES ($1,1)
            ON CONFLICT (user_id)
            DO UPDATE SET boost_count = boost_stats.boost_count + 1
            """, message.from_user.id)

            user = await conn.fetchrow(
                "SELECT nickname FROM users WHERE tg_id=$1",
                message.from_user.id
            )

            target_user = await conn.fetchrow(
                "SELECT nickname FROM users WHERE tg_id=$1",
                target_task['user_id']
            )

    if "Стро" in target_task['action_type']:
        text = f"🔥 {user['nickname']} ускорил стройку для {target_user['nickname']} на {int(percent*100)}%"
    else:
        text = f"🔥 {user['nickname']} ускорил исследование для {target_user['nickname']} на {int(percent*100)}%"

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)

    asyncio.create_task(delete_message_later(GROUP_CHAT_ID, msg.message_id, 43200))

    await message.answer("Буст выполнен ✅")
    await send_rating()
    await send_boost_stats()

    await state.clear()


# ================= RUN =================

async def rating_loop():
    while True:
        await send_rating()
        await asyncio.sleep(14400)


async def main():
    await init_db()

    asyncio.create_task(rating_loop())
    asyncio.create_task(boost_stats_loop())
    asyncio.create_task(cleanup_tasks())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
