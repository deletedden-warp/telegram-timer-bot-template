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

GROUP_CHAT_ID = -1003672834247
THREAD_ID = 5239

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

pool: asyncpg.Pool = None
last_message_id = None


# ================= FSM =================

class Form(StatesGroup):
    nickname = State()
    action = State()
    days = State()
    delete_one = State()
    confirm_delete = State()
    boost_type = State()
    boost_target = State()
    boost_percent = State()


# ================= UI =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📜 Посмотреть все записи")],
            [KeyboardButton(text="🏆 Рейтинг")],
            [KeyboardButton(text="⚡ Буст")],
            [KeyboardButton(text="🗑 Удалить одну запись")],
            [KeyboardButton(text="🧹 Удалить все записи")],
            [KeyboardButton(text="💀 Удалиться из базы")]
        ],
        resize_keyboard=True
    )


def action_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Строим"), KeyboardButton(text="🔬 Исследуем")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )


# ================= UTILS =================

def seconds_left(end):
    return max(0, int((end - datetime.utcnow()).total_seconds()))


def days_left(end):
    return seconds_left(end) // 86400


def icon(t):
    return "🏗" if "Строим" in t else "🔬"


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


# ================= DB FUNCS =================

async def get_user(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)


async def create_user(tg_id, nickname, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (tg_id, nickname, chat_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (tg_id) DO UPDATE
        SET nickname=$2, chat_id=$3
        """, tg_id, nickname, chat_id)


async def get_tasks():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT t.id, u.nickname, t.action_type, t.end_time, t.user_id
        FROM tasks t
        JOIN users u ON u.tg_id = t.user_id
        """)


async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT * FROM tasks WHERE user_id=$1
        """, tg_id)


async def add_task(tg_id, action, days):
    tasks = await get_user_tasks(tg_id)

    # лимит: 1 тип
    for t in tasks:
        if t["action_type"] == action:
            return False

    if len(tasks) >= 2:
        return False

    end = datetime.utcnow() + timedelta(days=days)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            tg_id, action, end
        )
    return True


async def delete_task(task_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)


async def delete_tasks_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)


async def delete_user(tg_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE user_id=$1", tg_id)
        await conn.execute("DELETE FROM users WHERE tg_id=$1", tg_id)


async def update_task(task_id, new_time):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tasks SET end_time=$1 WHERE id=$2",
            new_time, task_id
        )


# ================= RATING =================

async def send_rating():
    global last_message_id

    tasks = await get_tasks()

    build = []
    research = []

    for t in tasks:
        d = days_left(t["end_time"])
        if "Строим" in t["action_type"]:
            build.append((t["nickname"], d))
        else:
            research.append((t["nickname"], d))

    build.sort(key=lambda x: x[1], reverse=True)
    research.sort(key=lambda x: x[1], reverse=True)

    text = "🏆 Рейтинг\n\n"

    text += "🏗 Стройка:\n"
    for i, t in enumerate(build, 1):
        text += f"{i}) {t[0]} — {t[1]} дн\n"

    text += "\n🔬 Исследования:\n"
    for i, t in enumerate(research, 1):
        text += f"{i}) {t[0]} — {t[1]} дн\n"

    # удаляем старое сообщение
    try:
        if last_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=THREAD_ID)
    last_message_id = msg.message_id


async def auto_rating():
    while True:
        await asyncio.sleep(14400)
        await send_rating()


# ================= HANDLERS =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
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
        await state.clear()
        return

    await state.update_data(action=message.text)
    await message.answer("Сколько дней?")
    await state.set_state(Form.days)


@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    data = await state.get_data()

    ok = await add_task(message.from_user.id, data["action"], int(message.text))

    if not ok:
        await message.answer("❌ Лимит или уже есть такая запись", reply_markup=main_menu())
        await state.clear()
        return

    await message.answer("✅ Создано", reply_markup=main_menu())
    await send_rating()
    await state.clear()


# ================= DELETE ONE =================

@dp.message(F.text == "🗑 Удалить одну запись")
async def delete_one(message: Message, state: FSMContext):
    tasks = await get_tasks()

    kb = [[KeyboardButton(text=f"{t['id']} | {t['nickname']} | {icon(t['action_type'])}")]
          for t in tasks if t["user_id"] == message.from_user.id]

    await message.answer("Выбери запись", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Form.delete_one)


@dp.message(Form.delete_one)
async def delete_one_apply(message: Message, state: FSMContext):
    task_id = int(message.text.split(" | ")[0])
    await delete_task(task_id)

    await message.answer("Удалено", reply_markup=main_menu())
    await send_rating()
    await state.clear()


# ================= DELETE ALL =================

@dp.message(F.text == "🧹 Удалить все записи")
async def delete_all(message: Message):
    await delete_tasks_user(message.from_user.id)
    await message.answer("Удалено всё", reply_markup=main_menu())
    await send_rating()


# ================= DELETE USER =================

@dp.message(F.text == "💀 Удалиться из базы")
async def delete_confirm(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]
        ],
        resize_keyboard=True
    )
    await message.answer("Ты уверен?", reply_markup=kb)
    await state.set_state(Form.confirm_delete)


@dp.message(Form.confirm_delete)
async def delete_final(message: Message, state: FSMContext):
    if "Да" in message.text:
        await delete_user(message.from_user.id)
        await message.answer("Удалён", reply_markup=main_menu())
        await send_rating()
    else:
        await message.answer("Отмена", reply_markup=main_menu())

    await state.clear()


# ================= BOOST =================

@dp.message(F.text == "⚡ Буст")
async def boost_start(message: Message, state: FSMContext):
    await message.answer("Тип?", reply_markup=action_menu())
    await state.set_state(Form.boost_type)


@dp.message(Form.boost_type)
async def boost_type(message: Message, state: FSMContext):
    await state.update_data(type=message.text)

    tasks = await get_tasks()

    kb = [[KeyboardButton(text=f"{t['nickname']} | {icon(t['action_type'])} | {days_left(t['end_time'])} д")]
          for t in tasks]

    await message.answer("Кого бустим?", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(Form.boost_target)


@dp.message(Form.boost_target)
async def boost_target(message: Message, state: FSMContext):
    nickname = message.text.split(" | ")[0]

    user = await get_user(message.from_user.id)
    if user["nickname"] == nickname:
        await message.answer("❌ Нельзя бустить себя", reply_markup=main_menu())
        await state.clear()
        return

    await state.update_data(target=nickname)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="5%")],
            [KeyboardButton(text="10%")],
            [KeyboardButton(text="15%")]
        ],
        resize_keyboard=True
    )

    await message.answer("Процент?", reply_markup=kb)
    await state.set_state(Form.boost_percent)


@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    percent = int(message.text.replace("%", "")) / 100

    tasks = await get_tasks()
    attacker = (await get_user(message.from_user.id))["nickname"]

    for t in tasks:
        if t["nickname"] == data["target"]:
            left = seconds_left(t["end_time"])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))
            await update_task(t["id"], new_time)

            await bot.send_message(
                GROUP_CHAT_ID,
                f"🎉 {attacker} бустанул {t['nickname']} на {int(percent*100)}%",
                message_thread_id=THREAD_ID
            )

    await send_rating()
    await message.answer("Готово", reply_markup=main_menu())
    await state.clear()


# ================= RUN =================

async def main():
    await init_db()

    asyncio.create_task(auto_rating())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
