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
last_boost_stats_message_id = None


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
            user_id BIGINT PRIMARY KEY
        );
        """)

        # Миграция если таблица уже существует
        await conn.execute("""
        ALTER TABLE boost_stats
        ADD COLUMN IF NOT EXISTS boosts INTEGER DEFAULT 0
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


async def delete_message_later(chat_id, message_id, delay=43200):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass


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


# ================= CLEANUP =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)


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
    global last_boost_stats_message_id

    async with pool.acquire() as conn:
        stats = await conn.fetch("""
        SELECT u.nickname, b.boosts
        FROM boost_stats b
        JOIN users u ON u.tg_id=b.user_id
        ORDER BY b.boosts DESC
        """)

    if not stats:
        return

    text = "⚡ Статистика бустов\n\n"

    for i, s in enumerate(stats, start=1):
        text += f"{i}) 🚀 {s['nickname']} ускорил союзников {s['boosts']} раз\n"

    try:
        if last_boost_stats_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_boost_stats_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_boost_stats_message_id = msg.message_id


async def rating_loop():
    while True:
        await send_rating()
        await send_boost_stats()
        await asyncio.sleep(14400)


# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(cleanup_tasks())
    asyncio.create_task(rating_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
