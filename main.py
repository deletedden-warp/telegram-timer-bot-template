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

BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://user:password@localhost:5432/db"

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
            days INTEGER
        );
        """)


# ================= FSM =================

class Form(StatesGroup):
    nickname = State()
    action = State()
    days = State()


# ================= SCHEDULER =================

async def daily_sender():
    while True:
        now = datetime.utcnow() + timedelta(hours=3)

        if now.hour == 6 and now.minute == 0:
            async with pool.acquire() as conn:
                users = await conn.fetch("SELECT DISTINCT chat_id FROM users WHERE chat_id IS NOT NULL")

                tasks = await conn.fetch("""
                SELECT u.nickname, t.action_type, t.days
                FROM tasks t
                JOIN users u ON u.tg_id = t.user_id
                """)

                if not tasks:
                    text = "😶 Список пуст"
                else:
                    text = "📋 Ежедневный список:\n\n"
                    for i, t in enumerate(tasks, 1):
                        text += f"{i}) {t['nickname']} | {t['action_type']} | {t['days']} дней\n"

                for u in users:
                    try:
                        await bot.send_message(u["chat_id"], text)
                    except Exception as e:
                        logging.error(e)

            await asyncio.sleep(60)

        await asyncio.sleep(30)


# ================= DB FUNCS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, nickname, chat_id) VALUES ($1, $2, $3)",
            tg_id, nickname, chat_id
        )


async def update_chat(tg_id, chat_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET chat_id=$1 WHERE tg_id=$2",
            chat_id, tg_id
        )


async def count_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE user_id=$1", tg_id)


async def add_task(tg_id, action, days):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, days) VALUES ($1,$2,$3)",
            tg_id, action, days
        )


async def delete_tasks(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def get_all_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT u.nickname, t.action_type, t.days
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="🗑 Удалить мои записи")],
            [KeyboardButton(text="📜 Посмотреть все записи")]
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


# ================= START / MENU =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def menu(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("⚔️ Кто ты воин? Представься")
        await state.set_state(Form.nickname)
        return

    await update_chat(message.from_user.id, message.chat.id)

    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())


# ================= REG =================

@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text, message.chat.id)

    await message.answer(f"👋 Приветствую тебя \"{message.text}\"!")
    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())

    await state.clear()


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    if await count_tasks(message.from_user.id) >= 2:
        await message.answer("🚫 У тебя максимум записей (2)")
        return

    await message.answer("⚙️ Что делаем?", reply_markup=action_menu())
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text not in ["🏗 Строим", "🔬 Исследуем"]:
        return

    await state.update_data(action=message.text)
    await message.answer("⏳ Сколько осталось дней до завершения?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❗ Введи число")
        return

    data = await state.get_data()

    action = data["action"]
    days = int(message.text)

    user = await get_user(message.from_user.id)

    await add_task(message.from_user.id, action, days)

    await message.answer(
        f"📋 Список:\n\n"
        f"👤 {user['nickname']} | {action} | {days} дней"
    )

    await message.answer("🧠 Что дальше?", reply_markup=main_menu())
    await state.clear()


# ================= DELETE TASKS =================

@dp.message(F.text == "🗑 Удалить мои записи")
async def remove_tasks(message: Message):
    await delete_tasks(message.from_user.id)
    await message.answer("🗑 Все твои записи удалены")


# ================= ALL TASKS =================

@dp.message(F.text == "📜 Посмотреть все записи")
async def all_tasks(message: Message):
    tasks = await get_all_tasks()

    if not tasks:
        await message.answer("😶 ...а нету ничего")
        return

    text = "📋 Список:\n\n"
    for i, t in enumerate(tasks, 1):
        text += f"{i}) {t['nickname']} | {t['action_type']} | {t['days']} дней\n"

    await message.answer(text)


# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(daily_sender())
    logging.info("Bot started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
