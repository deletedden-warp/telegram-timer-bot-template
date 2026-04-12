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

last_rating_text = None
last_boost_text = None


# ================= DATABASE =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id BIGINT PRIMARY KEY,
            nickname TEXT
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks(
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            action_type TEXT,
            end_time TIMESTAMP
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS boosts(
            id SERIAL PRIMARY KEY,
            booster_id BIGINT,
            target_id BIGINT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """)


# ================= FSM =================

class Form(StatesGroup):

    nickname = State()

    action = State()
    days = State()

    boost_type = State()
    boost_target = State()
    boost_percent = State()

    delete_menu = State()


# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="📜 Список заявок")],
            [KeyboardButton(text="⚡ Буст"), KeyboardButton(text="🏆 Рейтинг бустов")],
            [KeyboardButton(text="🗑 Удалить запись")],
            [KeyboardButton(text="❌ Удалиться из базы")]
        ],
        resize_keyboard=True
    )


def back_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )


# ================= HELPERS =================

def seconds_left(end):
    return max(0, int((end - datetime.utcnow()).total_seconds()))


def days_left(end):
    return seconds_left(end) // 86400


def icon(t):
    return "🏗" if "Стро" in t else "🔬"


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
        return await conn.fetch("SELECT * FROM tasks WHERE user_id=$1", tg_id)


# ================= CLEANUP =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)


# ================= RATING =================

async def generate_rating_text():

    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    build.sort(key=lambda x: days_left(x["end_time"]), reverse=True)
    research.sort(key=lambda x: days_left(x["end_time"]), reverse=True)

    text = "📜 Список заявок\n\n"

    text += "🏗 Стройка\n"
    if build:
        for t in build:
            text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"
    else:
        text += "нет записей\n"

    text += "\n🔬 Исследования\n"
    if research:
        for t in research:
            text += f"{t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} д\n"
    else:
        text += "нет записей\n"

    return text


async def generate_boost_rating():

    async with pool.acquire() as conn:

        rows = await conn.fetch("""
        SELECT u.nickname, COUNT(b.id) as boosts
        FROM boosts b
        JOIN users u ON u.tg_id=b.booster_id
        GROUP BY u.nickname
        ORDER BY boosts DESC
        """)

    text = "🏆 Топ бустеров\n\n"

    if not rows:
        text += "😴 пока нет бустов"
        return text

    medals = ["🥇", "🥈", "🥉"]

    for i, r in enumerate(rows, start=1):
        medal = medals[i - 1] if i <= 3 else "🔹"
        text += f"{medal} {i}) {r['nickname']} — ⚡ {r['boosts']} бустов\n"

    return text


# ================= UPDATE GROUP =================

async def update_group_messages():

    global last_rating_message_id
    global last_boost_rating_message_id
    global last_rating_text
    global last_boost_text

    rating_text = await generate_rating_text()
    boost_text = await generate_boost_rating()

    if rating_text != last_rating_text:

        try:
            if last_rating_message_id:
                await bot.delete_message(GROUP_CHAT_ID, last_rating_message_id)
        except:
            pass

        msg = await bot.send_message(
            GROUP_CHAT_ID,
            rating_text,
            message_thread_id=TOPIC_ID
        )

        last_rating_message_id = msg.message_id
        last_rating_text = rating_text

    if boost_text != last_boost_text:

        try:
            if last_boost_rating_message_id:
                await bot.delete_message(GROUP_CHAT_ID, last_boost_rating_message_id)
        except:
            pass

        msg2 = await bot.send_message(
            GROUP_CHAT_ID,
            boost_text,
            message_thread_id=TOPIC_ID
        )

        last_boost_rating_message_id = msg2.message_id
        last_boost_text = boost_text


async def rating_loop():
    while True:
        await update_group_messages()
        await asyncio.sleep(14400)


# ================= HANDLERS =================

@dp.message(F.text == "📋 Мои записи")
async def my_records(message: Message):

    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("📭 У вас пока нет записей", reply_markup=main_menu())
        return

    text = "📋 Ваши записи:\n\n"

    # ❗ ID УБРАН
    for t in tasks:
        text += f"• {t['action_type']} — осталось {days_left(t['end_time'])} д\n"

    await message.answer(text, reply_markup=main_menu())


@dp.message(F.text == "🗑 Удалить запись")
async def delete_record_menu(message: Message):

    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("📭 У вас нет записей", reply_markup=main_menu())
        return

    kb = []

    # максимум 2 кнопки
    for t in tasks[:2]:
        kb.append([KeyboardButton(text=f"{t['action_type']}")])

    kb.append([KeyboardButton(text="🗑 Удалить все записи")])
    kb.append([KeyboardButton(text="🔙 Назад")])

    await message.answer(
        "🗑 Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(F.text == "🗑 Удалить все записи")
async def delete_all(message: Message):

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", message.from_user.id)

    await message.answer("🗑 Все записи удалены", reply_markup=main_menu())


@dp.message(F.text == "❌ Удалиться из базы")
async def delete_from_db(message: Message, state: FSMContext):

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE tg_id=$1", message.from_user.id)

    await state.clear()
    await message.answer("❌ Вы удалены из базы", reply_markup=main_menu())


# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):

    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("👤 Введите ваш ник:")
        await state.set_state(Form.nickname)
        return

    await message.answer("📋 Главное меню", reply_markup=main_menu())


@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):

    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id,nickname)
        VALUES ($1,$2)
        ON CONFLICT DO NOTHING
        """, message.from_user.id, message.text)

    await message.answer("✅ Регистрация завершена", reply_markup=main_menu())
    await state.clear()


# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(cleanup_tasks())
    asyncio.create_task(rating_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
