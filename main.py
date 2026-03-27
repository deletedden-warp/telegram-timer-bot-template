import os
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())


# ================= DB =================

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        tg_id BIGINT UNIQUE,
        nickname TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        action_type TEXT,
        days INTEGER
    );
    """)

    conn.commit()
    conn.close()


init_db()


# ================= FSM =================

class Form(StatesGroup):
    waiting_nickname = State()
    choosing_action = State()
    waiting_days = State()
    editing_nick = State()


# ================= UTILS =================

async def delete_messages(messages):
    await asyncio.sleep(1)
    for msg in messages:
        try:
            await msg.delete()
        except:
            pass


async def timeout_check(state: FSMContext, chat_id, messages):
    await asyncio.sleep(180)
    data = await state.get_data()
    if data.get("active"):
        await state.finish()
        await delete_messages(messages)


def get_user(tg_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM users WHERE tg_id=%s", (tg_id,))
    user = cur.fetchone()

    conn.close()
    return user


def create_user(tg_id, nickname):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO users (tg_id, nickname) VALUES (%s, %s)",
        (tg_id, nickname)
    )

    conn.commit()
    conn.close()


def update_nickname(tg_id, nickname):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET nickname=%s WHERE tg_id=%s",
        (nickname, tg_id)
    )

    conn.commit()
    conn.close()


def delete_user(tg_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM users WHERE tg_id=%s", (tg_id,))
    cur.execute("DELETE FROM tasks WHERE user_id=%s", (tg_id,))

    conn.commit()
    conn.close()


def get_tasks_count(tg_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s", (tg_id,))
    count = cur.fetchone()[0]

    conn.close()
    return count


def add_task(tg_id, action_type, days):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO tasks (user_id, action_type, days) VALUES (%s, %s, %s)",
        (tg_id, action_type, days)
    )

    conn.commit()
    conn.close()


def delete_tasks(tg_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM tasks WHERE user_id=%s", (tg_id,))

    conn.commit()
    conn.close()


def get_all_tasks():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
    SELECT u.nickname, t.action_type, t.days
    FROM tasks t
    JOIN users u ON u.tg_id = t.user_id
    """)

    tasks = cur.fetchall()
    conn.close()
    return tasks


# ================= KEYBOARDS =================

def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🛠 Создать запись")
    kb.add("🗑 Удалить мои записи")
    kb.add("📜 Посмотреть все записи")
    kb.add("✏️ Изменить никнейм")
    kb.add("💀 Удалиться из базы")
    return kb


# ================= HANDLERS =================

@dp.message_handler(commands=["menu"])
async def menu(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)

    if not user:
        msg = await message.answer("⚔️ Кто ты воин? Представься")
        await Form.waiting_nickname.set()
        return

    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())


@dp.message_handler(state=Form.waiting_nickname)
async def save_nick(message: types.Message, state: FSMContext):
    create_user(message.from_user.id, message.text)

    await message.answer(f"👋 Приветствую тебя \"{message.text}\"!")
    await message.answer("🧠 Что будем делать?", reply_markup=main_menu())

    await state.finish()


# ================= CREATE TASK =================

@dp.message_handler(lambda m: m.text == "🛠 Создать запись")
async def create_task(message: types.Message, state: FSMContext):
    if get_tasks_count(message.from_user.id) >= 2:
        await message.answer("🚫 У тебя уже максимум записей (2)")
        return

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🏗 Строим", "🔬 Исследуем")

    msg = await message.answer("⚙️ Что делаем?", reply_markup=kb)

    await state.update_data(
        messages=[msg],
        active=True,
        start_time=datetime.now()
    )

    asyncio.create_task(timeout_check(state, message.chat.id, [msg]))

    await Form.choosing_action.set()


@dp.message_handler(state=Form.choosing_action)
async def choose_action(message: types.Message, state: FSMContext):
    if message.text not in ["🏗 Строим", "🔬 Исследуем"]:
        return

    data = await state.get_data()
    messages = data.get("messages", [])

    msg = await message.answer("⏳ Сколько осталось дней до завершения?")
    messages.append(msg)
    messages.append(message)

    await state.update_data(action=message.text, messages=messages)
    await Form.waiting_days.set()


@dp.message_handler(state=Form.waiting_days)
async def save_days(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❗ Введи число без букв")
        return

    data = await state.get_data()
    messages = data.get("messages", [])

    action = data["action"]
    days = int(message.text)

    user = get_user(message.from_user.id)

    add_task(message.from_user.id, action, days)

    final = await message.answer(
        f"✅ Я записал. Ты молодец!\n\n"
        f"👤 {user['nickname']}\n"
        f"⚙️ {action}\n"
        f"⏳ {days} дней"
    )

    await delete_messages(messages + [message])

    await state.finish()


# ================= DELETE TASKS =================

@dp.message_handler(lambda m: m.text == "🗑 Удалить мои записи")
async def delete_confirm(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ Да, удаляй, я создам новые")
    kb.add("❌ Нет, я передумал.")

    await message.answer("⚠️ Уверены?", reply_markup=kb)


@dp.message_handler(lambda m: m.text == "✅ Да, удаляй, я создам новые")
async def delete_yes(message: types.Message):
    delete_tasks(message.from_user.id)
    await message.answer("🧹 Удалено, можешь создавать записи снова.", reply_markup=main_menu())


@dp.message_handler(lambda m: m.text == "❌ Нет, я передумал.")
async def delete_no(message: types.Message):
    await message.answer("🤨 Ну и нахрена ты меня тревожишь?.", reply_markup=main_menu())


# ================= VIEW TASKS =================

@dp.message_handler(lambda m: m.text == "📜 Посмотреть все записи")
async def show_tasks(message: types.Message):
    tasks = get_all_tasks()

    if not tasks:
        await message.answer("😶 ...а нету ничего, давай исправим это? Создай запись.")
        return

    text = "📋 Список записей:\n\n"

    for i, t in enumerate(tasks, 1):
        text += f"{i}) {t['nickname']} | {t['action_type']} | {t['days']} дней\n"

    await message.answer(text)


# ================= EDIT NICK =================

@dp.message_handler(lambda m: m.text == "✏️ Изменить никнейм")
async def edit_nick(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)

    await message.answer(
        f"🧾 Сейчас у тебя записан такой ник: \"{user['nickname']}\", на какой будем менять?"
    )

    await Form.editing_nick.set()


@dp.message_handler(state=Form.editing_nick)
async def save_new_nick(message: types.Message, state: FSMContext):
    update_nickname(message.from_user.id, message.text)

    await message.answer(
        f"✅ Отлично! Я переписал твой ник, теперь ты записан как: \"{message.text}\"",
        reply_markup=main_menu()
    )

    await state.finish()


# ================= DELETE USER =================

@dp.message_handler(lambda m: m.text == "💀 Удалиться из базы")
async def delete_user_confirm(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔥 Да, удаляй при мне")
    kb.add("🙏 Нет, простите, я больше так не буду.")

    await message.answer("😈 Вот значит как? Ну а ты уверен?", reply_markup=kb)


@dp.message_handler(lambda m: m.text == "🔥 Да, удаляй при мне")
async def delete_user_yes(message: types.Message):
    delete_user(message.from_user.id)

    await message.answer("🕳 Твой выбор, твой путь, удалил, забыл, впервые тебя вижу.")


@dp.message_handler(lambda m: m.text == "🙏 Нет, простите, я больше так не буду.")
async def delete_user_no(message: types.Message):
    await message.answer("😤 Вот и знай своё место и больше так не делай!", reply_markup=main_menu())


# ================= START =================

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
