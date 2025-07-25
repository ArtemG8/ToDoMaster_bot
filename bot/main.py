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

    # Создаем таблицу задач, если она не существует
    #Добавляем столбец remind_me
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_number INTEGER,
            description TEXT NOT NULL,
            deadline TEXT,
            status TEXT DEFAULT 'active',
            remind_me INTEGER DEFAULT 0 -- 0: не напоминать, 1: напоминать
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

    # Проверяем и добавляем столбец remind_me, если его нет
    if 'remind_me' not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN remind_me INTEGER DEFAULT 0;")
        conn.commit()
        # Обновляем существующие задачи, чтобы remind_me по умолчанию был 0
        cursor.execute("UPDATE tasks SET remind_me = 0 WHERE remind_me IS NULL;")
        conn.commit()

    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_task_number ON tasks (user_id, task_number);")
        conn.commit()
    except sqlite3.OperationalError as e:
        logging.warning(
            f"Could not create unique index 'idx_user_task_number': {e}. Please check your database for duplicate (user_id, task_number) pairs if this warning persists.")

    #  Создаем таблицу для статуса напоминаний пользователя (для контроля частоты)
    # Это заменит предыдущую таблицу `reminders`
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_reminder_status (
            user_id INTEGER PRIMARY KEY,
            last_reminded_at TEXT -- Время последнего напоминания в формате YYYY-MM-DD HH:MM:SS
        )
    ''')
    conn.commit()
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
    waiting_for_new_data = State()
    waiting_for_new_description = State()
    waiting_for_new_deadline = State()


class DeleteTask(StatesGroup):
    waiting_for_confirmation = State()


# роутеры
welcome_router = Router()
task_router = Router()

PAGE_SIZE = 5  # Кол-во задач на странице для пагинации


class TaskListFilterCallback(CallbackData, prefix="task_filter"):
    filter_type: str  # 'today', 'week', 'month', 'all', 'history_all'


class TaskActionCallback(CallbackData, prefix="task_action"):
    action: str  # например, 'complete_task_today', 'complete_task_week' и т.д.


class CompleteTaskCallback(CallbackData, prefix="complete_task"):
    filter_type: str  # 'today', 'week', 'month', 'all'
    page: int = 0  # страница отображения при пагинации
    task_number: int | None = None  # номер задачи для завершения (None — просто просмотр списка)

# Новые CallbackData для редактирования и удаления задач
class EditTaskCallback(CallbackData, prefix="edit_task"):
    page: int = 0
    task_number: int | None = None
    action: str = "view"  # 'view' for pagination, 'select' for selecting a task

class DeleteTaskCallback(CallbackData, prefix="delete_task"):
    page: int = 0
    task_number: int | None = None
    action: str = "view"  # 'view' for pagination, 'select' for selecting a task


# Новая CallbackData для возврата в главное меню
class MainMenuCallback(CallbackData, prefix="main_menu"):
    action: str = "show"

#  CallbackData для включения напоминаний для конкретной задачи
class EnableReminderForTaskCallback(CallbackData, prefix="enable_task_rem"):
    task_internal_id: int

#  CallbackData для меню напоминаний
class RemindersMenuCallback(CallbackData, prefix="rem_menu"):
    page: int = 0
    action: str = "view" # 'view', 'remove_task_reminder', 'disable_all'

#  CallbackData для удаления напоминания для конкретной задачи
class RemoveTaskReminderCallback(CallbackData, prefix="remove_task_rem"):
    task_internal_id: int
    current_page: int = 0 # Для возврата на ту же страницу после удаления

#  CallbackData для отключения всех напоминаний
class DisableAllRemindersCallback(CallbackData, prefix="disable_all_rem"):
    pass


# Новая функция для генерации инлайн-клавиатуры "Главное меню"
def get_main_menu_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🏠 Главное меню", callback_data=MainMenuCallback().pack()))
    return builder.as_markup()

# НОВАЯ ФУНКЦИЯ: Клавиатура для сообщения об успешном добавлении в напоминания
def get_reminder_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Все напоминания",
        callback_data=RemindersMenuCallback(page=0, action="view").pack()
    ))
    builder.add(types.InlineKeyboardButton(
        text="🏠 Главное меню",
        callback_data=MainMenuCallback().pack()
    ))
    builder.adjust(1) # Кнопки в один столбец
    return builder.as_markup()


@task_router.callback_query(MainMenuCallback.filter())
async def process_main_menu_callback(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(welcome_text, reply_markup=None)
    await callback_query.answer()

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


# Формируем клавиатуру с номерами задач для выбора (с пагинацией)
def build_task_selection_keyboard(tasks, callback_constructor, page=0):
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_tasks = tasks[start:end]

    # Handle case where the current page becomes empty after an action (e.g., deletion)
    if not page_tasks and page > 0:
        # Try to navigate to the previous page
        return build_task_selection_keyboard(tasks, callback_constructor, page - 1)
    elif not page_tasks: # No tasks at all or on first page with no tasks
        builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data=MainMenuCallback().pack()))
        return builder.as_markup()

    for task_number, description, deadline in page_tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" ({formatted_deadline})" if formatted_deadline else ""
        # Shorten description for button text if too long
        button_text = f"{task_number}. {description[:30]}{'...' if len(description) > 30 else ''}{deadline_str}"

        builder.row(types.InlineKeyboardButton(
            text=button_text,
            callback_data=callback_constructor(page=page, task_number=task_number, action="select").pack()
        ))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=callback_constructor(page=page - 1, action="view").pack()
        ))
    if end < len(tasks):
        nav_buttons.append(types.InlineKeyboardButton(
            text="Вперед ➡️",
            callback_data=callback_constructor(page=page + 1, action="view").pack()
        ))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(types.InlineKeyboardButton(
        text="❌ Отмена",
        callback_data=MainMenuCallback().pack()
    ))
    return builder.as_markup()


def build_complete_task_keyboard(tasks, filter_type, page=0):
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_tasks = tasks[start:end]

    if not page_tasks and page > 0:
        return build_complete_task_keyboard(tasks, filter_type, page - 1)
    elif not page_tasks:
        builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data=TaskListFilterCallback(filter_type=filter_type).pack()))
        return builder.as_markup()


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
        callback_data=TaskListFilterCallback(filter_type=filter_type).pack()
    ))
    return builder.as_markup()


def build_edit_task_keyboard(tasks, page=0):
    return build_task_selection_keyboard(tasks, EditTaskCallback, page)

def build_delete_task_keyboard(tasks, page=0):
    return build_task_selection_keyboard(tasks, DeleteTaskCallback, page)

#  Функция для построения клавиатуры напоминаний
def build_reminders_keyboard(tasks, page=0):
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_tasks = tasks[start:end]

    if not page_tasks and page > 0:
        return build_reminders_keyboard(tasks, page - 1)
    elif not page_tasks:
        builder.row(types.InlineKeyboardButton(text="🏠 Главное меню", callback_data=MainMenuCallback().pack()))
        return builder.as_markup()

    for internal_id, task_number, description, deadline in page_tasks: # Получаем internal_id
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" ({formatted_deadline})" if formatted_deadline else ""
        button_text = f"✅ {task_number}. {description[:30]}{'...' if len(description) > 30 else ''}{deadline_str}"

        builder.row(types.InlineKeyboardButton(
            text=button_text,
            callback_data=RemoveTaskReminderCallback(task_internal_id=internal_id, current_page=page).pack()
        ))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=RemindersMenuCallback(page=page - 1, action="view").pack()
        ))
    if end < len(tasks):
        nav_buttons.append(types.InlineKeyboardButton(
            text="Вперед ➡️",
            callback_data=RemindersMenuCallback(page=page + 1, action="view").pack()
        ))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(types.InlineKeyboardButton(
        text="❌ Отключить все напоминания",
        callback_data=DisableAllRemindersCallback().pack()
    ))
    builder.row(types.InlineKeyboardButton(
        text="🏠 Главное меню",
        callback_data=MainMenuCallback().pack()
    ))
    return builder.as_markup()


# Вспомогательная функция получения задач с фильтром и статусом
def get_tasks_for_user(user_id: int, filter_type: str, status_filter: str = 'active', remind_me_filter: bool = None):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    query = "SELECT id, task_number, description, deadline FROM tasks WHERE user_id = ? AND status = ?"
    params = [user_id, status_filter]

    if remind_me_filter is not None:
        query += " AND remind_me = ?"
        params.append(1 if remind_me_filter else 0)

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
                response_header = "📞 Ваши последние 5 активных задач:\n\n"
            elif filter_type == "all":
                response_header = "🗓 Ваши все активные задачи:\n\n"
        else:
            response_header = "🏆 Ваши завершенные задачи:\n\n"

        response = response_header
        selected_tasks = tasks if not task_limit else tasks[-5::1]
        # Изменяем извлечение, так как get_tasks_for_user теперь возвращает id
        for internal_id, task_number, description, deadline in selected_tasks:
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

        # Вставляем remind_me = 0 по умолчанию
        cursor.execute("INSERT INTO tasks (user_id, task_number, description, deadline, status, remind_me) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, new_task_number, description, deadline_str, 'active', 0))
        internal_task_id = cursor.lastrowid # Получаем внутренний ID только что добавленной задачи
        conn.commit()
        conn.close()

        formatted_deadline_display = format_deadline(deadline_str)
        # Отправляем начальное сообщение без кнопок напоминания, затем добавляем кнопки
        await callback_query.message.edit_text(
            f"✍ Задача '{description}' (Номер: {new_task_number}) со сроком выполнения '{formatted_deadline_display}' добавлена!")

        #Предложение включить напоминания для КОНКРЕТНОЙ задачи (только одна кнопка)
        reminder_text = "Если хотите, чтобы я напомнил вам о задаче, жмите кнопку 👇"
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(
            text="Напомнить о задаче",
            callback_data=EnableReminderForTaskCallback(task_internal_id=internal_task_id).pack()
        ))
        builder.add(types.InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MainMenuCallback().pack()
        ))
        builder.adjust(1)  # Размещаем кнопки в один столбец для лучшего вида
        await callback_query.message.answer(reminder_text, reply_markup=builder.as_markup())

        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()

#Обработчик для включения напоминаний для конкретной задачи
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

        # Также убедимся, что пользователь есть в таблице user_reminder_status для фоновых проверок
        cursor.execute("INSERT OR IGNORE INTO user_reminder_status (user_id, last_reminded_at) VALUES (?, ?)",
                       (user_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()

        await callback_query.message.edit_text(
            "Задача успешно добавлена в напоминание, я буду напоминать вам о незавершенных делах раз в час.",
            reply_markup=get_reminder_confirmation_keyboard() # ИСПОЛЬЗУЕМ НОВУЮ ФУНКЦИЮ ДЛЯ КЛАВИАТУРЫ
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
    # Получаем задачи, для которых включено напоминание
    remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)

    if not remindable_tasks:
        await message.answer("У вас пока нет задач, для которых включены напоминания.", reply_markup=get_main_menu_inline_keyboard())
        return

    keyboard = build_reminders_keyboard(remindable_tasks, page=0)
    await message.answer("🔔 Ваши задачи с напоминаниями (нажмите, чтобы убрать):", reply_markup=keyboard)


#  Обработчик callback для меню напоминаний (пагинация)
@task_router.callback_query(RemindersMenuCallback.filter())
async def process_reminders_menu_callback(callback_query: types.CallbackQuery, callback_data: RemindersMenuCallback):
    user_id = callback_query.from_user.id
    current_page = callback_data.page

    remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)

    if not remindable_tasks and current_page == 0: # Если задач больше нет и это первая страница
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


#Обработчик callback для удаления напоминания для конкретной задачи
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

        # Обновляем список напоминаний
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
        # Если произошла ошибка, можно попробовать обновить клавиатуру
        remindable_tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active', remind_me_filter=True)
        keyboard = build_reminders_keyboard(remindable_tasks, page=current_page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest:
            pass # Игнорируем, если сообщение не изменилось
    finally:
        conn.close()


# Обработчик callback для отключения всех напоминаний
@task_router.callback_query(DisableAllRemindersCallback.filter())
async def process_disable_all_reminders_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        # Отключаем напоминания для всех активных задач пользователя
        cursor.execute("UPDATE tasks SET remind_me = 0 WHERE user_id = ? AND status = 'active'", (user_id,))
        conn.commit()

        # Удаляем запись пользователя из таблицы user_reminder_status, чтобы он больше не получал напоминаний
        cursor.execute("DELETE FROM user_reminder_status WHERE user_id = ?", (user_id,))
        conn.commit()

        await callback_query.message.edit_text(
            "Все напоминания отключены. Вы можете включить их снова для конкретных задач при их добавлении или командой /reminders.", # Указываем команду /reminders
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


# Обработчик callback для завершения задачи / пагинации
@task_router.callback_query(CompleteTaskCallback.filter())
async def process_complete_task_callback(callback_query: types.CallbackQuery, callback_data: CompleteTaskCallback, state: FSMContext):
    user_id = callback_query.from_user.id
    filter_type = callback_data.filter_type
    page = callback_data.page
    selected_task_number = callback_data.task_number

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
            # Если задача не найдена, обновляем список
            keyboard = build_complete_task_keyboard(tasks, filter_type, page)
            try:
                await callback_query.message.edit_reply_markup(reply_markup=keyboard)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e
            return
        task_description = task_info[0]

        cursor.execute("UPDATE tasks SET status = 'completed', remind_me = 0 WHERE user_id = ? AND task_number = ? AND status = 'active'", #  remind_me = 0 при завершении
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
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()


#  Обработчики редактирования задачи
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
    # State is not set here, it will be set by the callback handler once a task is selected.


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
        # Handle pagination
        keyboard = build_edit_task_keyboard(tasks, page=callback_data.page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()
    elif callback_data.action == "select":
        # User selected a task
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

        # Определяем deadline_display здесь
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

        await callback_query.message.delete()  # Удаляет сообщение, на котором были кнопки выбора

        # Отправляем новое сообщение с ReplyKeyboardMarkup
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
    # State is not set here, it will be set by the callback handler once a task is selected.


@task_router.callback_query(DeleteTaskCallback.filter())
async def process_delete_task_callback(callback_query: types.CallbackQuery, callback_data: DeleteTaskCallback, state: FSMContext):
    user_id = callback_query.from_user.id
    tasks = get_tasks_for_user(user_id, filter_type="all", status_filter='active')

    if not tasks:
        await callback_query.message.edit_text("У вас нет активных задач для удаления.", reply_markup=get_main_menu_inline_keyboard())
        await callback_query.answer()
        return

    if callback_data.action == "view":
        # Handle pagination
        keyboard = build_delete_task_keyboard(tasks, page=callback_data.page)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await callback_query.answer()
    elif callback_data.action == "select":
        # User selected a task
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
        await callback_query.message.delete() # Удаляет сообщение, на котором были кнопки выбора

        # Отправляем новое сообщение с ReplyKeyboardMarkup
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
        #  При удалении задачи, напоминания по ней тоже отключаются.
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


#  Фоновая задача для отправки напоминаний
async def send_hourly_reminders(bot: Bot):
    while True:
        await asyncio.sleep(3600)  # Ждем 1 час (3600 секунд)
        logging.info("Running hourly reminders check...")
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        # Получаем user_id всех пользователей, у которых есть хоть одна задача с remind_me = 1
        # И где время последнего напоминания (или его отсутствие) указывает, что пора напомнить
        cursor.execute("""
            SELECT DISTINCT t.user_id, urs.last_reminded_at
            FROM tasks t
            JOIN user_reminder_status urs ON t.user_id = urs.user_id
            WHERE t.remind_me = 1 AND t.status = 'active'
        """)
        users_to_check = cursor.fetchall()

        current_time = datetime.now()

        for user_id, last_reminded_str in users_to_check:
            should_remind = False
            if not last_reminded_str:
                should_remind = True  # Если никогда не напоминали, то напоминаем
            else:
                last_reminded_dt = datetime.strptime(last_reminded_str, '%Y-%m-%d %H:%M:%S')
                # Если с последнего напоминания прошел час или более
                if (current_time - last_reminded_dt) >= timedelta(hours=1):
                    should_remind = True

            if should_remind:
                # Получаем количество активных задач на СЕГОДНЯ, для которых включено напоминание
                today_date_str = current_time.strftime('%Y-%m-%d')
                cursor.execute("""
                    SELECT COUNT(*) FROM tasks
                    WHERE user_id = ? AND status = 'active' AND deadline = ? AND remind_me = 1
                """, (user_id, today_date_str))
                active_today_remindable_task_count = cursor.fetchone()[0]

                if active_today_remindable_task_count > 0:
                    reminder_message = f"Привет! На сегодня у тебя {active_today_remindable_task_count} незавершенных задач, по которым я должен напомнить!"
                    builder = InlineKeyboardBuilder()
                    builder.add(types.InlineKeyboardButton(
                        text="Посмотреть задачи",
                        callback_data=TaskListFilterCallback(filter_type="today").pack() # Кнопка для просмотра задач на сегодня
                    ))
                    try:
                        await bot.send_message(chat_id=user_id, text=reminder_message, reply_markup=builder.as_markup())
                        # Обновляем время последнего напоминания в user_reminder_status
                        cursor.execute("UPDATE user_reminder_status SET last_reminded_at = ? WHERE user_id = ?",
                                       (current_time.strftime('%Y-%m-%d %H:%M:%S'), user_id))
                        conn.commit()
                        logging.info(f"Reminder sent to user {user_id} for {active_today_remindable_task_count} today's remindable tasks.")
                    except aiogram.exceptions.TelegramForbiddenError:
                        logging.warning(f"Bot blocked by user {user_id}. Removing from user_reminder_status and setting remind_me=0 for their tasks.")
                        # Удаляем пользователя из таблицы user_reminder_status, если он заблокировал бота
                        cursor.execute("DELETE FROM user_reminder_status WHERE user_id = ?", (user_id,))
                        # Отключаем все напоминания для этого пользователя
                        cursor.execute("UPDATE tasks SET remind_me = 0 WHERE user_id = ?", (user_id,))
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Error sending reminder to user {user_id}: {e}")
        conn.close()


# Главная функция запуска бота
async def main():
    init_db()
    dp.include_router(welcome_router)
    dp.include_router(task_router)
    #  Запускаем фоновую задачу напоминаний
    asyncio.create_task(send_hourly_reminders(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    print("Бот запускается... Нажмите Ctrl+C для остановки.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")

