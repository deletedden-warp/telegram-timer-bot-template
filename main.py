import os
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

import asyncpg

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

GROUP_CHAT_ID = -1003672834247
THREAD_ID = 5239

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

pool: asyncpg.Pool = None


# ================= FSM =================

class Form(StatesGroup):
    nickname = State()
    action = State()
    days = State()
    boost_type = State()
    boost_target = State()
    boost_percent = State()


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📜 Посмотреть все записи")],
            [KeyboardButton(text="🏆 Рейтинг")],
            [KeyboardButton(text="⚡ Буст")],
            [KeyboardButton(text="🗑 Удалить свои записи")],
            [KeyboardButton(text="💀 Удалиться из базы")]
        ],
        resize_keyboard=True
    )


def action_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end_time):
    return max(0, int((end_time - datetime.utcnow()).total_seconds()))


def format_days(seconds):
    return max(0, seconds // 86400)


def icon(t):
    return "🏗" if "Строим" in t else "🔬"


# ================= DB =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id BIGINT PRIMARY KEY,
            nickname TEXT,
            chat_id BIGINT
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


# ================= DB FUNCS =================

async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id, nickname, chat_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (tg_id) DO UPDATE
        SET nickname=$2, chat_id=$3
        """, tg_id, nickname, chat_id)


async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def add_task(tg_id, action, days):
    end = datetime.utcnow() + timedelta(days=days)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            tg_id, action, end
        )


async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.id, u.nickname, t.action_type, t.end_time, t.user_id
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


async def update_task_time(task_id, new_time):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tasks SET end_time=$1 WHERE id=$2",
            new_time, task_id
        )


async def delete_tasks_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def delete_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)
        result = await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)
        return result


# ================= TIMER =================

async def timer_loop():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time <= NOW()")
        await asyncio.sleep(60)


# ================= LIFE TABLE =================

async def send_life_table():
    tasks = await get_tasks()

    if not tasks:
        text = "😶 Нет активных задач"
    else:
        text = "📊 Таблица:\n\n"
        for t in tasks:
            left = format_days(seconds_left(t["end_time"]))
            text += f"{t['nickname']} | {icon(t['action_type'])} | {left} дней\n"

    await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=THREAD_ID)


async def auto_report():
    while True:
        await asyncio.sleep(14400)
        await send_life_table()


# ================= HANDLERS =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник")
        await state.set_state(Form.nickname)
        return

    await message.answer("Меню", reply_markup=main_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text, message.chat.id)
    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()


# ================= DELETE USER FIX =================

@dp.message(F.text == "💀 Удалиться из базы")
async def delete_me(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("⚠️ Ты уже удалён")
        return

    await delete_user(message.from_user.id)

    await message.answer("💀 Ты полностью удалён из базы")

    await send_life_table()

    await state.clear()


# ================= RUN =================

async def main():
    await init_db()

    asyncio.create_task(timer_loop())
    asyncio.create_task(auto_report())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
