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

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set")

bot = Bot(token=BOT_TOKEN.strip())
dp = Dispatcher(storage=MemoryStorage())

GROUP_CHAT_ID = -1003672834247
TOPIC_ID = 5239

pool = None
last_rating_message_id = None
last_boost_rating_message_id = None


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

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS boost_stats (
            user_id BIGINT PRIMARY KEY,
            boosts INTEGER DEFAULT 0
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
    confirm_user_delete = State()


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📋 Список заявок"), KeyboardButton(text="🏆 Рейтинг бустов")],
            [KeyboardButton(text="📂 Мои записи")],
            [KeyboardButton(text="⚡ Буст")],
            [KeyboardButton(text="🗑 Удалить запись"), KeyboardButton(text="❌ Удалиться из базы")]
        ],
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
        FROM tasks t
        JOIN users u ON u.tg_id=t.user_id
        """)

async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.*, u.nickname
        FROM tasks t
        JOIN users u ON u.tg_id=t.user_id
        WHERE user_id=$1
        """)

async def get_boost_rating():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT u.nickname, b.boosts
        FROM boost_stats b
        JOIN users u ON u.tg_id=b.user_id
        ORDER BY b.boosts DESC
        """)


# ================= TIMER =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)


async def delete_later(chat_id, msg_id, delay):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg_id)
    except:
        pass


# ================= BUILD TEXT =================

async def build_rating_text():

    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    build.sort(key=lambda x: days_left(x["end_time"]), reverse=True)
    research.sort(key=lambda x: days_left(x["end_time"]), reverse=True)

    text = "📋 Список заявок\n\n"

    text += "🏗 Стройка\n"
    for t in build:
        text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"

    text += "\n🔬 Исследования\n"
    for t in research:
        text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"

    return text


async def build_boost_rating_text():

    stats = await get_boost_rating()

    text = "🏆 Топ бустеров\n\n"

    for i, s in enumerate(stats, start=1):
        text += f"{i}) {s['nickname']} — {s['boosts']} бустов\n"

    if not stats:
        text += "Пока нет бустов"

    return text


# ================= RATING =================

async def send_rating():

    global last_rating_message_id

    text = await build_rating_text()

    try:
        if last_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_rating_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_rating_message_id = msg.message_id


async def send_boost_rating():

    global last_boost_rating_message_id

    text = await build_boost_rating_text()

    try:
        if last_boost_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_boost_rating_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)
    last_boost_rating_message_id = msg.message_id


async def rating_loop():
    while True:
        await send_rating()
        await send_boost_rating()
        await asyncio.sleep(14400)


# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):

    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("👤 Введите ваш ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("🏠 Главное меню", reply_markup=main_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id,nickname) VALUES ($1,$2)",
            message.from_user.id,
            message.text
        )

    await message.answer("✅ Регистрация завершена", reply_markup=main_menu())
    await state.clear()


# ================= CREATE =================
# (эта часть не менялась)

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Стройка")],
            [KeyboardButton(text="🔬 Исследования")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

    await message.answer("⚙ Выберите тип:", reply_markup=kb)
    await state.set_state(Form.action)


@dp.message(Form.action)
async def action(message: Message, state: FSMContext):

    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 Главное меню", reply_markup=main_menu())
        return

    await state.update_data(action=message.text)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )

    await message.answer("📅 Сколько дней?", reply_markup=kb)
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):

    if message.text == "🔙 Назад":
        await state.set_state(Form.action)
        return

    if not message.text.isdigit():
        await message.answer("❗ Введите число")
        return

    data = await state.get_data()

    end = datetime.utcnow() + timedelta(days=int(message.text))

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id,action_type,end_time) VALUES ($1,$2,$3)",
            message.from_user.id,
            data["action"],
            end
        )

    await message.answer("✅ Запись создана", reply_markup=main_menu())
    await send_rating()
    await state.clear()


# ================= MENU BUTTONS =================

@dp.message(F.text == "📋 Список заявок")
async def show_rating(message: Message):

    text = await build_rating_text()

    await message.answer(text)


@dp.message(F.text == "🏆 Рейтинг бустов")
async def show_boost_rating(message: Message):

    text = await build_boost_rating_text()

    await message.answer(text)


# ================= RUN =================

async def main():

    await init_db()

    asyncio.create_task(rating_loop())
    asyncio.create_task(cleanup_tasks())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
