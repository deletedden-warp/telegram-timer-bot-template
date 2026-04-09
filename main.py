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


# ================= DELETE MESSAGE TIMER =================

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


async def rating_loop():
    while True:
        await send_rating()
        await asyncio.sleep(14400)


# ================= BOOST STATS MESSAGE =================

async def send_boost_stats():

    global last_boost_stats_message_id

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

    medals = ["🥇", "🥈", "🥉"]

    for i, r in enumerate(rows):
        if i < 3:
            emoji = medals[i]
        else:
            emoji = "⚡"

        text += f"{emoji} {r['nickname']} — {r['boost_count']} ускорений\n"

    try:
        if last_boost_stats_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_boost_stats_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_boost_stats_message_id = msg.message_id


# ================= BOOST =================

@dp.message(F.text == "⚡ Буст")
async def boost_start(message: Message, state: FSMContext):

    tasks = await get_tasks()

    filtered = [
        t for t in tasks
        if t["user_id"] != message.from_user.id
    ]

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=f"{t['nickname']}")] for t in filtered],
        resize_keyboard=True
    )

    await state.update_data(filtered_tasks=filtered)

    await message.answer("Кого ускоряем?", reply_markup=kb)
    await state.set_state(Form.boost_target)


@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):

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

    text = f"🔥 {user['nickname']} ускорил {target_user['nickname']} на {int(percent*100)}%"

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)

    asyncio.create_task(delete_message_later(GROUP_CHAT_ID, msg.message_id, 43200))

    await send_boost_stats()
    await send_rating()

    await message.answer("Буст выполнен ✅", reply_markup=main_menu())
    await state.clear()


# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(rating_loop())
    asyncio.create_task(cleanup_tasks())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
