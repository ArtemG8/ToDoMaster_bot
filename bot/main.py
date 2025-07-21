import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData

from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

simple_calendar = SimpleCalendar()

# Конфигурация
TOKEN = "7860468847:AAG1fHL18lU0Rpnq6ey81vv1vWLRWg7frbQ"
DATABASE_NAME = 'todo.db'

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Включаем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')


#  Настройка базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Создаем таблицу, если она не существует
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_number INTEGER,
            description TEXT NOT NULL,
            deadline TEXT,
            status TEXT DEFAULT 'active' -- Добавляем новый столбец status
        )
    ''')
    conn.commit()

    # Проверяем и добавляем столбец task_number, если его нет
    cursor.execute("PRAGMA table_info(tasks)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'task_number' not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN task_number INTEGER;")
        conn.commit()
        # Заполняем task_number для существующих записей
        cursor.execute("SELECT DISTINCT user_id FROM tasks")
        user_ids = cursor.fetchall()
        for user_id_tuple in user_ids:
            user_id = user_id_tuple[0]
            cursor.execute("SELECT id FROM tasks WHERE user_id = ? ORDER BY id", (user_id,))
            user_tasks = cursor.fetchall()
            for i, task_id_tuple in enumerate(user_tasks):
                task_id = task_id_tuple[0]
                cursor.execute("UPDATE tasks SET task_number = ? WHERE id = ?", (i + 1, task_id))
            conn.commit()

    # Проверяем и добавляем столбец status, если его нет
    if 'status' not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'active';")
        conn.commit()
        # Обновляем существующие задачи, чтобы они имели статус 'active'
        cursor.execute("UPDATE tasks SET status = 'active' WHERE status IS NULL;")
        conn.commit()

    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_task_number ON tasks (user_id, task_number);")
        conn.commit()
    except sqlite3.OperationalError as e:
        logging.warning(
            f"Could not create unique index 'idx_user_task_number': {e}. Please check your database for duplicate (user_id, task_number) pairs if this warning persists.")

    conn.close()


# Форматирование дедлайна
def format_deadline(deadline_str):
    if not deadline_str:
        return ""
    try:
        dt_object = datetime.strptime(deadline_str, '%Y-%m-%d')
        months = [
            "", "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря"
        ]
        day = dt_object.day
        month_name = months[dt_object.month]
        current_year = datetime.now().year
        if dt_object.year == current_year:
            return f"{day} {month_name}"
        else:
            return f"{day} {month_name} {dt_object.year}"
    except ValueError:
        return deadline_str


# Состояния для FSM
class AddTask(StatesGroup):
    waiting_for_description = State()
    waiting_for_deadline = State()


class EditTask(StatesGroup):
    waiting_for_task_number = State()
    waiting_for_new_data = State()
    waiting_for_new_description = State()
    waiting_for_new_deadline = State()


class DeleteTask(StatesGroup):
    waiting_for_task_number = State()
    waiting_for_confirmation = State()


# НОВОЕ СОСТОЯНИЕ ДЛЯ ЗАВЕРШЕНИЯ ЗАДАЧИ
class CompleteTask(StatesGroup):
    waiting_for_task_number = State()


# роутеры
welcome_router = Router()
task_router = Router()


# Обработчик команды /start
@welcome_router.message(Command("start"))
async def start_command(message: types.Message):
    welcome_text = """
Привет! Я твой личный ToDo бот!👋

Я создан, чтобы помочь тебе эффективно управлять своими задачами и значительно повысить твою продуктивность. Со мной ты сможешь легко:

   ✍ Записывать все свои дела — от самых мелких заметок до крупных проектов.
   ⏰ Устанавливать сроки выполнения для каждой задачи, чтобы ничего не упустить.
   🔔 Настраивать таймеры и напоминания, чтобы вовремя приступать к работе и успевать в срок.

Забудь о забытых задачах и невыполненных планах!
Начнем прямо сейчас?

Для добавления задачи используй /add_task
Для просмотра задач используй /list_tasks
Для редактирования задачи используй /edit_task
Для удаления задачи используй /delete_task
"""
    await message.answer(welcome_text)


# Обработчики добавления задачи
@task_router.message(Command("add_task"))
async def cmd_add_task(message: types.Message, state: FSMContext):
    await message.answer("Отлично! Что нужно сделать? Опишите задачу.", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(AddTask.waiting_for_description)


@task_router.message(AddTask.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите описание задачи текстом.")
        return
    await state.update_data(description=message.text)
    await message.answer("Теперь выберите срок выполнения (дедлайн) с помощью календаря:",
                         reply_markup=await simple_calendar.start_calendar())
    await state.set_state(AddTask.waiting_for_deadline)


# Обработчик выбора даты из календаря для добавления задачи
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

        # Добавляем статус 'active' при вставке новой задачи
        cursor.execute("INSERT INTO tasks (user_id, task_number, description, deadline, status) VALUES (?, ?, ?, ?, ?)",
                       (user_id, new_task_number, description, deadline_str, 'active'))
        conn.commit()
        conn.close()

        formatted_deadline_display = format_deadline(deadline_str)
        await callback_query.message.edit_text(
            f"Задача '{description}' (Номер: {new_task_number}) со сроком выполнения '{formatted_deadline_display}' добавлена!\nХотите ещё? /add_task \nПосмотреть все задачи: /list_tasks")
        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()


# --- ОБНОВЛЕННЫЙ ФУНКЦИОНАЛ ПРОСМОТРА ЗАДАЧ И ЗАВЕРШЕНИЯ ---

# Класс для обработки CallbackData от инлайн-кнопок фильтрации задач
class TaskListFilterCallback(CallbackData, prefix="task_filter"):
    filter: str  # 'today', 'week', 'month', 'all', 'history_all'


# Класс для обработки CallbackData от кнопок действий
class TaskActionCallback(CallbackData, prefix="task_action"):
    action: str  # 'complete_task'


# Функция для создания инлайн-клавиатуры с кнопками фильтрации и завершения
def get_task_list_keyboard(current_filter: str = None):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Задачи на сегодня",
        callback_data=TaskListFilterCallback(filter="today").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="На неделю",
        callback_data=TaskListFilterCallback(filter="week").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="На месяц",
        callback_data=TaskListFilterCallback(filter="month").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="Посмотреть все",
        callback_data=TaskListFilterCallback(filter="all").pack()
    ))
    # Добавляем кнопку "Завершить задачу" только для активных задач
    if current_filter != "history_all":  # Убедимся, что эта кнопка не появляется в истории
        builder.add(types.InlineKeyboardButton(
            text="Завершить задачу",
            callback_data=TaskActionCallback(action="complete_task").pack()
        ))
    builder.add(types.InlineKeyboardButton(
        text="История задач",
        callback_data=TaskListFilterCallback(filter="history_all").pack()
    ))
    builder.adjust(2)
    return builder.as_markup()


# Вспомогательная функция для получения и отправки списка задач
async def send_task_list(target_message_or_query: types.Message | types.CallbackQuery, user_id: int,
                         task_limit: int = None, filter_type: str = None, status_filter: str = 'active'):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    query = "SELECT task_number, description, deadline FROM tasks WHERE user_id = ? AND status = ?"
    params = [user_id, status_filter]

    current_date = datetime.now()

    if filter_type == "today":
        query += " AND deadline = ?"
        params.append(current_date.strftime('%Y-%m-%d'))
    elif filter_type == "week":
        start_of_week = current_date - timedelta(days=current_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        query += " AND deadline BETWEEN ? AND ?"
        params.extend([start_of_week.strftime('%Y-%m-%d'), end_of_week.strftime('%Y-%m-%d')])
    elif filter_type == "month":
        query += " AND strftime('%Y-%m', deadline) = strftime('%Y-%m', ?)"
        params.append(current_date.strftime('%Y-%m-%d'))

    query += " ORDER BY task_number"
    if task_limit:
        query += f" LIMIT {task_limit}"

    cursor.execute(query, tuple(params))
    tasks = cursor.fetchall()
    conn.close()

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
        else:  # status_filter == 'completed'
            response = "У вас пока нет завершенных задач."
    else:
        response_header = ""
        if status_filter == 'active':
            if filter_type == "today":
                response_header = "Ваши активные задачи на сегодня:\n\n"
            elif filter_type == "week":
                response_header = "Ваши активные задачи на текущую неделю:\n\n"
            elif filter_type == "month":
                response_header = "Ваши активные задачи на текущий месяц:\n\n"
            elif task_limit:
                response_header = "Ваши первые 5 активных задач:\n\n"
            else:
                response_header = "Ваши все активные задачи:\n\n"
        else:  # status_filter == 'completed'
            response_header = "Ваши завершенные задачи:\n\n"

        response = response_header
        for task_number, description, deadline in tasks:
            formatted_deadline = format_deadline(deadline)
            deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
            response += f"Номер: {task_number}.\n   Задача: {description}{deadline_str}\n"

    keyboard = get_task_list_keyboard(filter_type)  # Передаем текущий фильтр для определения кнопок

    if isinstance(target_message_or_query, types.Message):
        await target_message_or_query.answer(response, reply_markup=keyboard)
    elif isinstance(target_message_or_query, types.CallbackQuery):
        await target_message_or_query.message.edit_text(
            text=response,
            reply_markup=keyboard
        )


# Обработчик команды /list_tasks
@task_router.message(Command("list_tasks"))
async def cmd_list_tasks(message: types.Message):
    user_id = message.from_user.id
    await send_task_list(message, user_id, task_limit=5, status_filter='active')


# Обработчик команды /history_tasks
@task_router.message(Command("history_tasks"))
async def cmd_history_tasks(message: types.Message):
    user_id = message.from_user.id
    # Отправляем все завершенные задачи
    await send_task_list(message, user_id, filter_type="history_all", status_filter='completed')


# Обработчик callback-запросов от кнопок фильтрации задач
@task_router.callback_query(TaskListFilterCallback.filter())
async def process_task_list_filter_callback(callback_query: types.CallbackQuery, callback_data: TaskListFilterCallback):
    user_id = callback_query.from_user.id
    filter_type = callback_data.filter

    status_filter = 'active'
    if filter_type == "history_all":
        status_filter = 'completed'

    await send_task_list(callback_query, user_id, filter_type=filter_type, status_filter=status_filter)

    await callback_query.answer()


# ОБРАБОТЧИК: Нажатие кнопки "Завершить задачу"
@task_router.callback_query(TaskActionCallback.filter())  # Исправлено: без аргумента action здесь
async def process_complete_task_action(callback_query: types.CallbackQuery, callback_data: TaskActionCallback,
                                       state: FSMContext):
    if callback_data.action == "complete_task":  # Проверяем action внутри обработчика
        user_id = callback_query.from_user.id

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task_number, description, deadline FROM tasks WHERE user_id = ? AND status = 'active' ORDER BY task_number",
            (user_id,))
        tasks = cursor.fetchall()
        conn.close()

        if not tasks:
            await callback_query.message.answer("У вас нет активных задач для завершения.")
            await callback_query.answer()
            return

        response = "Выберите задачу для завершения, указав её номер:\n\n"
        for task_number, description, deadline in tasks:
            formatted_deadline = format_deadline(deadline)
            deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
            response += f"Номер: {task_number}\n   Задача: {description}{deadline_str}\n"
        response += "\nВведите номер задачи, которую хотите завершить."

        await callback_query.message.answer(response, reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(CompleteTask.waiting_for_task_number)
        await callback_query.answer()
    else:
        await callback_query.answer("Неизвестное действие.")


# ОБРАБОТЧИК: Получение номера задачи для завершения
@task_router.message(CompleteTask.waiting_for_task_number)
async def process_complete_task_number(message: types.Message, state: FSMContext):
    try:
        user_provided_task_number = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой номер задачи.")
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT description FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
                   (user_id, user_provided_task_number))
    task_info = cursor.fetchone()

    if not task_info:
        await message.answer(
            "Активная задача с таким номером не найдена или не принадлежит вам. Пожалуйста, введите корректный номер.")
        conn.close()
        return

    task_description = task_info[0]

    # Обновляем статус задачи на 'completed' вместо удаления
    cursor.execute("UPDATE tasks SET status = 'completed' WHERE user_id = ? AND task_number = ? AND status = 'active'",
                   (user_id, user_provided_task_number))
    conn.commit()

    if cursor.rowcount > 0:
        await message.answer(
            f"Задача '{task_description}' (Номер: {user_provided_task_number}) успешно завершена и перемещена в историю.")
        await send_task_list(message, user_id, task_limit=5,
                             status_filter='active')  # Показываем обновленный список активных задач
    else:
        await message.answer("Не удалось завершить задачу. Возможно, задача уже была завершена или не найдена.")

    conn.close()
    await state.clear()


#  Обработчики редактирования задачи
@task_router.message(Command("edit_task"))
async def cmd_edit_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Выбираем только активные задачи для редактирования
    cursor.execute(
        "SELECT task_number, description, deadline FROM tasks WHERE user_id = ? AND status = 'active' ORDER BY task_number",
        (user_id,))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("У вас нет активных задач для редактирования.")
        await state.clear()
        return

    response = "Выберите задачу для редактирования, указав её номер:\n\n"
    for task_number, description, deadline in tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
        response += f"Номер: {task_number}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите номер задачи, которую хотите изменить."

    await message.answer(response, reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(EditTask.waiting_for_task_number)


@task_router.message(EditTask.waiting_for_task_number)
async def process_edit_task_number(message: types.Message, state: FSMContext):
    try:
        user_provided_task_number = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой номер задачи.")
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Ищем активную задачу по user_id и user_provided_task_number
    cursor.execute(
        "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
        (user_id, user_provided_task_number))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await message.answer(
            "Активная задача с таким номером не найдена или не принадлежит вам. Пожалуйста, введите корректный номер.")
        return

    internal_db_id = task[0]
    task_number_for_user = task[1]

    await state.update_data(editing_internal_db_id=internal_db_id, editing_task_number=task_number_for_user)
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Описание"), types.KeyboardButton(text="Срок выполнения")],
            [types.KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    formatted_current_deadline_display = format_deadline(task[3])
    deadline_display = f"Срок выполнения: {formatted_current_deadline_display}" if formatted_current_deadline_display else "Срок выполнения: не указан"

    await message.answer(
        f"Вы выбрали задачу:\nНомер: {task_number_for_user}\nЗадача: {task[2]}\n{deadline_display}\n\nЧто хотите изменить?",
        reply_markup=keyboard)
    await state.set_state(EditTask.waiting_for_new_data)


@task_router.message(EditTask.waiting_for_new_data, F.text.in_({"Описание", "Срок выполнения", "Отмена"}))
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await message.answer("Редактирование отменено.", reply_markup=types.ReplyKeyboardRemove())
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
        await message.answer("Пожалуйста, введите описание задачи текстом.")
        return

    data = await state.get_data()
    internal_db_id = data['editing_internal_db_id']
    task_number_for_user = data['editing_task_number']
    new_description = message.text

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Обновляем описание только для активных задач
    cursor.execute("UPDATE tasks SET description = ? WHERE id = ? AND user_id = ? AND status = 'active'",
                   (new_description, internal_db_id, message.from_user.id))
    conn.commit()
    conn.close()

    if cursor.rowcount > 0:
        await message.answer(f"Описание задачи (Номер: {task_number_for_user}) обновлено на: '{new_description}'")
    else:
        await message.answer(
            "Не удалось обновить задачу. Возможно, задача не найдена, не принадлежит вам или неактивна.")
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
        # Обновляем дедлайн только для активных задач
        cursor.execute("UPDATE tasks SET deadline = ? WHERE id = ? AND user_id = ? AND status = 'active'",
                       (deadline_str, internal_db_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            formatted_deadline_display = format_deadline(deadline_str)
            await callback_query.message.edit_text(
                f"Срок выполнения задачи (Номер: {task_number_for_user}) обновлен на: '{formatted_deadline_display}'")
        else:
            await callback_query.message.edit_text(
                "Не удалось обновить задачу. Возможно, задача не найдена, не принадлежит вам или неактивна.")
        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()


# Обработчики удаления задачи
@task_router.message(Command("delete_task"))
async def cmd_delete_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Выбираем только активные задачи для удаления
    cursor.execute(
        "SELECT task_number, description, deadline FROM tasks WHERE user_id = ? AND status = 'active' ORDER BY task_number",
        (user_id,))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("У вас нет активных задач для удаления.")
        await state.clear()
        return

    response = "Выберите задачу для удаления, указав её номер:\n\n"
    for task_number, description, deadline in tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
        response += f"Номер: {task_number}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите номер задачи, которую хотите удалить."

    await message.answer(response)
    await state.set_state(DeleteTask.waiting_for_task_number)


@task_router.message(DeleteTask.waiting_for_task_number)
async def process_delete_task_number(message: types.Message, state: FSMContext):
    try:
        user_provided_task_number = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой номер задачи.")
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Ищем активную задачу по user_id и user_provided_task_number
    cursor.execute(
        "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
        (user_id, user_provided_task_number))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await message.answer(
            "Активная задача с таким номером не найдена или не принадлежит вам. Пожалуйста, введите корректный номер.")
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
    await message.answer(
        f"Вы уверены, что хотите удалить задачу (Номер: {task_number_for_user}): '{task_description}'? (Да/Нет)",
        reply_markup=keyboard)
    await state.set_state(DeleteTask.waiting_for_confirmation)


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
        # Удаляем только активную задачу
        cursor.execute("DELETE FROM tasks WHERE id = ? AND user_id = ? AND status = 'active'",
                       (internal_db_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            await message.answer(f"Задача '{task_description}' (Номер: {task_number_for_user}) успешно удалена.",
                                 reply_markup=types.ReplyKeyboardRemove())
        else:
            await message.answer(
                "Не удалось удалить задачу. Возможно, задача уже была удалена, не принадлежит вам или неактивна.",
                reply_markup=types.ReplyKeyboardRemove())
    else:
        await message.answer("Удаление отменено.", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


# Главная функция запуска бота
async def main():
    init_db()
    dp.include_router(welcome_router)
    dp.include_router(task_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    print("Бот запускается... Нажмите Ctrl+C для остановки.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")

