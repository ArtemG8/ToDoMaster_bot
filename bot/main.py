import asyncio
import logging
import sqlite3
from dotenv import load_dotenv
import os
load_dotenv()
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
import aiogram.exceptions

from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

simple_calendar = SimpleCalendar()

# Конфигурация
DATABASE_NAME = 'todo.db'
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

# Инициализация бота и диспетчера
bot = Bot(os.getenv("TOKEN"))
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


class CompleteTask(StatesGroup):
    waiting_for_task_number = State()


# роутеры
welcome_router = Router()
task_router = Router()

# --- НОВЫЕ CALLBACKDATA И КОНСТАНТЫ ДЛЯ ЗАВЕРШЕНИЯ ЗАДАЧ ---

PAGE_SIZE = 5  # Кол-во задач на странице для пагинации


class TaskListFilterCallback(CallbackData, prefix="task_filter"):
    filter_type: str  # 'today', 'week', 'month', 'all', 'history_all'


class TaskActionCallback(CallbackData, prefix="task_action"):
    action: str  # например, 'complete_task_today', 'complete_task_week' и т.д.


class CompleteTaskCallback(CallbackData, prefix="complete_task"):
    filter_type: str  # 'today', 'week', 'month', 'all'
    page: int = 0  # страница отображения при пагинации
    task_number: int | None = None  # номер задачи для завершения (None — просто просмотр списка)


# Главная клавиатура с кнопками фильтров и кнопкой "Завершить задачу"
def get_task_list_keyboard(current_filter: str = None):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Задачи на сегодня",
        callback_data=TaskListFilterCallback(filter_type="today").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="На неделю",
        callback_data=TaskListFilterCallback(filter_type="week").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="На месяц",
        callback_data=TaskListFilterCallback(filter_type="month").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="Посмотреть все",
        callback_data=TaskListFilterCallback(filter_type="all").pack()
    ))
    if current_filter != "history_all":
        # Кнопка завершения задачи с передачей текущего фильтра (по умолчанию all)
        builder.add(types.InlineKeyboardButton(
            text="Завершить задачу",
            callback_data=TaskActionCallback(action="complete_task_" + (current_filter or "all")).pack()
        ))
    builder.add(types.InlineKeyboardButton(
        text="История задач",
        callback_data=TaskListFilterCallback(filter_type="history_all").pack()
    ))
    builder.adjust(2)
    return builder.as_markup()


# Формируем клавиатуру с номерами задач для завершения (с пагинацией)
def build_complete_task_keyboard(tasks, filter_type, page=0):
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_tasks = tasks[start:end]

    for task_number, description, deadline in page_tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" ✅({formatted_deadline})" if formatted_deadline else ""
        button_text = f"{task_number}{deadline_str}"

        builder.row(types.InlineKeyboardButton(
            text=button_text,
            callback_data=CompleteTaskCallback(filter_type=filter_type, page=page, task_number=task_number).pack()
        ))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=CompleteTaskCallback(filter_type=filter_type, page=page - 1).pack()
        ))
    if end < len(tasks):
        nav_buttons.append(types.InlineKeyboardButton(
            text="Вперед ➡️",
            callback_data=CompleteTaskCallback(filter_type=filter_type, page=page + 1).pack()
        ))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(types.InlineKeyboardButton(
        text="❌ Отменить завершение",
        callback_data="cancel_complete"
    ))
    return builder.as_markup()


# Вспомогательная функция получения задач с фильтром и статусом
def get_tasks_for_user(user_id: int, filter_type: str, status_filter: str = 'active'):
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
    cursor.execute(query, tuple(params))
    tasks = cursor.fetchall()
    conn.close()
    return tasks


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
                response_header = "Ваши первые 5 активных задач:\n\n"
            elif filter_type == "all":
                response_header = "Ваши все активные задачи:\n\n"
        else:
            response_header = "Ваши завершенные задачи:\n\n"

        response = response_header
        selected_tasks = tasks if not task_limit else tasks[:task_limit]
        for task_number, description, deadline in selected_tasks:
            formatted_deadline = format_deadline(deadline)
            deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
            response += f"Номер: {task_number}.\n   Задача: {description}{deadline_str}\n"

    keyboard = get_task_list_keyboard(filter_type)  # Передаем текущий фильтр

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


