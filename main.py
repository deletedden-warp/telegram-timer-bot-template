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

BOT_TOKEN = os.getenv("BOT_TOKEN").strip().replace('"', '')
DATABASE_URL = os.getenv("DATABASE_URL")

# 🔥 ВАЖНО — твой чат
GROUP_CHAT_ID = -1003672834247

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
            [KeyboardButton(text="📜 Посмотреть все записи")],
            [KeyboardButton(text="⚡ Буст игрока")],
            [KeyboardButton(text="🏆 Рейтинг")],
            [KeyboardButton(text="🗑 Удалить свои записи")],
            [KeyboardButton(text="💀 Удалиться из базы")]
        ],
        resize_keyboard=True
    )


def action_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="🔙 Назад"), KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )


def back_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад"), KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )


def inline_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Создать", callback_data="create")],
        [InlineKeyboardButton(text="📜 Список", callback_data="list")],
        [InlineKeyboardButton(text="⚡ Буст", callback_data="boost")],
        [InlineKeyboardButton(text="🏆 Рейтинг", callback_data="rating")]
    ])


# ================= UTILS =================

def seconds_left(end_time):
    return int((end_time - datetime.utcnow()).total_seconds())


def format_days(seconds):
    return max(0, seconds // 86400)


def get_icon(action):
    return "🏗" if action == "Строим" else "🔬"


# ================= LIVE =================

async def build_table():
    tasks = await get_tasks()

    if not tasks:
        return "😶 Нет задач"

    text = "📊 Задачи:\n\n"
    for t in tasks:
        left = format_days(seconds_left(t["end_time"]))
        icon = get_icon(t["action_type"])
        text += f"{t['nickname']} | {icon} | {left} дн.\n"

    return text


async def send_live():
    text = await build_table()

    try:
        await bot.send_message(GROUP_CHAT_ID, text)
    except Exception as e:
        print("Ошибка отправки в группу:", e)


async def auto_live():
    while True:
        await asyncio.sleep(14400)
        await send_live()


# ================= DB =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id, nickname, chat_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (tg_id) DO UPDATE
        SET nickname=EXCLUDED.nickname,
            chat_id=EXCLUDED.chat_id
        """, tg_id, nickname, chat_id)


async def delete_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def delete_my_tasks(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


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
        SELECT t.id, u.nickname, t.action_type, t.end_time, t.user_id
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


async def update_task(task_id, new_time):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tasks SET end_time=$1 WHERE id=$2", new_time, task_id)


async def delete_finished():
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE end_time <= NOW()")


# ================= TIMER =================

async def timer_loop():
    while True:
        await delete_finished()
        await asyncio.sleep(60)


# ================= HANDLERS =================

@dp.message(F.text.in_({"/start", "/menu", "🏠 Главное меню"}))
async def menu(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник:")
        await state.set_state(Form.nickname)
        return

    if message.chat.type == "private":
        await message.answer("Меню", reply_markup=main_menu())
    else:
        await message.answer("Меню", reply_markup=inline_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text, message.chat.id)
    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    await message.answer("Выбери действие", reply_markup=action_menu())
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await menu(message, state)
        return

    await state.update_data(action=message.text)
    await message.answer("Сколько дней?", reply_markup=back_menu())
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await message.answer("Выбери действие", reply_markup=action_menu())
        await state.set_state(Form.action)
        return

    if not message.text.isdigit():
        return

    data = await state.get_data()
    await add_task(message.from_user.id, data["action"], int(message.text))

    await message.answer("Создано", reply_markup=main_menu())
    await send_live()
    await state.clear()


# ================= DELETE =================

@dp.message(F.text == "🗑 Удалить свои записи")
async def delete_tasks(message: Message):
    await delete_my_tasks(message.from_user.id)
    await message.answer("Все твои записи удалены")
    await send_live()


@dp.message(F.text == "💀 Удалиться из базы")
async def del_user(message: Message):
    await delete_user(message.from_user.id)
    await message.answer("Удалён из базы")
    await send_live()


# ================= RUN =================

async def main():
    await init_db()

    asyncio.create_task(timer_loop())
    asyncio.create_task(auto_live())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
