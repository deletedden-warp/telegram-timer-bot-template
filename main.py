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
        CREATE TABLE IF NOT EXISTS boosts (
            id SERIAL PRIMARY KEY,
            booster_id BIGINT,
            target_id BIGINT,
            boost_type TEXT,
            boost_percent INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
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
    confirm_user_delete = State()
    delete_task_type = State()

# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Создать запись")],
            [KeyboardButton(text="📋 Список заявок"), KeyboardButton(text="⭐️ Рейтинг бустов")],
            [KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="⚡️ Буст")],
            [KeyboardButton(text="🗑 Удалить запись"), KeyboardButton(text="❌ Удалиться из базы")]
        ],
        resize_keyboard=True
    )

def back_button():
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
        ORDER BY t.end_time DESC
        """)

async def get_user_tasks(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM tasks WHERE user_id=$1", tg_id)

async def get_task_by_type(tg_id, task_type):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM tasks WHERE user_id=$1 AND action_type=$2",
            tg_id, task_type
        )

async def get_task_by_id(task_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)

async def log_boost(booster_id, target_id, boost_type, boost_percent):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO boosts (booster_id, target_id, boost_type, boost_percent) VALUES ($1,$2,$3,$4)",
            booster_id, target_id, boost_type, boost_percent
        )

async def get_boost_stats():
    async with pool.acquire() as conn:
        return await conn.fetch("""
        SELECT u.nickname, COUNT(b.id) as boost_count
        FROM boosts b
        JOIN users u ON u.tg_id = b.booster_id
        GROUP BY u.nickname
        ORDER BY boost_count DESC
        """)

# ================= TIMER =================

async def cleanup_tasks():
    while True:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE end_time < NOW()")
        await asyncio.sleep(60)

async def delete_message_after_delay(chat_id, message_id, delay):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

# ================= RATING =================

async def send_rating_to_group():
    global last_rating_message_id

    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    text = "📊 **Список заявок**\n\n"

    text += "🏗 **Стройка**\n"
    if build:
        for t in build:
            text += f"👤 {t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} дн.\n"
    else:
        text += "❌ Нет активных заявок\n"

    text += "\n🔬 **Исследования**\n"
    if research:
        for t in research:
            text += f"👤 {t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} дн.\n"
    else:
        text += "❌ Нет активных заявок\n"

    try:
        if last_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_rating_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID, parse_mode="Markdown")
    last_rating_message_id = msg.message_id

async def send_boost_rating_to_group():
    global last_boost_rating_message_id

    stats = await get_boost_stats()

    text = "⭐️ **Рейтинг бустов**\n\n"
    
    if stats:
        for idx, stat in enumerate(stats, 1):
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "📌"
            text += f"{medal} {stat['nickname']} — {stat['boost_count']} буст(ов)\n"
    else:
        text += "❌ Пока нет совершенных бустов\n"

    try:
        if last_boost_rating_message_id:
            await bot.delete_message(GROUP_CHAT_ID, last_boost_rating_message_id)
    except:
        pass

    msg = await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID, parse_mode="Markdown")
    last_boost_rating_message_id = msg.message_id

async def rating_loop():
    while True:
        await send_rating_to_group()
        await send_boost_rating_to_group()
        await asyncio.sleep(14400)

# ================= START =================

@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)

    if not user:
        await message.answer("📝 **Введи свой ник:**", parse_mode="Markdown", reply_markup=back_button())
        await state.set_state(Form.nickname)
        return

    await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())

@dp.message(Form.nickname)
async def reg(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
        
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, nickname) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            message.from_user.id, message.text
        )

    await message.answer("✅ **Регистрация завершена!**", parse_mode="Markdown", reply_markup=main_menu())
    await state.clear()

# ================= CREATE =================

@dp.message(F.text == "🛠 Создать запись")
async def create(message: Message, state: FSMContext):
    tasks = await get_user_tasks(message.from_user.id)
    
    types = [t['action_type'] for t in tasks]

    buttons = []
    if not any("Стро" in t for t in types):
        buttons.append([KeyboardButton(text="🏗 Строим")])
    if not any("Исслед" in t for t in types):
        buttons.append([KeyboardButton(text="🔬 Исследуем")])

    if not buttons:
        await message.answer("⚠️ **У тебя уже есть обе записи!**\n\nИспользуй кнопку 🗑 Удалить запись", parse_mode="Markdown", reply_markup=main_menu())
        return

    buttons.append([KeyboardButton(text="🔙 Назад")])

    await message.answer("🔧 **Что создаём?**", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state(Form.action)

@dp.message(Form.action)
async def action(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return

    await state.update_data(action=message.text)
    await message.answer("📅 **Сколько дней?**", parse_mode="Markdown", reply_markup=back_button())
    await state.set_state(Form.days)

@dp.message(Form.days)
async def days(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
        
    if not message.text.isdigit():
        await message.answer("❌ **Введи число!**", parse_mode="Markdown")
        return

    data = await state.get_data()
    
    existing = await get_task_by_type(message.from_user.id, data["action"])
    if existing:
        await message.answer(f"⚠️ **У тебя уже есть запись типа {data['action']}!**\n\nСначала удали её или дождись завершения.", parse_mode="Markdown", reply_markup=main_menu())
        await state.clear()
        return

    end = datetime.utcnow() + timedelta(days=int(message.text))

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (user_id, action_type, end_time) VALUES ($1,$2,$3)",
            message.from_user.id, data["action"], end
        )

    await message.answer("✅ **Заявка создана!**", parse_mode="Markdown", reply_markup=main_menu())
    await send_rating_to_group()
    await state.clear()

# ================= MY TASKS =================

@dp.message(F.text == "📋 Мои записи")
async def my_tasks(message: Message):
    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("📭 **У тебя нет активных заявок**", parse_mode="Markdown", reply_markup=main_menu())
        return

    text = "📋 **Твои заявки**\n\n"
    for t in tasks:
        text += f"{icon(t['action_type'])} — {days_left(t['end_time'])} дн.\n"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=main_menu())

# ================= RATING BUTTON =================

@dp.message(F.text == "📋 Список заявок")
async def rating_pm(message: Message):
    tasks = await get_tasks()

    build = [t for t in tasks if "Стро" in t["action_type"]]
    research = [t for t in tasks if "Исслед" in t["action_type"]]

    text = "📊 **Список заявок**\n\n"

    text += "🏗 **Стройка**\n"
    if build:
        for t in build:
            text += f"👤 {t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} дн.\n"
    else:
        text += "❌ Нет активных заявок\n"

    text += "\n🔬 **Исследования**\n"
    if research:
        for t in research:
            text += f"👤 {t['nickname']} {icon(t['action_type'])} — {days_left(t['end_time'])} дн.\n"
    else:
        text += "❌ Нет активных заявок\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⭐️ Рейтинг бустов")
async def boost_rating_pm(message: Message):
    stats = await get_boost_stats()

    text = "⭐️ **Рейтинг бустов**\n\n"
    
    if stats:
        for idx, stat in enumerate(stats, 1):
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "📌"
            text += f"{medal} {stat['nickname']} — {stat['boost_count']} буст(ов)\n"
    else:
        text += "❌ Пока нет совершенных бустов\n"

    await message.answer(text, parse_mode="Markdown")

# ================= DELETE TASK =================

@dp.message(F.text == "🗑 Удалить запись")
async def delete_task(message: Message, state: FSMContext):
    tasks = await get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("❌ **У тебя нет активных заявок для удаления**", parse_mode="Markdown", reply_markup=main_menu())
        return

    buttons = []
    
    for t in tasks:
        if "Стро" in t["action_type"]:
            buttons.append([KeyboardButton(text="🏗 Удалить стройку")])
        elif "Исслед" in t["action_type"]:
            buttons.append([KeyboardButton(text="🔬 Удалить исследование")])
    
    if len(tasks) == 2:
        buttons.append([KeyboardButton(text="🗑 Удалить обе записи")])
    
    buttons.append([KeyboardButton(text="🔙 Назад")])

    await message.answer("🗑 **Что удаляем?**", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state(Form.delete_task_type)
    await state.update_data(tasks=tasks)

@dp.message(Form.delete_task_type)
async def delete_task_choice(message: Message, state: FSMContext):
    data = await state.get_data()
    tasks = data['tasks']
    
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
    
    async with pool.acquire() as conn:
        if message.text == "🏗 Удалить стройку":
            for t in tasks:
                if "Стро" in t["action_type"]:
                    await conn.execute("DELETE FROM tasks WHERE id=$1", t['id'])
                    await message.answer("✅ **Стройка удалена!**", parse_mode="Markdown")
                    break
        elif message.text == "🔬 Удалить исследование":
            for t in tasks:
                if "Исслед" in t["action_type"]:
                    await conn.execute("DELETE FROM tasks WHERE id=$1", t['id'])
                    await message.answer("✅ **Исследование удалено!**", parse_mode="Markdown")
                    break
        elif message.text == "🗑 Удалить обе записи" and len(tasks) == 2:
            for t in tasks:
                await conn.execute("DELETE FROM tasks WHERE id=$1", t['id'])
            await message.answer("✅ **Обе записи удалены!**", parse_mode="Markdown")
    
    await send_rating_to_group()
    await state.clear()
    await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())

# ================= DELETE USER =================

@dp.message(F.text == "❌ Удалиться из базы")
async def delete_user_start(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
        resize_keyboard=True
    )
    await message.answer("⚠️ **Ты уверен?** Это действие необратимо!", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Form.confirm_user_delete)

@dp.message(Form.confirm_user_delete)
async def delete_user_confirm(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
        
    if message.text == "✅ Да":
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE user_id=$1", message.from_user.id)
            await conn.execute("DELETE FROM users WHERE tg_id=$1", message.from_user.id)
            await conn.execute("DELETE FROM boosts WHERE booster_id=$1 OR target_id=$1", message.from_user.id)

        await message.answer("🗑 **Ты удалён из базы.**\n\nВведи /start для регистрации", parse_mode="Markdown")
        await state.set_state(Form.nickname)
        await send_rating_to_group()
    elif message.text == "❌ Нет":
        await message.answer("✅ **Операция отменена**", parse_mode="Markdown", reply_markup=main_menu())
        await state.clear()
    else:
        await message.answer("❌ **Используй кнопки!**", parse_mode="Markdown")

# ================= BOOST =================

@dp.message(F.text == "⚡️ Буст")
async def boost_start(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Стройка")],
            [KeyboardButton(text="🔬 Исследования")],
            [KeyboardButton(text="🔙 Назад")]
        ], 
        resize_keyboard=True
    )
    await message.answer("⚡️ **Выбери тип для буста:**", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Form.boost_type)

@dp.message(Form.boost_type)
async def boost_type_handler(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
    
    # Сохраняем выбранный тип буста
    boost_type = message.text
    await state.update_data(boost_type=boost_type)
    
    # Получаем все задачи
    tasks = await get_tasks()
    
    # Фильтруем по типу и исключаем себя
    filtered = []
    for t in tasks:
        if boost_type == "🏗 Стройка" and "Стро" in t["action_type"]:
            if t['user_id'] != message.from_user.id:
                filtered.append(t)
        elif boost_type == "🔬 Исследования" and "Исслед" in t["action_type"]:
            if t['user_id'] != message.from_user.id:
                filtered.append(t)
    
    if not filtered:
        await message.answer("❌ **Нет доступных пользователей для буста**", parse_mode="Markdown", reply_markup=main_menu())
        await state.clear()
        return
    
    # Сохраняем список пользователей
    await state.update_data(filtered_users=filtered)
    
    # Создаем клавиатуру с никами
    buttons = []
    for user in filtered:
        buttons.append([KeyboardButton(text=user['nickname'])])
    buttons.append([KeyboardButton(text="🔙 Назад")])
    
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("🎯 **Выбери цель для буста:**", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Form.boost_target)

@dp.message(Form.boost_target)
async def boost_target_handler(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
    
    data = await state.get_data()
    filtered_users = data.get('filtered_users', [])
    
    # Ищем выбранного пользователя
    selected_user = None
    for user in filtered_users:
        if user['nickname'] == message.text:
            selected_user = user
            break
    
    if not selected_user:
        await message.answer("❌ **Выбери пользователя из списка!**", parse_mode="Markdown")
        return
    
    # Сохраняем данные о цели
    await state.update_data(
        target_task_id=selected_user['id'],
        target_user_id=selected_user['user_id'],
        target_nickname=selected_user['nickname'],
        target_action_type=selected_user['action_type']
    )
    
    # Клавиатура с уровнями буста
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔥 Уровень 1: 5%")],
            [KeyboardButton(text="⚡️ Уровень 2: 10%")],
            [KeyboardButton(text="💪 Уровень 3: 15%")],
            [KeyboardButton(text="🔙 Назад")]
        ], 
        resize_keyboard=True
    )
    await message.answer("📈 **Выбери уровень буста:**", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Form.boost_percent)

@dp.message(Form.boost_percent)
async def boost_apply_handler(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🏠 **Главное меню**", parse_mode="Markdown", reply_markup=main_menu())
        return
    
    # Определяем процент буста
    percent_map = {
        "🔥 Уровень 1: 5%": 5,
        "⚡️ Уровень 2: 10%": 10,
        "💪 Уровень 3: 15%": 15
    }
    
    if message.text not in percent_map:
        await message.answer("❌ **Выбери уровень из списка!**", parse_mode="Markdown")
        return
    
    percent = percent_map[message.text]
    data = await state.get_data()
    
    # Получаем сохраненные данные
    target_task_id = data.get('target_task_id')
    target_user_id = data.get('target_user_id')
    target_nickname = data.get('target_nickname')
    target_action_type = data.get('target_action_type')
    boost_type = data.get('boost_type')
    
    if not target_task_id:
        await message.answer("❌ **Ошибка! Начни процесс буста заново.**", parse_mode="Markdown")
        await state.clear()
        return
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Получаем задачу цели
            target_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id=$1 FOR UPDATE",
                target_task_id
            )
            
            if not target_task:
                await message.answer("❌ **Запись уже удалена!**", parse_mode="Markdown")
                await state.clear()
                return
            
            # Рассчитываем новое время для цели
            current_end = target_task['end_time']
            left_seconds = seconds_left(current_end)
            new_seconds = int(left_seconds * (1 - percent / 100))
            new_end_time = datetime.utcnow() + timedelta(seconds=new_seconds)
            
            # Обновляем задачу цели
            await conn.execute(
                "UPDATE tasks SET end_time=$1 WHERE id=$2",
                new_end_time, target_task_id
            )
            
            # Находим и обновляем свою задачу того же типа
            self_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE user_id=$1 AND action_type=$2",
                message.from_user.id, target_action_type
            )
            
            if self_task:
                self_left_seconds = seconds_left(self_task['end_time'])
                self_new_seconds = int(self_left_seconds * (1 - percent / 100))
                self_new_end_time = datetime.utcnow() + timedelta(seconds=self_new_seconds)
                
                await conn.execute(
                    "UPDATE tasks SET end_time=$1 WHERE id=$2",
                    self_new_end_time, self_task['id']
                )
            
            # Логируем буст
            await log_boost(message.from_user.id, target_user_id, target_action_type, percent)
            
            # Получаем ник бустера
            booster = await conn.fetchrow(
                "SELECT nickname FROM users WHERE tg_id=$1",
                message.from_user.id
            )
    
    # Отправляем сообщение в группу
    boost_text = f"🔥 **Буст!** {booster['nickname']} ускорил "
    if "Стро" in target_action_type:
        boost_text += f"стройку для {target_nickname} на {percent}%"
    else:
        boost_text += f"исследование для {target_nickname} на {percent}%"
    
    boost_msg = await bot.send_message(GROUP_CHAT_ID, boost_text, message_thread_id=TOPIC_ID, parse_mode="Markdown")
    
    # Удаляем сообщение через 12 часов
    asyncio.create_task(delete_message_after_delay(GROUP_CHAT_ID, boost_msg.message_id, 43200))
    
    # Ответ пользователю
    await message.answer(
        f"✅ **Буст выполнен!**\n\n"
        f"📊 Ты ускорил {target_nickname} на {percent}%\n"
        f"⚡️ Твоя задача тоже ускорена на {percent}%",
        parse_mode="Markdown", 
        reply_markup=main_menu()
    )
    
    # Обновляем рейтинги
    await send_rating_to_group()
    await send_boost_rating_to_group()
    
    # Очищаем состояние
    await state.clear()

# ================= RUN =================

async def main():
    await init_db()
    asyncio.create_task(rating_loop())
    asyncio.create_task(cleanup_tasks())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