# Обработчики добавления задачи
@task_router.message(Command("add_task"))
async def cmd_add_task(message: types.Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_add_task"))
    await message.answer("Отлично! Что нужно сделать? Опишите задачу.", reply_markup=builder.as_markup())
    await state.set_state(AddTask.waiting_for_description)

# Обработчик inline кнопки отмены при добавлении задачи
@task_router.callback_query(F.data == "cancel_add_task")
async def process_cancel_add_task(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    # Изменяем сообщение, на котором была нажата кнопка
    await callback_query.answer("Добавление задачи отменено", show_alert=False)


    # Отправляем сообщение главного меню
    await callback_query.message.answer(welcome_text)
    await callback_query.answer()  # Отвечаем на callback-запрос, чтобы убрать "часики"

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

        cursor.execute("INSERT INTO tasks (user_id, task_number, description, deadline, status) VALUES (?, ?, ?, ?, ?)",
                       (user_id, new_task_number, description, deadline_str, 'active'))
        conn.commit()
        conn.close()

        formatted_deadline_display = format_deadline(deadline_str)
        await callback_query.message.edit_text(
            f"Задача '{description}' (Номер: {new_task_number}) со сроком выполнения '{formatted_deadline_display}' добавлена!\n"
            f"Хотите ещё? /add_task \nПосмотреть все задачи: /list_tasks")
        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()


# Обработчик команды /list_tasks
@task_router.message(Command("list_tasks"))
async def cmd_list_tasks(message: types.Message):
    user_id = message.from_user.id
    await send_task_list(message, user_id, task_limit=5, status_filter='active', filter_type="all")


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

    #Уведомление при фильтрах
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

        # Вместо изменения текста сообщения, просто меняем клавиатуру на кнопку выбора задач для завершения
        keyboard = build_complete_task_keyboard(tasks, filter_type, page=0)

        # Обновляем реквизиты — оставляем текст без изменений, меняем только клавиатуру
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            # Если клавиатура такая же, игнорируем
            if "message is not modified" not in str(e):
                raise e

        await callback_query.answer()
    else:
        await callback_query.answer("Неизвестное действие.")


# Обработчик callback для завершения задачи / пагинации / отмены завершения
@task_router.callback_query(lambda c: c.data and (c.data.startswith("complete_task") or c.data == "cancel_complete"))
async def process_complete_task_callback(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data
    user_id = callback_query.from_user.id

    if data == "cancel_complete":
        # При отмене показываем исходный список задач с фильтром, не сообщение "Отменено завершение"
        # Чтобы сохранить текущий фильтр, определим его по клавиатуре или по дефолту
        # Но проще сохранить фильтр в data (это можно усложнить), пока вернем дефолт all
        # Если хотите, можно попробовать парсить callback_query.message.reply_markup, но проще универсально:
        # Покажем все активные задачи (filter_type='all')
        await send_task_list(callback_query.message, user_id, filter_type="all", status_filter='active')
        await callback_query.answer("Завершение задачи отменено.")
        return

    try:
        cb = CompleteTaskCallback.unpack(data)
    except Exception:
        await callback_query.answer("Ошибка. Попробуйте снова.", show_alert=True)
        return

    filter_type = cb.filter_type
    page = cb.page
    selected_task_number = cb.task_number

    tasks = get_tasks_for_user(user_id, filter_type=filter_type, status_filter='active')

    if selected_task_number is not None:
        # Пользователь выбрал задачу для завершения
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT description FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
                       (user_id, selected_task_number))
        task_info = cursor.fetchone()
        if not task_info:
            await callback_query.answer("Задача не найдена или уже завершена.", show_alert=True)
            conn.close()
            return
        task_description = task_info[0]

        cursor.execute("UPDATE tasks SET status = 'completed' WHERE user_id = ? AND task_number = ? AND status = 'active'",
                       (user_id, selected_task_number))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            # Обновляем и показываем оригинальный список задач с кнопками
            await send_task_list(callback_query.message, user_id, filter_type=filter_type, status_filter='active')
            await callback_query.answer(f"Задача '{task_description}' (Номер: {selected_task_number}) завершена.")
        else:
            await callback_query.answer("Не удалось завершить задачу.", show_alert=True)
    else:
        # Переход по страницам пагинации
        keyboard = build_complete_task_keyboard(tasks, filter_type, page=page)
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer()


#  Обработчики редактирования задачи
@task_router.message(Command("edit_task"))
async def cmd_edit_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
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
    cursor.execute(
        "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND task_number = ? AND status = 'active'",
        (user_id, user_provided_task_number))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await message.answer(
            "Активная задача с таким номером не найдена. Пожалуйста, введите корректный номер.")
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
