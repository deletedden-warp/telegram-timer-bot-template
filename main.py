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
    message_id BIGINT
)
""")

# ================= TEMP STATE =================
user_states = {}

# ================= HELPERS =================

async def delete_safe(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def auto_delete(chat_id, message_id, delay=60):
    await asyncio.sleep(delay)
    await delete_safe(chat_id, message_id)

async def safe_send(chat_id, text, reply_markup=None):
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)

async def get_nick(user_id):
    cursor.execute("SELECT nickname FROM users WHERE user_id=%s", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else None

def progress_bar(percent):
    total = 10
    filled = int(percent / 10)
    return "█" * filled + "░" * (total - filled)

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

    await safe_send(
        msg.chat.id,
        "📋 Меню:",
        reply_markup=menu_kb()
    )

# ================= CALLBACK =================

@dp.callback_query_handler(lambda c: True)
async def callbacks(c: CallbackQuery):
    uid = c.from_user.id
    data = c.data

    await c.answer()

    # ===== CREATE =====
    if data == "create":
        nick = await get_nick(uid)

        if not nick:
            user_states[uid] = {"step": "wait_nick_create"}
            m = await safe_send(c.message.chat.id, "Введи ник:")
            asyncio.create_task(auto_delete(c.message.chat.id, m.message_id))
            return

        user_states[uid] = {}
        m = await safe_send(c.message.chat.id, "Что делаем?", type_kb())
        return

    # ===== TYPE =====
    if data.startswith("type_"):
        user_states[uid]["type"] = "🏗 Строим" if "build" in data else "🔬 Исследуем"
        await safe_send(c.message.chat.id, "Выбери диапазон:", range_kb())
        return

    # ===== RANGE =====
    if data.startswith("range_"):
        start = int(data.split("_")[1])
        await c.message.edit_reply_markup(days_kb(start))
        return

    # ===== DAY =====
    if data.startswith("day_"):
        user_states[uid]["days"] = int(data.split("_")[1])
        await safe_send(c.message.chat.id, "Сколько часов?", hours_kb())
        return

    # ===== HOURS =====
    if data.startswith("hour_"):
        st = user_states.get(uid, {})
        if not st:
            return

        hours = int(data.split("_")[1])
        days = st["days"]
        total = days * 24 + hours

        nick = await get_nick(uid)

        cursor.execute("""
        INSERT INTO tasks (user_id,name,type,hours_left,delete_at,chat_id,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (uid, nick, st["type"], total,
              datetime.utcnow() + timedelta(hours=48),
              c.message.chat.id, 0))

        tid = cursor.fetchone()[0]

        msg = await safe_send(
            c.message.chat.id,
            f"👤 {nick}\n📌 {st['type']}\n⏳ {days}д {hours}ч",
            del_kb(tid)
        )

        cursor.execute("UPDATE tasks SET message_id=%s WHERE id=%s",
                       (msg.message_id, tid))

        user_states.pop(uid, None)
        return

    # ===== SET NICK =====
    if data == "set_nick":
        user_states[uid] = {"step": "wait_nick"}
        m = await safe_send(c.message.chat.id, "Введи ник:")
        asyncio.create_task(auto_delete(c.message.chat.id, m.message_id))
        return

    # ===== DELETE =====
    if data.startswith("del_"):
        tid = int(data.split("_")[1])

        cursor.execute("SELECT chat_id,message_id FROM tasks WHERE id=%s", (tid,))
        r = cursor.fetchone()

        if r:
            await delete_safe(r[0], r[1])

        cursor.execute("DELETE FROM tasks WHERE id=%s", (tid,))
        return

    # ===== MY =====
    if data == "my":
        cursor.execute("SELECT name,type,hours_left FROM tasks WHERE user_id=%s", (uid,))
        rows = cursor.fetchall()

        if not rows:
            return await safe_send(c.message.chat.id, "Нет записей")

        text = "\n".join([f"{n} | {t} | {h//24}д {h%24}ч" for n,t,h in rows])
        await safe_send(c.message.chat.id, text)
        return

    # ===== ALL =====
    if data == "all":
        cursor.execute("SELECT name,type,hours_left FROM tasks")
        rows = cursor.fetchall()

        if not rows:
            return await safe_send(c.message.chat.id, "Нет записей")

        max_hours = max([r[2] for r in rows]) if rows else 1

        text = ""
        for name, typ, hours in rows:
            percent = int((hours / max_hours) * 100)
            bar = progress_bar(percent)

            text += f"👤 {name}\n📌 {typ}\n⏳ {hours//24}д {hours%24}ч\n{bar} {percent}%\n\n"

        await safe_send(c.message.chat.id, text)
        return

    # ===== DELETE ALL =====
    if data == "del_all":
        cursor.execute("SELECT chat_id,message_id FROM tasks WHERE user_id=%s", (uid,))
        for chat_id, msg_id in cursor.fetchall():
            await delete_safe(chat_id, msg_id)

        cursor.execute("DELETE FROM tasks WHERE user_id=%s", (uid,))
        m = await safe_send(c.message.chat.id, "Удалено ✅")
        asyncio.create_task(auto_delete(c.message.chat.id, m.message_id))
        return

# ================= TEXT =================

@dp.message_handler()
async def text(msg: types.Message):
    uid = msg.from_user.id
    st = user_states.get(uid)

    if not st:
        return

    # ===== SAVE NICK =====
    if st.get("step") in ["wait_nick", "wait_nick_create"]:
        cursor.execute("""
        INSERT INTO users (user_id, nickname)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
        """, (uid, msg.text.strip()))

        m = await msg.answer("Ник сохранён ✅")
        asyncio.create_task(auto_delete(msg.chat.id, m.message_id))

        if st["step"] == "wait_nick_create":
            user_states[uid] = {}
            await safe_send(msg.chat.id, "Что делаем?", type_kb())
        else:
            user_states.pop(uid, None)

# ================= UPDATE =================

async def update_tasks():
    cursor.execute("SELECT * FROM tasks")

    for t in cursor.fetchall():
        tid, uid, name, typ, hours, delete_at, chat_id, msg_id = t

        hours = max(0, hours - 4)

        try:
            await bot.edit_message_text(
                f"👤 {name}\n📌 {typ}\n⏳ {hours//24}д {hours%24}ч",
                chat_id,
                msg_id,
                reply_markup=del_kb(tid)
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
