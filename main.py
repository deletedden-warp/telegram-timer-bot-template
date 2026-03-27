import os
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

import asyncpg

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

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
            nickname TEXT
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
    edit_nick = State()


# ================= MESSAGE TRACKING =================

async def add_msg(state: FSMContext, msg):
    data = await state.get_data()
    msgs = data.get("messages", [])
    msgs.append(msg)
    await state.update_data(messages=msgs)


async def cleanup(state: FSMContext):
    data = await state.get_data()
    msgs = data.get("messages", [])

    for msg in msgs:
        try:
            await msg.delete()
        except:
            pass

    await state.update_data(messages=[])


# ================= DB FUNCS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM users WHERE tg_id=$1", tg_id
        )


async def create_user(tg_id, nickname):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, nickname) VALUES ($1, $2)",
            tg_id, nickname
        )


async def update_nick(tg_id, nickname):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET nickname=$1 WHERE tg_id=$2",
            nickname, tg_id
        )


async def delete_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def count_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE user_id=$1", tg_id
        )


async def add_task(tg_id, action, days):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, days) VALUES ($1,$2,$3)",
            tg_id, action, days
        )


async def delete_tasks(tg_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tasks WHERE user_id=$1", tg_id
        )


async def get_all_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT u.nickname, t.action_type, t.days
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


# ================= KEYBOARDS =================

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Создать запись", callback_data="create")],
        [InlineKeyboardButton(text="🗑 Удалить мои записи", callback_data="delete_tasks")],
        [InlineKeyboardButton(text="📜 Посмотреть все записи", callback_data="all_tasks")],
        [InlineKeyboardButton(text="✏️ Изменить никнейм", callback_data="edit_nick")],
        [InlineKeyboardButton(text="💀 Удалиться из базы", callback_data="delete_user")]
    ])


# ================= START =================

@dp.message(Command("menu"))
async def menu(message: Message, state: FSMContext):
    await cleanup(state)  # 💥 чистим старые сообщения

    user = await get_user(message.from_user.id)

    if not user:
        msg = await message.answer("⚔️ Кто ты воин? Представься")
        await add_msg(state, msg)
        await add_msg(state, message)

        await state.set_state(Form.nickname)
        return

    msg = await message.answer("🧠 Что будем делать?", reply_markup=main_menu())
    await add_msg(state, msg)


# ================= REG =================

@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text)

    await add_msg(state, message)

    msg1 = await message.answer(f"👋 Приветствую тебя \"{message.text}\"!")
    msg2 = await message.answer("🧠 Что будем делать?", reply_markup=main_menu())

    await add_msg(state, msg1)
    await add_msg(state, msg2)

    await state.clear()


# ================= CREATE =================

@dp.callback_query(F.data == "create")
async def create(call: CallbackQuery, state: FSMContext):
    if await count_tasks(call.from_user.id) >= 2:
        await call.message.answer("🚫 У тебя максимум записей (2)")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏗 Строим", callback_data="build"),
            InlineKeyboardButton(text="🔬 Исследуем", callback_data="research")
        ]
    ])

    msg = await call.message.answer("⚙️ Что делаем?", reply_markup=kb)
    await add_msg(state, msg)

    await state.set_state(Form.action)


@dp.callback_query(Form.action)
async def action(call: CallbackQuery, state: FSMContext):
    action = "Строим" if call.data == "build" else "Исследуем"

    msg = await call.message.answer("⏳ Сколько осталось дней до завершения?")
    await add_msg(state, msg)

    await state.update_data(action=action)
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❗ Введи число")
        return

    await add_msg(state, message)

    data = await state.get_data()

    action = data["action"]
    days = int(message.text)

    user = await get_user(message.from_user.id)

    await add_task(message.from_user.id, action, days)

    await cleanup(state)  # 💥 удаляем ВСЁ

    await message.answer(
        f"✅ Я записал. Ты молодец!\n\n"
        f"👤 {user['nickname']}\n"
        f"⚙️ {action}\n"
        f"⏳ {days} дней"
    )

    await state.clear()


# ================= RUN =================

async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
