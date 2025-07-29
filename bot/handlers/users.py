import logging
import sqlite3 # Keep for direct DB interaction within handlers if needed, or refactor to db_utils
from datetime import datetime, timedelta

from aiogram import Bot, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardRemove
import aiogram.exceptions
# Correct import for InlineKeyboardBuilder
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiogram_calendar import SimpleCalendarCallback

from config import DATABASE_NAME, PAGE_SIZE, welcome_text
from db_utils import get_tasks_for_user, format_deadline
from keyboards.inline import (
    simple_calendar,
    get_main_menu_inline_keyboard,
    get_reminder_confirmation_keyboard,
    get_task_list_keyboard,
    build_task_selection_keyboard,
    build_complete_task_keyboard,
    build_edit_task_keyboard,
    build_delete_task_keyboard,
    build_reminders_keyboard,
    TaskListFilterCallback,
    TaskActionCallback,
    CompleteTaskCallback,
    EditTaskCallback,
    DeleteTaskCallback,
    MainMenuCallback,
    EnableReminderForTaskCallback,
    RemindersMenuCallback,
    RemoveTaskReminderCallback,
    DisableAllRemindersCallback
)
from states.admin_states import AddTask, EditTask, DeleteTask # Renamed for clarity in this context


welcome_router = Router()
task_router = Router()


