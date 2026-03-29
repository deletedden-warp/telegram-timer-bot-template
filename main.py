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

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL не найден")

GROUP_CHAT_ID = -1003672834247
TOPIC_ID = 5239

bot = Bot(token=BOT_TOKEN.strip())
dp = Dispatcher(storage=MemoryStorage())

pool: asyncpg.Pool = None
last_rating_message_id = None


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


# ================= FSM =================

class Form(StatesGroup):
    nickname = State()
    action = State()
    days = State()
    boost_type = State()
    boost_target = State()
    boost_percent = State()
    delete_one = State()
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


def action_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def back_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end_time):
    return int((end_time - datetime.utcnow()).total_seconds())


def format_days(seconds):
    return max(0, seconds // 86400)


def icon(t):
    return "🏗" if "Стро" in t else "🔬"


# ================= DB =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id, nickname, chat_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (tg_id) DO UPDATE SET nickname=$2, chat_id=$3
        """, tg_id, nickname, chat_id)


async def delete_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)
        await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)


async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.*, u.nickname
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT * FROM tasks WHERE user_id=$1
        """, tg_id)


async def add_task(tg_id, action, days):
    end_time = datetime.utcnow() + timedelta(days=days)

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("""
        SELECT * FROM tasks WHERE user_id=$1 AND action_type=$2
        """, tg_id, action)

        if existing:
            return False

        await conn.execute("""
        INSERT INTO tasks (user_id, action_type, end_time)
        VALUES ($1,$2,$3)
        """, tg_id, action, end_time)

    return True


async def delete_task(task_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)


async def update_task(task_id, new_time):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tasks SET end_time=$1 WHERE id=$2", new_time, task_id)


# ================= RATING =================

async def send_rating():
    global last_rating_message_id

    tasks = await get_tasks()

    build = []
    research = []

    for t in tasks:
        days = format_days(seconds_left(t["end_time"]))
        if "Стро" in t["action_type"]:
            build.append((t, days))
        else:
            research.append((t, days))

    build.sort(key=lambda x: x[1], reverse=True)
    research.sort(key=lambda x: x[1], reverse=True)

    text = "📊 Рейтинг\n\n"

    text += "🏗 Стройка:\n"
    for i, (t, d) in enumerate(build, 1):
        text += f"{i}) {t['nickname']} — {d} дней\n"

    text += "\n🔬 Исследования:\n"
    for i, (t, d) in enumerate(research, 1):
        text += f"{i}) {t['nickname']} — {d} дней\n"

    try:
        if last_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_rating_message_id)
    except:
        pass

    msg = await bot.send_message(
        GROUP_CHAT_ID,
        text,
        message_thread_id=TOPIC_ID
    )

    last_rating_message_id = msg.message_id


async def rating_loop():
    while True:
        await send_rating()
        await asyncio.sleep(14400)


# ================= GLOBAL BACK =================

@dp.message(F.text == "🔙 Назад")
async def back_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Меню", reply_markup=main_menu())


# ================= MENU =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def menu(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("Меню", reply_markup=main_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text, message.chat.id)
    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    await message.answer("Выбери тип", reply_markup=action_menu())
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Меню", reply_markup=main_menu())
        return

    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Меню", reply_markup=main_menu())
        return

    if not message.text.isdigit():
        await message.answer("Введи число")
        return

    data = await state.get_data()

    success = await add_task(message.from_user.id, data["action"], int(message.text))

    if not success:
        await message.answer("Уже есть запись такого типа")
    else:
        await message.answer("Создано")
        await send_rating()

    await state.clear()


# ================= MY TASKS =================

@dp.message(F.text == "📋 Мои записи")
async def my_tasks(message: Message):
    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("Нет записей")
        return

    text = "📋 Твои записи:\n\n"

    for t in tasks:
        days = format_days(seconds_left(t["end_time"]))
        text += f"{icon(t['action_type'])} {t['action_type']} — {days} дней\n"

    await message.answer(text)


# ================= DELETE =================

@dp.message(F.text == "❌ Удалиться из базы")
async def delete_confirm(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]
        ],
        resize_keyboard=True
    )
    await message.answer("Точно удалить?", reply_markup=kb)
    await state.set_state(Form.confirm_delete)


@dp.message(Form.confirm_delete)
async def delete_apply(message: Message, state: FSMContext):
    if message.text == "✅ Да":
        await delete_user(message.from_user.id)
        await message.answer("Удалено", reply_markup=main_menu())
    else:
        await message.answer("Отмена", reply_markup=main_menu())

    await state.clear()


# ================= FALLBACK =================

@dp.message()
async def fallback(message: Message):
    await message.answer("Используй кнопки меню")
    

# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(rating_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
