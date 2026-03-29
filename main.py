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

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN.strip())
dp = Dispatcher(storage=MemoryStorage())

GROUP_CHAT_ID = -1003672834247
TOPIC_ID = 5239

pool = None
last_rating_message_id = None


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
    delete_select = State()
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


def back_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end):
    return max(0, int((end - datetime.utcnow()).total_seconds()))


def days_left(end):
    return seconds_left(end) // 86400


def icon(t):
    return "🏗" if "Стро" in t else "🔬"


# ================= HELPERS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.*, u.nickname
        FROM tasks t JOIN users u ON u.tg_id=t.user_id
        """)


async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM tasks WHERE user_id=$1", tg_id)


# ================= TIMER CLEAN =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)


# ================= RATING =================

async def send_rating():
    global last_rating_message_id

    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    text = "📊 Рейтинг\n\n"

    text += "🏗 Стройка\n"
    for t in build:
        text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"

    text += "\n🔬 Исследования\n"
    for t in research:
        text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"

    try:
        if last_rating_message_id:
            await bot.edit_message_text(text, GROUP_CHAT_ID, last_rating_message_id)
            return
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_rating_message_id = msg.message_id


async def rating_loop():
    while True:
        await send_rating()
        await asyncio.sleep(14400)


# ================= GLOBAL BACK =================

@dp.message(F.text == "🔙 Назад")
async def back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Меню", reply_markup=main_menu())


# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("Введи ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("Меню", reply_markup=main_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, nickname) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            message.from_user.id, message.text
        )

    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()


# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи число")
        return

    data = await state.get_data()
    end = datetime.utcnow() + timedelta(days=int(message.text))

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            message.from_user.id, data["action"], end
        )

    await message.answer("Создано")
    await send_rating()
    await state.clear()


# ================= RATING PRIVATE =================

@dp.message(F.text == "📊 Рейтинг")
async def rating_private(message: Message):
    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    text = "📊 Рейтинг\n\n"

    text += "🏗 Стройка\n"
    for t in build:
        text += f"{t['nickname']} 🏗 — {days_left(t['end_time'])}\n"

    text += "\n🔬 Исследования\n"
    for t in research:
        text += f"{t['nickname']} 🔬 — {days_left(t['end_time'])}\n"

    await message.answer(text)


# ================= DELETE USER =================

@dp.message(F.text == "❌ Удалиться из базы")
async def delete_user_btn(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
        resize_keyboard=True
    )
    await message.answer("Точно?", reply_markup=kb)
    await state.set_state(Form.confirm_delete)


@dp.message(Form.confirm_delete)
async def delete_user_apply(message: Message, state: FSMContext):
    if message.text == "✅ Да":
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE user_id=$1", message.from_user.id)
            await conn.execute("DELETE FROM users WHERE tg_id=$1", message.from_user.id)

        await message.answer("Удалено. Введи ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("Отмена", reply_markup=main_menu())
    await state.clear()


# ================= DELETE TASK =================

@dp.message(F.text == "🗑 Удалить запись")
async def delete_task_menu(message: Message, state: FSMContext):
    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("Удалять нечего")
        return

    if len(tasks) == 1:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE id=$1", tasks[0]["id"])

        await message.answer("Запись удалена")
        await send_rating()
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{t['id']} | {t['action_type']}")]
            for t in tasks
        ] + [[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )

    await message.answer("Выбери запись", reply_markup=kb)
    await state.set_state(Form.delete_select)


@dp.message(Form.delete_select)
async def delete_task_apply(message: Message, state: FSMContext):
    task_id = int(message.text.split("|")[0])

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)

    await message.answer("Удалено", reply_markup=main_menu())
    await send_rating()
    await state.clear()


# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(rating_loop())
    asyncio.create_task(cleanup_tasks())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
