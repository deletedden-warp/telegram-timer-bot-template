import asyncio
from datetime import datetime, timedelta
import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

tasks = {}
user_tasks = {}
task_id_counter = 1
user_state = {}

def type_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🏗 Строим", callback_data="type:build"),
        InlineKeyboardButton("🔬 Исследуем", callback_data="type:research")
    )
    return kb

def days_kb():
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(1, 31):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"day:{i}"))
    return kb

def hours_kb():
    kb = InlineKeyboardMarkup(row_width=6)
    for i in range(1, 24):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"hour:{i}"))
    return kb

def control_kb(task_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("❌ Удалить", callback_data=f"del:{task_id}"))
    return kb

@dp.message_handler(commands=["create"])
async def create(message: types.Message):
    if len(user_tasks.get(message.from_user.id, [])) >= 2:
        await message.delete()
        return
    await message.delete()
    msg = await message.answer("Выбери тип задачи:", reply_markup=type_kb())
    user_state[message.from_user.id] = {"step": "type", "msgs": [msg.message_id]}

@dp.callback_query_handler(lambda c: c.data.startswith("type"))
async def set_type(callback: types.CallbackQuery):
    state = user_state.get(callback.from_user.id)
    t = callback.data.split(":")[1]
    state["type"] = "🏗 Строим" if t == "build" else "🔬 Исследуем"
    state["step"] = "days"
    await callback.message.edit_text("Выбери дни:", reply_markup=days_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("day"))
async def set_days(callback: types.CallbackQuery):
    state = user_state.get(callback.from_user.id)
    state["days"] = int(callback.data.split(":")[1])
    state["step"] = "hours"
    await callback.message.edit_text("Выбери часы:", reply_markup=hours_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("hour"))
async def set_hours(callback: types.CallbackQuery):
    state = user_state.get(callback.from_user.id)
    state["hours"] = int(callback.data.split(":")[1])
    state["step"] = "name"
    await callback.message.edit_text("Введи ник:")

@dp.message_handler()
async def set_name(message: types.Message):
    global task_id_counter
    state = user_state.get(message.from_user.id)
    if not state or state["step"] != "name":
        return
    await message.delete()
    name = message.text
    days = state["days"]
    hours = state["hours"]
    total_hours = days * 24 + hours
    task_id = task_id_counter
    task_id_counter += 1
    now = datetime.utcnow()
    tasks[task_id] = {
        "name": name,
        "type": state["type"],
        "days": days,
        "hours": hours,
        "hours_left": total_hours,
        "delete_at": now + timedelta(hours=48),
        "chat_id": message.chat.id,
        "message_id": None,
        "user_id": message.from_user.id
    }
    user_tasks.setdefault(message.from_user.id, []).append(task_id)
    for msg_id in state["msgs"]:
        try:
            await bot.delete_message(message.chat.id, msg_id)
        except:
            pass
    msg = await message.answer(
        f"👤 Ник: {name}\n📌 Тип: {state['type']}\n⏳ Время: {days} д {hours} ч",
        reply_markup=control_kb(task_id)
    )
    tasks[task_id]["message_id"] = msg.message_id
    user_state.pop(message.from_user.id)

@dp.callback_query_handler(lambda c: c.data.startswith("del"))
async def delete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = tasks.get(task_id)
    if not task or task["user_id"] != callback.from_user.id:
        return await callback.answer("Не твоя запись", show_alert=True)
    await bot.delete_message(task["chat_id"], task["message_id"])
    user_tasks[task["user_id"]].remove(task_id)
    tasks.pop(task_id)

async def updater():
    while True:
        await asyncio.sleep(7200)
        now = datetime.utcnow()
        for task_id in list(tasks.keys()):
            task = tasks[task_id]
            if now >= task["delete_at"]:
                try:
                    await bot.delete_message(task["chat_id"], task["message_id"])
                except:
                    pass
                user_tasks[task["user_id"]].remove(task_id)
                tasks.pop(task_id)
                continue
            task["hours_left"] -= 2
            if task["hours_left"] < 0:
                task["hours_left"] = 0
            d = task["hours_left"] // 24
            h = task["hours_left"] % 24
            try:
                await bot.edit_message_text(
                    f"👤 Ник: {task['name']}\n📌 Тип: {task['type']}\n⏳ Время: {d} д {h} ч",
                    task["chat_id"], task["message_id"],
                    reply_markup=control_kb(task_id)
                )
            except:
                pass

async def on_startup(dp):
    asyncio.create_task(updater())

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
