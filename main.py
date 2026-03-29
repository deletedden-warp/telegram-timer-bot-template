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
            [KeyboardButton(text="🏆 Рейтинг")]
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


def format_time(seconds):
    return f"{max(0, seconds // 86400)} дн."


# ================= LIVE TABLE =================

async def build_table():
    tasks = await get_tasks()

    if not tasks:
        return "😶 Нет задач"

    text = "📊 Задачи:\n\n"
    for t in tasks:
        left = seconds_left(t["end_time"])
        text += f"{t['nickname']} | {t['action_type']} | {format_time(left)}\n"

    return text


async def send_live():
    text = await build_table()

    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT DISTINCT chat_id FROM users WHERE chat_id IS NOT NULL")

    for u in users:
        try:
            await bot.send_message(u["chat_id"], text)
        except:
            pass


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


# ================= RATING =================

async def build_rating():
    tasks = await get_tasks()

    build_map = {}
    research_map = {}

    for t in tasks:
        left = seconds_left(t["end_time"])
        days = left // 86400
        nick = t["nickname"]

        if t["action_type"] == "Строим":
            build_map[nick] = build_map.get(nick, 0) + days
        else:
            research_map[nick] = research_map.get(nick, 0) + days

    build_sorted = sorted(build_map.items(), key=lambda x: x[1], reverse=True)
    research_sorted = sorted(research_map.items(), key=lambda x: x[1], reverse=True)

    text = "🏆 Рейтинг игроков\n\n"

    text += "🏗 Стройка:\n"
    if not build_sorted:
        text += "— пусто\n"
    else:
        for i, (nick, days) in enumerate(build_sorted, 1):
            text += f"{i}) {nick} — {days} дн.\n"

    text += "\n🔬 Исследования:\n"
    if not research_sorted:
        text += "— пусто\n"
    else:
        for i, (nick, days) in enumerate(research_sorted, 1):
            text += f"{i}) {nick} — {days} дн.\n"

    return text


# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
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
@dp.callback_query(F.data == "create")
async def create_any(event, state: FSMContext):
    if isinstance(event, CallbackQuery):
        await event.message.answer("Строим или Исследуем?")
    else:
        await event.answer("Строим или Исследуем?")
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return

    data = await state.get_data()
    await add_task(message.from_user.id, data["action"], int(message.text))

    await message.answer("Создано")
    await send_live()
    await state.clear()


# ================= LIST =================

@dp.message(F.text == "📜 Посмотреть все записи")
@dp.callback_query(F.data == "list")
async def list_any(event):
    text = await build_table()
    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
    else:
        await event.answer(text)


# ================= BOOST =================

@dp.message(F.text == "⚡ Буст игрока")
@dp.callback_query(F.data == "boost")
async def boost_start(event):
    tasks = await get_tasks()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["nickname"], callback_data=f"bst_{t['id']}")]
            for t in tasks
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer("Выбери цель", reply_markup=kb)
    else:
        await event.answer("Выбери цель", reply_markup=kb)


@dp.callback_query(F.data.startswith("bst_"))
async def choose_percent(call: CallbackQuery):
    task_id = int(call.data.split("_")[1])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Уровень 1: 5%", callback_data=f"p5_{task_id}")],
        [InlineKeyboardButton(text="Уровень 2: 10%", callback_data=f"p10_{task_id}")],
        [InlineKeyboardButton(text="Уровень 3: 15%", callback_data=f"p15_{task_id}")]
    ])

    await call.message.answer("Выбери уровень", reply_markup=kb)


@dp.callback_query(F.data.startswith("p"))
async def apply_boost(call: CallbackQuery):
    percent = int(call.data.split("_")[0][1:]) / 100
    task_id = int(call.data.split("_")[1])

    tasks = await get_tasks()

    for t in tasks:
        if t["id"] == task_id:
            left = seconds_left(t["end_time"])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))
            await update_task(task_id, new_time)

    await call.message.answer("⚡ Буст применён")
    await send_live()


# ================= RATING =================

@dp.message(F.text == "🏆 Рейтинг")
@dp.callback_query(F.data == "rating")
async def rating_any(event):
    text = await build_rating()

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
    else:
        await event.answer(text)


# ================= RUN =================

async def main():
    await init_db()

    asyncio.create_task(timer_loop())
    asyncio.create_task(auto_live())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
