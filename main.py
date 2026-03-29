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

BOT_TOKEN = "YOUR_TOKEN"
DATABASE_URL = "YOUR_DB_URL"

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
            [KeyboardButton(text="📜 Все записи")],
            [KeyboardButton(text="⚔️ Сократить время игроку")]
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


def inline_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Создать", callback_data="create")],
        [InlineKeyboardButton(text="📜 Список", callback_data="list")],
        [InlineKeyboardButton(text="⚔️ Атака", callback_data="attack")]
    ])


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
        await conn.execute(
            "INSERT INTO users (tg_id, nickname, chat_id) VALUES ($1,$2,$3)",
            tg_id, nickname, chat_id
        )


async def update_chat(tg_id, chat_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET chat_id=$1 WHERE tg_id=$2",
            chat_id, tg_id
        )


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


async def update_task_time(task_id, new_time):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tasks SET end_time=$1 WHERE id=$2",
            new_time, task_id
        )


# ================= TIMER =================

async def timer_loop():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("""
            DELETE FROM tasks WHERE end_time <= NOW()
            """)
        await asyncio.sleep(60)


# ================= AUTO REPORT =================

async def auto_report():
    while True:
        await asyncio.sleep(7200)

        tasks = await get_tasks()

        if not tasks:
            text = "😶 Нет активных задач"
        else:
            text = "📊 Таблица:\n\n"
            for i, t in enumerate(tasks, 1):
                left = seconds_left(t["end_time"])
                text += f"{i}) {t['nickname']} | {t['action_type']} | {format_days(left)}\n"

        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT DISTINCT chat_id FROM users")

        for u in users:
            try:
                await bot.send_message(u["chat_id"], text)
            except:
                pass


# ================= HANDLERS =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def menu(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник")
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


@dp.callback_query(F.data == "create")
async def create_inline(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Напиши: Строим или Исследуем")
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    data = await state.get_data()
    await add_task(message.from_user.id, data["action"], int(message.text))

    await message.answer("Создано")
    await state.clear()


# ================= LIST =================

@dp.message(F.text == "📜 Все записи")
async def list_tasks(message: Message):
    tasks = await get_tasks()

    text = ""
    for t in tasks:
        left = seconds_left(t["end_time"])
        text += f"{t['nickname']} | {t['action_type']} | {format_days(left)}\n"

    await message.answer(text)


@dp.callback_query(F.data == "list")
async def list_inline(call: CallbackQuery):
    tasks = await get_tasks()

    text = ""
    for t in tasks:
        left = seconds_left(t["end_time"])
        text += f"{t['nickname']} | {t['action_type']} | {format_days(left)}\n"

    await call.message.answer(text)


# ================= ATTACK =================

@dp.message(F.text == "⚔️ Сократить время игроку")
async def attack(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")]
        ],
        resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.attack_type)


@dp.message(Form.attack_type)
async def attack_type(message: Message, state: FSMContext):
    await state.update_data(type=message.text)

    tasks = await get_tasks()
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t["nickname"])] for t in tasks],
        resize_keyboard=True
    )

    await message.answer("Выбери игрока", reply_markup=kb)
    await state.set_state(Form.attack_target)


@dp.message(Form.attack_target)
async def attack_target(message: Message, state: FSMContext):
    await state.update_data(target=message.text)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Уровень 1: 5%")],
            [KeyboardButton(text="Уровень 2: 10%")],
            [KeyboardButton(text="Уровень 3: 15%")]
        ],
        resize_keyboard=True
    )

    await message.answer("Выбери уровень", reply_markup=kb)
    await state.set_state(Form.attack_percent)


@dp.message(Form.attack_percent)
async def attack_apply(message: Message, state: FSMContext):
    data = await state.get_data()

    percent = 0.05 if "1" in message.text else 0.1 if "2" in message.text else 0.15

    tasks = await get_tasks()

    for t in tasks:
        if t["nickname"] == data["target"]:
            left = seconds_left(t["end_time"])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))
            await update_task_time(t["id"], new_time)

    await message.answer("Атака применена")
    await state.clear()


# ================= RUN =================

async def main():
    await init_db()

    asyncio.create_task(timer_loop())
    asyncio.create_task(auto_report())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
