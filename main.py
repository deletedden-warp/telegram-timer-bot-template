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

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

GROUP_CHAT_ID = -1003672834247
GROUP_THREAD_ID = 5239

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
    attack_type = State()
    attack_target = State()
    attack_percent = State()


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
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )


# ================= UTILS =================

def normalize_type(action: str):
    if "Строим" in action:
        return "build"
    if "Исследуем" in action:
        return "research"
    return "unknown"


def seconds_left(end_time):
    return int((end_time - datetime.utcnow()).total_seconds())


def format_days(seconds):
    return f"{max(0, seconds // 86400)} дней"


# ================= DB FUNCS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id, nickname, chat_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (tg_id) DO UPDATE SET nickname=$2
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
        SELECT t.id, u.nickname, t.action_type, t.end_time, t.user_id
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


async def delete_user_tasks(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def delete_user_full(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


# ================= REPORT =================

async def send_group_report():
    tasks = await get_tasks()

    if not tasks:
        text = "😶 Нет задач"
    else:
        text = "📊 LIVE таблица:\n\n"
        for t in tasks:
            left = seconds_left(t["end_time"])
            text += f"{t['nickname']} | {t['action_type']} | {format_days(left)}\n"

    try:
        await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=GROUP_THREAD_ID)
    except Exception as e:
        logging.error(e)


# ================= HANDLERS =================

@dp.message(F.text.in_({"/start", "/menu", "🏠 Главное меню"}))
async def menu(message: Message, state: FSMContext):
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


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    await message.answer("Выбери действие", reply_markup=action_menu())
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await message.answer("Меню", reply_markup=main_menu())
        return

    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return

    data = await state.get_data()
    await add_task(message.from_user.id, data["action"], int(message.text))

    await message.answer("Создано", reply_markup=main_menu())
    await state.clear()

    await send_group_report()


# ================= LIST =================

@dp.message(F.text == "📜 Посмотреть все записи")
async def list_tasks(message: Message):
    tasks = await get_tasks()

    text = ""
    for t in tasks:
        left = seconds_left(t["end_time"])
        text += f"{t['nickname']} | {t['action_type']} | {format_days(left)}\n"

    await message.answer(text or "Пусто")


# ================= RATING =================

@dp.message(F.text == "🏆 Рейтинг")
async def rating(message: Message):
    tasks = await get_tasks()

    build = []
    research = []

    for t in tasks:
        left = seconds_left(t["end_time"])
        days = left // 86400

        t_type = normalize_type(t["action_type"])

        if t_type == "build":
            build.append((t["nickname"], days))
        elif t_type == "research":
            research.append((t["nickname"], days))

    build.sort(key=lambda x: x[1], reverse=True)
    research.sort(key=lambda x: x[1], reverse=True)

    text = "🏆 Стройка:\n"
    for i, (n, d) in enumerate(build, 1):
        text += f"{i}) {n} — {d} дней\n"

    text += "\n🔬 Исследования:\n"
    for i, (n, d) in enumerate(research, 1):
        text += f"{i}) {n} — {d} дней\n"

    await message.answer(text)


# ================= BOOST =================

@dp.message(F.text == "⚡ Буст")
async def boost(message: Message):
    tasks = await get_tasks()

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{t['nickname']} / {t['action_type']}")]
            for t in tasks
        ],
        resize_keyboard=True
    )

    await message.answer("Выбери цель", reply_markup=kb)
    dp.current_state().set_state(Form.attack_target)


@dp.message(Form.attack_target)
async def boost_target(message: Message, state: FSMContext):
    target = message.text.split(" / ")[0]

    tasks = await get_tasks()

    for t in tasks:
        if t["nickname"] == target:

            if t["user_id"] == message.from_user.id:
                await message.answer("❌ Нельзя бустить себя")
                await state.clear()
                return

            percent = 0.15

            left = seconds_left(t["end_time"])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tasks SET end_time=$1 WHERE id=$2",
                    new_time, t["id"]
                )

    await message.answer("⚡ Буст применен")
    await state.clear()

    await send_group_report()


# ================= DELETE =================

@dp.message(F.text == "🗑 Удалить свои записи")
async def delete_tasks(message: Message):
    await delete_user_tasks(message.from_user.id)
    await message.answer("Удалено")


@dp.message(F.text == "💀 Удалиться из базы")
async def delete_user(message: Message):
    await delete_user_full(message.from_user.id)
    await message.answer("Удален")


# ================= RUN =================

async def main():
    await init_db()

    async def scheduler():
        while True:
            await asyncio.sleep(14400)
            await send_group_report()

    asyncio.create_task(scheduler())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
