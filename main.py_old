import os
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
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


# ================= UTILS =================

async def delete_messages(messages):
    await asyncio.sleep(1)
    for msg in messages:
        try:
            await msg.delete()
        except:
            pass


async def timeout(state: FSMContext, messages):
    await asyncio.sleep(180)
    data = await state.get_data()
    if data.get("active"):
        await state.clear()
        await delete_messages(messages)


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

@dp.message(F.text == "/menu")
async def menu(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("⚔️ Кто ты воин? Представься")
        await state.set_state(Form.nickname)
        return

    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())


# ================= REG =================

@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.text)

    await message.answer(f"👋 Приветствую тебя \"{message.text}\"!")
    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())

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

    await state.update_data(messages=[msg], active=True)

    asyncio.create_task(timeout(state, [msg]))

    await state.set_state(Form.action)


@dp.callback_query(Form.action)
async def action(call: CallbackQuery, state: FSMContext):
    action = "Строим" if call.data == "build" else "Исследуем"

    data = await state.get_data()
    msgs = data["messages"]

    msg = await call.message.answer("⏳ Сколько осталось дней до завершения?")
    msgs.append(msg)

    await state.update_data(action=action, messages=msgs)
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❗ Введи число")
        return

    data = await state.get_data()
    msgs = data["messages"]

    action = data["action"]
    days = int(message.text)

    user = await get_user(message.from_user.id)

    await add_task(message.from_user.id, action, days)

    final = await message.answer(
        f"✅ Я записал. Ты молодец!\n\n"
        f"👤 {user['nickname']}\n"
        f"⚙️ {action}\n"
        f"⏳ {days} дней"
    )

    await delete_messages(msgs + [message])

    await state.clear()


# ================= DELETE TASKS =================

@dp.callback_query(F.data == "delete_tasks")
async def del_tasks(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="yes_del")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="no_del")]
    ])
    await call.message.answer("⚠️ Уверены?", reply_markup=kb)


@dp.callback_query(F.data == "yes_del")
async def yes(call: CallbackQuery):
    await delete_tasks(call.from_user.id)
    await call.message.answer("🧹 Удалено")


@dp.callback_query(F.data == "no_del")
async def no(call: CallbackQuery):
    await call.message.answer("🤨 Ну и нахрена ты меня тревожишь?.")


# ================= ALL TASKS =================

@dp.callback_query(F.data == "all_tasks")
async def all_tasks(call: CallbackQuery):
    tasks = await get_all_tasks()

    if not tasks:
        await call.message.answer("😶 ...а нету ничего, давай исправим это?")
        return

    text = "📋 Список:\n\n"
    for i, t in enumerate(tasks, 1):
        text += f"{i}) {t['nickname']} | {t['action_type']} | {t['days']} дней\n"

    await call.message.answer(text)


# ================= EDIT NICK =================

@dp.callback_query(F.data == "edit_nick")
async def edit(call: CallbackQuery, state: FSMContext):
    user = await get_user(call.from_user.id)

    await call.message.answer(
        f"🧾 Сейчас ник: \"{user['nickname']}\"\nНа какой меняем?"
    )

    await state.set_state(Form.edit_nick)


@dp.message(Form.edit_nick)
async def save_new(message: Message, state: FSMContext):
    await update_nick(message.from_user.id, message.text)

    await message.answer(
        f"✅ Теперь ты \"{message.text}\"",
        reply_markup=main_menu()
    )

    await state.clear()


# ================= DELETE USER =================

@dp.callback_query(F.data == "delete_user")
async def del_user(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Да", callback_data="yes_user")],
        [InlineKeyboardButton(text="🙏 Нет", callback_data="no_user")]
    ])
    await call.message.answer("😈 Уверен?", reply_markup=kb)


@dp.callback_query(F.data == "yes_user")
async def yes_user(call: CallbackQuery):
    await delete_user(call.from_user.id)
    await call.message.answer("🕳 Удалил. Кто ты?")


@dp.callback_query(F.data == "no_user")
async def no_user(call: CallbackQuery):
    await call.message.answer("😤 Вот и правильно")


# ================= RUN =================

async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
