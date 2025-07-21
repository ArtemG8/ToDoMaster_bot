import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Импортируем календарь
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            deadline TEXT
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
    waiting_for_task_id = State()
    waiting_for_new_data = State()
    waiting_for_new_description = State()
    waiting_for_new_deadline = State()

class DeleteTask(StatesGroup):
    waiting_for_task_id = State()
    waiting_for_confirmation = State()

#роутеры
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
    await message.answer("Отлично! Что нужно сделать? Опишите задачу.")
    await state.set_state(AddTask.waiting_for_description)


@task_router.message(AddTask.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите описание задачи текстом.")
        return
    await state.update_data(description=message.text)
    # Вместо текстового ввода дедлайна, отправляем календарь
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
        cursor.execute("INSERT INTO tasks (user_id, description, deadline) VALUES (?, ?, ?)",
                       (user_id, description, deadline_str))
        conn.commit()
        conn.close()

        formatted_deadline_display = format_deadline(deadline_str)
        await callback_query.message.edit_text(
            f"Задача '{description}' со сроком выполнения '{formatted_deadline_display}' добавлена!")
        await state.clear()
        await callback_query.answer()
    else:
        await callback_query.answer()


# Обработчик просмотра задач
@task_router.message(Command("list_tasks"))
async def cmd_list_tasks(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, description, deadline FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("У вас пока нет активных задач.")
        return

    response = "Ваши текущие задачи:\n\n"
    for i, task in enumerate(tasks):
        task_id, description, deadline = task
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
        response += f"{task_id}.\n   Задача: {description}{deadline_str}\n"

    await message.answer(response)


#  Обработчики редактирования задачи
@task_router.message(Command("edit_task"))
async def cmd_edit_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, description, deadline FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("У вас нет задач для редактирования.")
        await state.clear()
        return

    response = "Выберите задачу для редактирования, указав её номер:\n\n"
    for task_id, description, deadline in tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
        response += f"Номер: {task_id}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите номер задачи, которую хотите изменить."

    await message.answer(response)
    await state.set_state(EditTask.waiting_for_task_id)


@task_router.message(EditTask.waiting_for_task_id)
async def process_edit_task_id(message: types.Message, state: FSMContext):
    try:
        task_id = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой ID задачи.")
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, description, deadline FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await message.answer("Задача с таким ID не найдена или не принадлежит вам. Пожалуйста, введите корректный ID.")
        return

    await state.update_data(editing_task_id=task_id)
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Описание"), types.KeyboardButton(text="Срок выполнения")],
            [types.KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    formatted_current_deadline_display = format_deadline(task[2])
    deadline_display = f"Срок выполнения: {formatted_current_deadline_display}" if formatted_current_deadline_display else "Срок выполнения: не указан"

    await message.answer(
        f"Вы выбрали задачу:\nНомер: {task[0]}\nЗадача: {task[1]}\n{deadline_display}\n\nЧто хотите изменить?",
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
        # Отправляем календарь для выбора нового дедлайна
        await message.answer("Выберите новый срок выполнения с помощью календаря:",
                             reply_markup=await simple_calendar.start_calendar())
        await state.set_state(EditTask.waiting_for_new_deadline)


@task_router.message(EditTask.waiting_for_new_description)
async def process_new_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите описание задачи текстом.")
        return

    data = await state.get_data()
    task_id = data['editing_task_id']
    new_description = message.text

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET description = ? WHERE id = ? AND user_id = ?",
                   (new_description, task_id, message.from_user.id))
    conn.commit()
    conn.close()

    if cursor.rowcount > 0:
        await message.answer(f"Описание задачи (Номер: {task_id}) обновлено на: '{new_description}'")
    else:
        await message.answer("Не удалось обновить задачу. Возможно, задача не найдена или не принадлежит вам.")
    await state.clear()


# Обработчик выбора даты из календаря для редактирования задачи
@task_router.callback_query(SimpleCalendarCallback.filter(), EditTask.waiting_for_new_deadline)
async def process_edit_deadline_calendar(callback_query: types.CallbackQuery, callback_data: SimpleCalendarCallback,
                                         state: FSMContext):
    selected, date = await simple_calendar.process_selection(callback_query, callback_data)
    if selected:
        data = await state.get_data()
        task_id = data['editing_task_id']
        user_id = callback_query.from_user.id
        deadline_str = f"{date.strftime('%Y-%m-%d')}"

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET deadline = ? WHERE id = ? AND user_id = ?",
                       (deadline_str, task_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            formatted_deadline_display = format_deadline(deadline_str)
            await callback_query.message.edit_text(
                f"Срок выполнения задачи (Номер: {task_id}) обновлен на: '{formatted_deadline_display}'")
        else:
            await callback_query.message.edit_text(
                "Не удалось обновить задачу. Возможно, задача не найдена или не принадлежит вам.")
        await state.clear()
        await callback_query.answer()
    else:
        # Продолжаем ждать выбор даты, если пользователь не выбрал
        await callback_query.answer()


# Обработчики удаления задачи
@task_router.message(Command("delete_task"))
async def cmd_delete_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, description, deadline FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("У вас нет задач для удаления.")
        await state.clear()
        return

    response = "Выберите задачу для удаления, указав её номер:\n\n"
    for task_id, description, deadline in tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" (Срок выполнения: {formatted_deadline})" if formatted_deadline else ""
        response += f"Номер: {task_id}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите номер задачи, которую хотите удалить."

    await message.answer(response)
    await state.set_state(DeleteTask.waiting_for_task_id)


@task_router.message(DeleteTask.waiting_for_task_id)
async def process_delete_task_id(message: types.Message, state: FSMContext):
    try:
        task_id = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой ID задачи.")
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, description, deadline FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await message.answer("Задача с таким номером не найдена или не принадлежит вам. Пожалуйста, введите корректный номер.")
        return

    await state.update_data(deleting_task_id=task_id, deleting_task_desc=task[1])

    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Да"), types.KeyboardButton(text="Нет")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(f"Вы уверены, что хотите удалить задачу (Номер: {task[0]}): '{task[1]}'? (Да/Нет)",
                         reply_markup=keyboard)
    await state.set_state(DeleteTask.waiting_for_confirmation)


@task_router.message(DeleteTask.waiting_for_confirmation, F.text.in_({"Да", "Нет"}))
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    if message.text == "Да":
        data = await state.get_data()
        task_id = data['deleting_task_id']
        task_description = data['deleting_task_desc']
        user_id = message.from_user.id

        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            await message.answer(f"Задача '{task_description}' (Номер: {task_id}) успешно удалена.",
                                 reply_markup=types.ReplyKeyboardRemove())
        else:
            await message.answer("Не удалось удалить задачу. Возможно, задача уже была удалена или не принадлежит вам.",
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
