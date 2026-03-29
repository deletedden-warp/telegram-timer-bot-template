# ================= BOOST =================

@dp.message(F.text == "⚡ Буст")
async def boost_start(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏗 Стройка"), KeyboardButton(text="🔬 Исследования")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    await message.answer("Выбери тип", reply_markup=kb)
    await state.set_state(Form.boost_type)


@dp.message(Form.boost_type)
async def boost_type(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Меню", reply_markup=main_menu())
        return

    await state.update_data(boost_type=message.text)

    tasks = await get_tasks()

    filtered = [
        t for t in tasks
        if ("Стро" in message.text and "Стро" in t["action_type"]) or
           ("Исслед" in message.text and "Исслед" in t["action_type"])
    ]

    if not filtered:
        await message.answer("Нет записей")
        await state.clear()
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{t['nickname']} ({days_left(t['end_time'])} д)")]
            for t in filtered
        ] + [[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )

    await state.update_data(filtered_tasks=filtered)

    await message.answer("Выбери игрока", reply_markup=kb)
    await state.set_state(Form.boost_target)


@dp.message(Form.boost_target)
async def boost_target(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(Form.boost_type)
        return

    data = await state.get_data()
    tasks = data["filtered_tasks"]

    selected = None

    for t in tasks:
        if t["nickname"] in message.text:
            selected = t
            break

    if not selected:
        await message.answer("Ошибка выбора")
        return

    await state.update_data(target_task=selected)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Уровень 1: 5%")],
            [KeyboardButton(text="Уровень 2: 10%")],
            [KeyboardButton(text="Уровень 3: 15%")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

    await message.answer("Выбери уровень", reply_markup=kb)
    await state.set_state(Form.boost_percent)


@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(Form.boost_target)
        return

    percent_map = {
        "Уровень 1: 5%": 0.05,
        "Уровень 2: 10%": 0.10,
        "Уровень 3: 15%": 0.15
    }

    if message.text not in percent_map:
        await message.answer("Выбери кнопку")
        return

    percent = percent_map[message.text]
    data = await state.get_data()
    target = data["target_task"]

    async with pool.acquire() as conn:
        # цель
        target_task = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", target["id"])

        if not target_task:
            await message.answer("Ошибка")
            await state.clear()
            return

        if target_task["user_id"] == message.from_user.id:
            await message.answer("Нельзя бустить себя")
            await state.clear()
            return

        # уменьшаем цель
        left = seconds_left(target_task["end_time"])
        new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))

        await conn.execute(
            "UPDATE tasks SET end_time=$1 WHERE id=$2",
            new_time, target_task["id"]
        )

        # буст себе
        self_task = await conn.fetchrow("""
        SELECT * FROM tasks 
        WHERE user_id=$1 AND action_type=$2
        """, message.from_user.id, target_task["action_type"])

        if self_task:
            left_self = seconds_left(self_task["end_time"])
            new_self = datetime.utcnow() + timedelta(seconds=left_self * (1 - percent))

            await conn.execute(
                "UPDATE tasks SET end_time=$1 WHERE id=$2",
                new_self, self_task["id"]
            )

        # ники
        user = await conn.fetchrow("SELECT nickname FROM users WHERE tg_id=$1", message.from_user.id)
        target_user = await conn.fetchrow("SELECT nickname FROM users WHERE tg_id=$1", target_task["user_id"])

    # сообщение в группу
    if "Стро" in target_task["action_type"]:
        text = f"🔥 Ура! {user['nickname']} ускорил стройку для {target_user['nickname']} на {int(percent*100)}%"
    else:
        text = f"🔥 Ура! {user['nickname']} ускорил исследование для {target_user['nickname']} на {int(percent*100)}%"

    await bot.send_message(
        GROUP_CHAT_ID,
        text,
        message_thread_id=TOPIC_ID
    )

    await message.answer("Буст успешно применён ✅")

    await send_rating()
    await state.clear()
