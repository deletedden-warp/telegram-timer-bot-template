import os
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

import asyncpg

logging.basicConfig(level=logging.INFO)

# ================= TOKEN =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

print("TOKEN RAW:", repr(BOT_TOKEN))

if BOT_TOKEN:
    BOT_TOKEN = BOT_TOKEN.strip().replace('"', '')

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise ValueError(f"❌ TOKEN BROKEN: {repr(BOT_TOKEN)}")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

pool: asyncpg.Pool = None


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


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📜 Все записи")],
        ],
        resize_keyboard=True
    )


def action_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")]
        ],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end_time):
    return int((end_time - datetime.utcnow()).total_seconds())


def format_days(seconds):
    days = max(0, seconds // 86400)
    return f"{days} дней"


# ================= DB FUNCS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (tg_id, nickname, chat_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (tg_id) DO UPDATE
            SET nickname = EXCLUDED.nickname,
                chat_id = EXCLUDED.chat_id
        """, tg_id, nickname, chat_id)


async def add_task(tg_id, action, days):
    end_time = datetime.utcnow() + timedelta(days=days)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            tg_id, action, end_time
        )


async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.id, u.nickname, t.action_type, t.end_time
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


# ================= HANDLERS =================

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

    await message.answer("✅ Ты зарегистрирован", reply_markup=main_menu())
    await state.clear()


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    await message.answer("Выбери действие", reply_markup=action_menu())
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text not in ["🏗 Строим", "🔬 Исследуем", "Строим", "Исследуем"]:
        await message.answer("Выбери кнопку")
        return

    action = "Строим" if "Строим" in message.text else "Исследуем"

    await state.update_data(action=action)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи число")
        return

    data = await state.get_data()

    await add_task(message.from_user.id, data["action"], int(message.text))

    await message.answer("✅ Запись создана")
    await state.clear()


# ================= LIST =================

@dp.message(F.text == "📜 Все записи")
async def list_tasks(message: Message):
    tasks = await get_tasks()

    if not tasks:
        await message.answer("😶 Нет записей")
        return

    text = "📋 Список:\n\n"
    for t in tasks:
        left = seconds_left(t["end_time"])
        text += f"{t['nickname']} | {t['action_type']} | {format_days(left)}\n"

    await message.answer(text)


# ================= FALLBACK =================

@dp.message()
async def fallback(message: Message):
    await message.answer("Не понял команду. Напиши /menu")


# ================= RUN =================

async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
