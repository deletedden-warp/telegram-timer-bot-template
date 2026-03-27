import asyncio
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import psycopg2
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler()

# ================= DB =================

url = urlparse(DATABASE_URL)

conn = psycopg2.connect(
    database=url.path[1:],
    user=url.username,
    password=url.password,
    host=url.hostname,
    port=url.port
)

conn.autocommit = True
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    nickname TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    name TEXT,
    type TEXT,
    hours_left INTEGER,
    delete_at TIMESTAMP,
    chat_id BIGINT,
    message_id BIGINT,
    thread_id BIGINT
)
""")

# ================= STATE =================

user_states = {}

# ================= HELPERS =================

async def delete_safe(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def auto_delete(chat_id, message_id, delay):
    await asyncio.sleep(delay)
    await delete_safe(chat_id, message_id)

async def safe_send(chat_id, text, reply_markup=None, thread_id=None, auto_del=10):
    msg = await bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        message_thread_id=thread_id
    )
    if auto_del:
        asyncio.create_task(auto_delete(chat_id, msg.message_id, auto_del))
    return msg

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

def progress_bar(percent):
    total = 10
    filled = int(percent / 10)
    return "█" * filled + "░" * (total - filled)

async def reset_user(uid, chat_id, thread_id):
    user_states.pop(uid, None)
    await safe_send(chat_id, "⏱ Время вышло. Начни заново:", menu_kb(), thread_id)

# ================= TIMEOUT =================

async def start_timeout(uid, chat_id, thread_id):
    await asyncio.sleep(300)  # 5 минут

    st = user_states.get(uid)
    if st and st.get("active"):
        await reset_user(uid, chat_id, thread_id)

# ================= UI =================

def menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Создать", callback_data="create"),
        InlineKeyboardButton("📋 Мои записи", callback_data="my"),
    )
    kb.add(
        InlineKeyboardButton("📊 Все записи", callback_data="all"),
        InlineKeyboardButton("❌ Удалить все", callback_data="del_all"),
    )
    kb.add(
        InlineKeyboardButton("✏️ Изменить ник", callback_data="set_nick"),
    )
    return kb

def type_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("🏗 Строим", callback_data="type_build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="type_research")
    )

def range_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("1-30", callback_data="range_1"),
        InlineKeyboardButton("31-60", callback_data="range_31"),
        InlineKeyboardButton("61-90", callback_data="range_61")
    )

def days_kb(start):
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(start, start + 30):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"day_{i}"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"hour_{i}"))
    return kb

def del_kb(tid):
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del_{tid}")
    )

# ================= MENU =================

@dp.message_handler(commands=["menu"])
async def menu(msg: types.Message):
    await delete_safe(msg.chat.id, msg.message_id)

    m = await safe_send(
        msg.chat.id,
        "📋 Меню:",
        menu_kb(),
        msg.message_thread_id,
        auto_del=None
    )

    asyncio.create_task(auto_delete(msg.chat.id, m.message_id, 120))

# ================= CALLBACK =================

@dp.callback_query_handler(lambda c: True)
async def callbacks(c: CallbackQuery):
    uid = c.from_user.id
    data = c.data
    thread_id = c.message.message_thread_id

    await c.answer()

    # ===== CREATE =====
    if data == "create":
        nick = await get_nick(uid)

        user_states[uid] = {"active": True, "thread": thread_id}
        asyncio.create_task(start_timeout(uid, c.message.chat.id, thread_id))

        if not nick:
            user_states[uid]["step"] = "wait_nick_create"
            await safe_send(c.message.chat.id, "Введи ник:", thread_id=thread_id)
            return

        await safe_send(c.message.chat.id, "Что делаем?", type_kb(), thread_id)
        return

    # защита от смены темы
    if uid in user_states and user_states[uid].get("thread") != thread_id:
        return

    # ===== TYPE =====
    if data.startswith("type_"):
        user_states[uid]["type"] = "🏗 Строим" if "build" in data else "🔬 Исследуем"
        await safe_send(c.message.chat.id, "Выбери диапазон:", range_kb(), thread_id)
        return

    # ===== RANGE =====
    if data.startswith("range_"):
        start = int(data.split("_")[1])
        await c.message.edit_reply_markup(days_kb(start))
        return

    # ===== DAY =====
    if data.startswith("day_"):
        user_states[uid]["days"] = int(data.split("_")[1])
        await safe_send(c.message.chat.id, "Сколько часов?", hours_kb(), thread_id)
        return

    # ===== HOURS =====
    if data.startswith("hour_"):
        st = user_states.get(uid)
        if not st:
            return

        hours = int(data.split("_")[1])
        days = st["days"]
        total = days * 24 + hours
        nick = await get_nick(uid)

        cursor.execute("""
        INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id,thread_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (uid, nick, st["type"], total,
              datetime.utcnow() + timedelta(hours=48),
              c.message.chat.id, 0, thread_id))

        tid = cursor.fetchone()[0]

        msg = await bot.send_message(
            c.message.chat.id,
            f"👤 {nick}\n📌 {st['type']}\n⏳ {days}д {hours}ч",
            reply_markup=del_kb(tid),
            message_thread_id=thread_id
        )

        cursor.execute("UPDATE tasks SET message_id=%s WHERE id=%s",
                       (msg.message_id, tid))

        user_states.pop(uid, None)
        return

    # ===== DELETE ALL =====
    if data == "del_all":
        cursor.execute("SELECT chat_id,message_id FROM tasks WHERE user_id=%s", (uid,))
        for chat_id, msg_id in cursor.fetchall():
            await delete_safe(chat_id, msg_id)

        cursor.execute("DELETE FROM tasks WHERE user_id=%s", (uid,))
        await safe_send(c.message.chat.id, "Удалено ✅", thread_id=thread_id)
        return

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    if st.get("thread") != msg.message_thread_id:
        return

    if st.get("step") == "wait_nick_create":
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        await safe_send(msg.chat.id, "Ник сохранён ✅", thread_id=msg.message_thread_id)
        await safe_send(msg.chat.id, "Что делаем?", type_kb(), msg.message_thread_id)

# ================= UPDATE =================

async def update_tasks():
    cursor.execute("SELECT * FROM tasks")

    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id, thread_id = t

        hours = max(0, hours - 4)

        try:
            await bot.edit_message_text(
                f"👤 {name}\n📌 {typ}\n⏳ {hours//24}д {hours%24}ч",
                chat_id,
                msg_id,
                reply_markup=del_kb(tid),
                message_thread_id=thread_id
            )
        except:
            pass

        cursor.execute("UPDATE tasks SET hours_left=%s WHERE id=%s",
                       (hours, tid))

# ================= START =================

async def on_startup(dp):
    scheduler.add_job(update_tasks, "interval", hours=4)
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