# Вспомогательная функция для получения и отправки списка задач (при обычном просмотре)
async def send_task_list(target_message_or_query: types.Message | types.CallbackQuery, user_id: int,
                         task_limit: int = None, filter_type: str = None, status_filter: str = 'active'):
    tasks = get_tasks_for_user(user_id, filter_type=filter_type or "all", status_filter=status_filter)

    response = ""
    if not tasks:
        if status_filter == 'active':
            if filter_type == "today":
                response = "У вас нет активных задач на сегодня."
            elif filter_type == "week":
                response = "У вас нет активных задач на текущую неделю."
            elif filter_type == "month":
                response = "У вас нет активных задач на текущий месяц."
            else:
                response = "У вас пока нет активных задач."
        else:
            response = "У вас пока нет завершенных задач."
    else:
        response_header = ""
        if status_filter == 'active':
            if filter_type == "today":
                response_header = "🗓 Ваши активные задачи на сегодня:\n\n"
            elif filter_type == "week":
                response_header = "🗓 Ваши активные задачи на текущую неделю:\n\n"
            elif filter_type == "month":
                response_header = "🗓 Ваши активные задачи на текущий месяц:\n\n"
            elif task_limit:
                response_header = "📞 Ваши последние 5 активных задач:\n\n"
            elif filter_type == "all":
                response_header = "🗓 Ваши все активные задачи:\n\n"
        else:
            response_header = "🏆 Ваши завершенные задачи:\n\n"

        response = response_header
        selected_tasks = tasks if not task_limit else tasks[-5::1]
        for internal_id, task_number, description, deadline in selected_tasks:
            formatted_deadline = format_deadline(deadline)
            deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
            response += f"Номер: {task_number}.\n   Задача: {description}{deadline_str}\n"

    keyboard = get_task_list_keyboard(filter_type)

    if isinstance(target_message_or_query, types.Message):
        await target_message_or_query.answer(response, reply_markup=keyboard)
    elif isinstance(target_message_or_query, types.CallbackQuery):
        current_text = target_message_or_query.message.text or ""
        current_reply_markup = target_message_or_query.message.reply_markup

        def get_keyboard_data(markup: types.InlineKeyboardMarkup):
            if not markup:
                return None
            return [[button.model_dump() for button in row] for row in markup.inline_keyboard]

        new_keyboard_data = get_keyboard_data(keyboard)
        current_keyboard_data = get_keyboard_data(current_reply_markup)

        if response == current_text and new_keyboard_data == current_keyboard_data:
            logging.info("Skipping message edit: content and markup are identical.")
        else:
            try:
                await target_message_or_query.message.edit_text(
                    text=response,
                    reply_markup=keyboard
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    logging.info("Caught TelegramBadRequest: message not modified. Ignoring.")
                else:
                    raise e

# Обработчик команды /start
@welcome_router.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(welcome_text)

# Обработчик callback для возврата в главное меню
@task_router.callback_query(MainMenuCallback.filter())
async def process_main_menu_callback(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(welcome_text, reply_markup=None)
    await callback_query.answer()

# Обработчики добавления задачи
@task_router.message(Command("add_task"))
async def cmd_add_task(message: types.Message, state: FSMContext):
    builder = InlineKeyboardBuilder() # Corrected here
    builder.add(types.InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_add_task"))
    await message.answer("Отлично! Что нужно сделать? Опишите задачу.", reply_markup=builder.as_markup())
    await state.set_state(AddTask.waiting_for_description)

# Обработчик inline кнопки отмены при добавлении задачи
@task_router.callback_query(F.data == "cancel_add_task")
async def process_cancel_add_task(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer("Добавление задачи отменено", show_alert=False)
    await callback_query.message.answer(welcome_text)
    await callback_query.answer()

@task_router.message(AddTask.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите описание задачи текстом.")
        return
    await state.update_data(description=message.text)
    await message.answer("Теперь выберите срок выполнения (дедлайн) с помощью календаря:",
                         reply_markup=await simple_calendar.start_calendar())
    await state.set_state(AddTask.waiting_for_deadline)

@task_router.callback_query(SimpleCalendarCallback.filter(), AddTask.waiting_for_deadline)
async def process_add_deadline_calendar(callback_query: types.CallbackQuery, callback_data: SimpleCalendarCallback,
                                        state: FSMContext):
    selected, date = await simple_calendar.process_selection(callback_query, callback_data)
    if selected:
        user_id = callback_query.from_user.id
        data = await state.get_data()
        description = data['description']
        deadline_str = f"{date.strftime('%Y-%m-%d')}"

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        cursor.execute("SELECT MAX(task_number) FROM tasks WHERE user_id = ?", (user_id,))
        max_task_number = cursor.fetchone()[0]
        new_task_number = (max_task_number or 0) + 1

        cursor.execute("INSERT INTO tasks (user_id, task_number, description, deadline, status, remind_me) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, new_task_number, description, deadline_str, 'active', 0))
        internal_task_id = cursor.lastrowid
        conn.commit()

        if new_task_number == 1:
            cursor.execute("INSERT OR IGNORE INTO user_stats (user_id, completed_tasks_count) VALUES (?, 0)", (user_id,))
            conn.commit()
            await callback_query.message.answer("Поздравляем с вашей первой задачей! Спасибо что выбрали нас 😉",
                                                reply_markup=get_main_menu_inline_keyboard())

        conn.close()

        formatted_deadline_display = format_deadline(deadline_str)
        await callback_query.message.edit_text(
            f"✍ Задача '{description}' (Номер: {new_task_number}) со сроком выполнения '{formatted_deadline_display}' добавлена!")

        reminder_text = "Если хотите, чтобы я напомнил вам о задаче, жмите кнопку 👇"
        builder = InlineKeyboardBuilder() # Corrected here
        builder.add(types.InlineKeyboardButton(
            text="Напомнить о задаче",
            callback_data=EnableReminderForTaskCallback(task_internal_id=internal_task_id).pack()
        ))
        builder.add(types.InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MainMenuCallback().pack()
        ))
        builder.adjust(1)
        await callback_query.message.answer(reminder_text, reply_markup=builder.as_markup())

        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()

# Обработчик для включения напоминаний для конкретной задачи
@task_router.callback_query(EnableReminderForTaskCallback.filter())
async def process_enable_reminder_for_task_callback(callback_query: types.CallbackQuery, callback_data: EnableReminderForTaskCallback):
    task_id_to_remind = callback_data.task_internal_id
    user_id = callback_query.from_user.id

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE tasks SET remind_me = 1 WHERE id = ? AND user_id = ? AND status = 'active'",
                       (task_id_to_remind, user_id))
        conn.commit()

        cursor.execute("INSERT OR IGNORE INTO user_reminder_status (user_id, last_reminded_at) VALUES (?, ?)",
                       (user_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()

        await callback_query.message.edit_text(
            "Задача успешно добавлена в напоминание, я буду напоминать вам о незавершенных делах раз в час.",
            reply_markup=get_reminder_confirmation_keyboard()
        )
    except Exception as e:
        logging.error(f"Error enabling reminder for task {task_id_to_remind} by user {user_id}: {e}")
        await callback_query.message.edit_text("Произошла ошибка при включении напоминания.", reply_markup=get_main_menu_inline_keyboard())
    finally:
        conn.close()
    await callback_query.answer()

# Обработчик команды /reminders (для просмотра и управления напоминаниями)
@task_router.message(Command("reminders"))
async def cmd_reminders(message: types.Message):
    user_id = message.from_user.id
    remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)

    if not remindable_tasks:
        await message.answer("У вас пока нет задач, для которых включены напоминания.", reply_markup=get_main_menu_inline_keyboard())
        return

    keyboard = build_reminders_keyboard(remindable_tasks, page=0)
    await message.answer("🔔 Ваши задачи с напоминаниями (нажмите, чтобы убрать):", reply_markup=keyboard)

# Обработчик callback для меню напоминаний (пагинация)
@task_router.callback_query(RemindersMenuCallback.filter())
async def process_reminders_menu_callback(callback_query: types.CallbackQuery, callback_data: RemindersMenuCallback):
    user_id = callback_query.from_user.id
    current_page = callback_data.page

    remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)

    if not remindable_tasks and current_page == 0:
        await callback_query.message.edit_text("У вас больше нет задач с включенными напоминаниями.", reply_markup=get_main_menu_inline_keyboard())
        await callback_query.answer()
        return

    keyboard = build_reminders_keyboard(remindable_tasks, page=current_page)
    try:
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
    except aiogram.exceptions.TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e
    await callback_query.answer()

# Обработчик callback для удаления напоминания для конкретной задачи
@task_router.callback_query(RemoveTaskReminderCallback.filter())
async def process_remove_task_reminder_callback(callback_query: types.CallbackQuery, callback_data: RemoveTaskReminderCallback):
    task_id_to_remove_reminder = callback_data.task_internal_id
    current_page = callback_data.current_page
    user_id = callback_query.from_user.id

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE tasks SET remind_me = 0 WHERE id = ? AND user_id = ?",
                       (task_id_to_remove_reminder, user_id))
        conn.commit()
        await callback_query.answer("Напоминание по задаче отключено.", show_alert=False)

        remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)
        if not remindable_tasks:
            await callback_query.message.edit_text("У вас больше нет задач с включенными напоминаниями.", reply_markup=get_main_menu_inline_keyboard())
        else:
            keyboard = build_reminders_keyboard(remindable_tasks, page=current_page)
            try:
                await callback_query.message.edit_reply_markup(reply_markup=keyboard)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e

    except Exception as e:
        logging.error(f"Error removing reminder for task {task_id_to_remove_reminder} by user {user_id}: {e}")
        await callback_query.answer("Произошла ошибка при отключении напоминания.", show_alert=True)
        remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)
        keyboard = build_reminders_keyboard(remindable_tasks, page=current_page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest:
            pass
    finally:
        conn.close()

# Обработчик callback для отключения всех напоминаний
@task_router.callback_query(DisableAllRemindersCallback.filter())
async def process_disable_all_reminders_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE tasks SET remind_me = 0 WHERE user_id = ? AND status = 'active'", (user_id,))
        conn.commit()

        cursor.execute("DELETE FROM user_reminder_status WHERE user_id = ?", (user_id,))
        conn.commit()

        await callback_query.message.edit_text(
            "Все напоминания отключены. Вы можете включить их снова для конкретных задач при их добавлении или командой /reminders.",
            reply_markup=get_main_menu_inline_keyboard()
        )
    except Exception as e:
        logging.error(f"Error disabling all reminders for user {user_id}: {e}")
        await callback_query.message.edit_text("Произошла ошибка при отключении всех напоминаний.", reply_markup=get_main_menu_inline_keyboard())
    finally:
        conn.close()
    await callback_query.answer()

# Обработчик команды /list_tasks
@task_router.message(Command("list_tasks"))
async def cmd_list_tasks(message: types.Message):
    user_id = message.from_user.id
    await send_task_list(message, user_id, task_limit=1, status_filter='active', filter_type="all")

# Обработчик команды /history_tasks
@task_router.message(Command("history_tasks"))
async def cmd_history_tasks(message: types.Message):
    user_id = message.from_user.id
    await send_task_list(message, user_id, filter_type="history_all", status_filter='completed')

# Обработчик callback-запросов от кнопок фильтрации задач
@task_router.callback_query(TaskListFilterCallback.filter())
async def process_task_list_filter_callback(callback_query: types.CallbackQuery, callback_data: TaskListFilterCallback):
    user_id = callback_query.from_user.id
    filter_type = callback_data.filter_type

    status_filter = 'active'
    if filter_type == "history_all":
        status_filter = 'completed'

    await send_task_list(callback_query, user_id, filter_type=filter_type, status_filter=status_filter)

    if filter_type == "today":
        await callback_query.answer("Ваши задачи на сегодня", show_alert=False)
    elif filter_type == "week":
        await callback_query.answer("Ваши задачи на неделю", show_alert=False)
    elif filter_type == "month":
        await callback_query.answer("Ваши задачи на месяц", show_alert=False)
    elif filter_type == "all":
        await callback_query.answer("Все ваши задачи", show_alert=False)
    else:
        await callback_query.answer()

# Обработчик нажатия "Завершить задачу" с фильтром в callback
@task_router.callback_query(TaskActionCallback.filter())
async def process_complete_task_action(callback_query: types.CallbackQuery, callback_data: TaskActionCallback,
                                       state: FSMContext):
    action = callback_data.action
    if action.startswith("complete_task"):
        parts = action.split("_", 2)
        filter_type = parts[2] if len(parts) > 2 else "all"
        user_id = callback_query.from_user.id

        tasks = get_tasks_for_user(user_id, filter_type=filter_type, status_filter='active')

        if not tasks:
            await callback_query.answer("У вас нет активных задач для завершения.", show_alert=True)
            return

        keyboard = build_complete_task_keyboard(tasks, filter_type, page=0)

        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e

        await callback_query.answer()
    else:
        await callback_query.answer("Неизвестное действие.")

# Обработчик callback для завершения задачи / пагинации
@task_router.callback_query(CompleteTaskCallback.filter())
async def process_complete_task_callback(callback_query: types.CallbackQuery, callback_data: CompleteTaskCallback, state: FSMContext):
    user_id = callback_query.from_user.id
    filter_type = callback_data.filter_type
    page = callback_data.page
    selected_task_number = callback_data.task_number

    tasks = get_tasks_for_user(user_id, filter_type=filter_type, status_filter='active')

    if selected_task_number is not None:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT description FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
                           (user_id, selected_task_number))
            task_info = cursor.fetchone()
            if not task_info:
                await callback_query.answer("Задача не найдена или уже завершена.", show_alert=True)
                keyboard = build_complete_task_keyboard(tasks, filter_type, page)
                try:
                    await callback_query.message.edit_reply_markup(reply_markup=keyboard)
                except aiogram.exceptions.TelegramBadRequest as e:
                    if "message is not modified" not in str(e):
                        raise e
                return

            task_description = task_info[0]

            cursor.execute("UPDATE tasks SET status = 'completed', remind_me = 0 WHERE user_id = ? AND task_number = ? AND status = 'active'",
                           (user_id, selected_task_number))
            conn.commit()

            if cursor.rowcount > 0:
                cursor.execute("INSERT OR IGNORE INTO user_stats (user_id, completed_tasks_count) VALUES (?, 0)", (user_id,))
                cursor.execute("UPDATE user_stats SET completed_tasks_count = completed_tasks_count + 1 WHERE user_id = ?", (user_id,))
                conn.commit()

                cursor.execute("SELECT completed_tasks_count FROM user_stats WHERE user_id = ?", (user_id,))
                completed_tasks_count = cursor.fetchone()[0]

                congrats_message = ""
                if completed_tasks_count == 10:
                    congrats_message = "У вас уже 10 задач! Вероятно, вы на пути к идеальной продуктивности 🪷"
                elif completed_tasks_count == 100:
                    congrats_message = "У вас уже 100 задач! Дела идут в гору, а вы становитесь лучше чем вчера. Я прав? 👁"
                elif completed_tasks_count == 500:
                    congrats_message = "у вас целых 500 задач! Вы гуру продуктивности!🌓"
                elif completed_tasks_count == 1000:
                    congrats_message = "1000 завершенных задач - Вы настоящий бог продуктивности!🤞 🧘"

                await send_task_list(callback_query.message, user_id, filter_type=filter_type, status_filter='active')
                await callback_query.answer(f"Задача '{task_description}' (Номер: {selected_task_number}) завершена.")

                if congrats_message:
                    await callback_query.message.answer(congrats_message)
            else:
                await callback_query.answer("Не удалось завершить задачу.", show_alert=True)
        finally:
            conn.close()
    else:
        keyboard = build_complete_task_keyboard(tasks, filter_type, page=page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()

# Обработчики редактирования задачи
@task_router.message(Command("edit_task"))
async def cmd_edit_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active')

    if not tasks:
        await message.answer("У вас нет активных задач для редактирования.", reply_markup=get_main_menu_inline_keyboard())
        await state.clear()
        return

    keyboard = build_edit_task_keyboard(tasks, page=0)
    await message.answer("✏ Выберите задачу для редактирования:", reply_markup=keyboard)

@task_router.callback_query(EditTaskCallback.filter())
async def process_edit_task_callback(callback_query: types.CallbackQuery, callback_data: EditTaskCallback,
                                     state: FSMContext):
    user_id = callback_query.from_user.id
    tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active')

    if not tasks:
        await callback_query.message.edit_text("У вас нет активных задач для редактирования.",
                                               reply_markup=get_main_menu_inline_keyboard())
        await callback_query.answer()
        return

    if callback_data.action == "view":
        keyboard = build_edit_task_keyboard(tasks, page=callback_data.page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()
    elif callback_data.action == "select":
        selected_task_number = callback_data.task_number

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
            (user_id, selected_task_number))
        task = cursor.fetchone()
        conn.close()

        if not task:
            await callback_query.answer("Задача не найдена или уже завершена.", show_alert=True)
            keyboard = build_edit_task_keyboard(tasks, page=callback_data.page)
            try:
                await callback_query.message.edit_text(
                    "Задача не найдена или уже завершена. Выберите другую задачу или отмените.", reply_markup=keyboard)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e
            return

        internal_db_id = task[0]
        task_number_for_user = task[1]

        formatted_current_deadline_display = format_deadline(task[3])
        deadline_display = f"Срок выполнения: {formatted_current_deadline_display}" if formatted_current_deadline_display else "Срок выполнения: не указан"

        await state.update_data(editing_internal_db_id=internal_db_id, editing_task_number=task_number_for_user)
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Описание"), types.KeyboardButton(text="Срок выполнения")],
                [types.KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )

        await callback_query.message.delete()

        await callback_query.message.answer(
            f"Вы выбрали задачу с номером {task_number_for_user}.\nОписание: {task[2]}\n{deadline_display}\n\nЧто хотите изменить? ✏️",
            reply_markup=keyboard)
        await state.set_state(EditTask.waiting_for_new_data)
        await callback_query.answer()

@task_router.message(EditTask.waiting_for_new_data, F.text.in_({"Описание", "Срок выполнения", "Отмена"}))
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await message.answer("Редактирование отменено.",
                             reply_markup=get_main_menu_inline_keyboard())
        await state.clear()
        return

    if message.text == "Описание":
        await message.answer("Введите новое описание задачи:", reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(EditTask.waiting_for_new_description)
    elif message.text == "Срок выполнения":
        await message.answer("Выберите новый срок выполнения с помощью календаря:",
                             reply_markup=await simple_calendar.start_calendar())
        await state.set_state(EditTask.waiting_for_new_deadline)

@task_router.message(EditTask.waiting_for_new_description)
async def process_new_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите описание задачи текстом.",
                             reply_markup=get_main_menu_inline_keyboard())
        return

    data = await state.get_data()
    internal_db_id = data['editing_internal_db_id']
    task_number_for_user = data['editing_task_number']
    new_description = message.text

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET description = ? WHERE id = ? AND user_id = ? AND status = 'active'",
                   (new_description, internal_db_id, message.from_user.id))
    conn.commit()
    conn.close()

    if cursor.rowcount > 0:
        await message.answer(f"Описание задачи (Номер: {task_number_for_user}) обновлено на: '{new_description}'",
                             reply_markup=get_main_menu_inline_keyboard())
    else:
        await message.answer(
            "Не удалось обновить задачу. Возможно, задача не найдена, не принадлежит вам или неактивна.",
            reply_markup=get_main_menu_inline_keyboard())
    await state.clear()

@task_router.callback_query(SimpleCalendarCallback.filter(), EditTask.waiting_for_new_deadline)
async def process_edit_deadline_calendar(callback_query: types.CallbackQuery, callback_data: SimpleCalendarCallback,
                                         state: FSMContext):
    selected, date = await simple_calendar.process_selection(callback_query, callback_data)
    if selected:
        data = await state.get_data()
        internal_db_id = data['editing_internal_db_id']
        task_number_for_user = data['editing_task_number']
        user_id = callback_query.from_user.id
        deadline_str = f"{date.strftime('%Y-%m-%d')}"

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET deadline = ? WHERE id = ? AND user_id = ? AND status = 'active'",
                       (deadline_str, internal_db_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            formatted_deadline_display = format_deadline(deadline_str)
            await callback_query.message.edit_text(
                f"Срок выполнения задачи (Номер: {task_number_for_user}) обновлен на: '{formatted_deadline_display}'",
                reply_markup=get_main_menu_inline_keyboard())
        else:
            await callback_query.message.edit_text(
                "Не удалось обновить задачу. Возможно, задача не найдена, не принадлежит вам или неактивна.",
                reply_markup=get_main_menu_inline_keyboard())
        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()

# Обработчики удаления задачи
@task_router.message(Command("delete_task"))
async def cmd_delete_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active')

    if not tasks:
        await message.answer("У вас нет активных задач для удаления.", reply_markup=get_main_menu_inline_keyboard())
        await state.clear()
        return

    keyboard = build_delete_task_keyboard(tasks, page=0)
    await message.answer("🗑 Выберите задачу для удаления:", reply_markup=keyboard)

@task_router.callback_query(DeleteTaskCallback.filter())
async def process_delete_task_callback(callback_query: types.CallbackQuery, callback_data: DeleteTaskCallback, state: FSMContext):
    user_id = callback_query.from_user.id
    tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active')

    if not tasks:
        await callback_query.message.edit_text("У вас нет активных задач для удаления.", reply_markup=get_main_menu_inline_keyboard())
        await callback_query.answer()
        return

    if callback_data.action == "view":
        keyboard = build_delete_task_keyboard(tasks, page=callback_data.page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()
    elif callback_data.action == "select":
        selected_task_number = callback_data.task_number

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
            (user_id, selected_task_number))
        task = cursor.fetchone()
        conn.close()

        if not task:
            await callback_query.answer("Задача не найдена или уже завершена.", show_alert=True)
            keyboard = build_delete_task_keyboard(tasks, page=callback_data.page)
            try:
                await callback_query.message.edit_text("Задача не найдена или уже завершена. Выберите другую задачу или отмените.", reply_markup=keyboard)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e
            return

        internal_db_id = task[0]
        task_number_for_user = task[1]
        task_description = task[2]

        await state.update_data(deleting_internal_db_id=internal_db_id, deleting_task_number=task_number_for_user,
                                deleting_task_desc=task_description)

        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Да"), types.KeyboardButton(text="Нет")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback_query.message.delete()

        await callback_query.message.answer(
            f"👁 Вы уверены, что хотите удалить задачу (Номер: {task_number_for_user}): '{task_description}'? (Да/Нет)",
            reply_markup=keyboard)
        await state.set_state(DeleteTask.waiting_for_confirmation)
        await callback_query.answer()

@task_router.message(DeleteTask.waiting_for_confirmation, F.text.in_({"Да", "Нет"}))
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    if message.text == "Да":
        data = await state.get_data()
        internal_db_id = data['deleting_internal_db_id']
        task_number_for_user = data['deleting_task_number']
        task_description = data['deleting_task_desc']
        user_id = message.from_user.id

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ? AND user_id = ? AND status = 'active'",
                       (internal_db_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            await message.answer(f"Задача '{task_description}' (Номер: {task_number_for_user}) успешно удалена.",
                                 reply_markup=get_main_menu_inline_keyboard())
        else:
            await message.answer(
                "Не удалось удалить задачу. Возможно, задача уже была удалена, не принадлежит вам или неактивна.",
                reply_markup=get_main_menu_inline_keyboard())
    else:
        await message.answer("Удаление отменено.", reply_markup=get_main_menu_inline_keyboard())
    await state.clear()

