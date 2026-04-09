@dp.message(Form.boost_percent)
async def boost_apply(message: Message, state: FSMContext):

    # исправление кнопки назад
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Меню", reply_markup=main_menu())
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
    target = data['target']

    async with pool.acquire() as conn:
        async with conn.transaction():
            target_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id=$1 FOR UPDATE",
                target['id']
            )

            if target_task['user_id'] == message.from_user.id:
                await message.answer("Нельзя бустить себя")
                await state.clear()
                return

            left = seconds_left(target_task['end_time'])
            new_time = datetime.utcnow() + timedelta(seconds=left * (1 - percent))

            await conn.execute(
                "UPDATE tasks SET end_time=$1 WHERE id=$2",
                new_time, target_task['id']
            )

            self_task = await conn.fetchrow(
                "SELECT * FROM tasks WHERE user_id=$1 AND action_type=$2",
                message.from_user.id,
                target_task['action_type']
            )

            if self_task:
                left2 = seconds_left(self_task['end_time'])
                new2 = datetime.utcnow() + timedelta(seconds=left2 * (1 - percent))

                await conn.execute(
                    "UPDATE tasks SET end_time=$1 WHERE id=$2",
                    new2, self_task['id']
                )

            user = await conn.fetchrow(
                "SELECT nickname FROM users WHERE tg_id=$1",
                message.from_user.id
            )
            target_user = await conn.fetchrow(
                "SELECT nickname FROM users WHERE tg_id=$1",
                target_task['user_id']
            )

    if "Стро" in target_task['action_type']:
        text = f"🔥 Ура! {user['nickname']} ускорил стройку для {target_user['nickname']} на {int(percent*100)}%"
    else:
        text = f"🔥 Ура! {user['nickname']} ускорил исследование для {target_user['nickname']} на {int(percent*100)}%"

    await bot.send_message(GROUP_CHAT_ID, text, message_thread_id=TOPIC_ID)

    await message.answer("Буст выполнен ✅")
    await send_rating()
    await state.clear()
