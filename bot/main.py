import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = "7860468847:AAG1fHL18lU0Rpnq6ey81vv1vWLRWg7frbQ"
DATABASE_NAME = 'todo.db'

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Включаем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# Настройка базы данных
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

# Роутеры
welcome_router = Router()
task_router = Router()

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
    await message.answer("Теперь укажите срок выполнения (дедлайн) в формате ГГГГ-ММ-ДД ЧЧ:ММ (например, 2025-12-31 18:00). Если дедлайн не нужен, напишите 'нет'.")
    await state.set_state(AddTask.waiting_for_deadline)

@task_router.message(AddTask.waiting_for_deadline)
async def process_deadline(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    description = data['description']
    deadline = message.text.strip()

    if deadline.lower() == 'нет':
        deadline = None
    else:
        try:
            # пробуем распарсить дедлайн для проверки формата
            datetime.strptime(deadline, '%Y-%m-%d %H:%M')
        except ValueError:
            await message.answer("Неверный формат даты/времени. Пожалуйста, используйте ГГГГ-ММ-ДД ЧЧ:ММ или напишите 'нет'.")
            return

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (user_id, description, deadline) VALUES (?, ?, ?)",
                   (user_id, description, deadline))
    conn.commit()
    conn.close()

    await message.answer(f"Задача '{description}' с дедлайном '{deadline if deadline else 'не указан'}' добавлена!")
    await state.clear()

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
        deadline_str = f" (Дедлайн: {deadline})" if deadline else ""
        response += f"{i+1}. ID: {task_id}\n   Задача: {description}{deadline_str}\n"

    await message.answer(response)

# Обработчики редактирования задачи
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

    response = "Выберите задачу для редактирования, указав её ID:\n\n"
    for task_id, description, deadline in tasks:
        deadline_str = f" (Дедлайн: {deadline})" if deadline else ""
        response += f"ID: {task_id}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите ID задачи, которую хотите изменить."

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
            [types.KeyboardButton(text="Описание"), types.KeyboardButton(text="Дедлайн")],
            [types.KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(f"Вы выбрали задачу:\nID: {task[0]}\nЗадача: {task[1]}\nДедлайн: {task[2] if task[2] else 'не указан'}\n\nЧто хотите изменить?", reply_markup=keyboard)
    await state.set_state(EditTask.waiting_for_new_data)

@task_router.message(EditTask.waiting_for_new_data, F.text.in_({"Описание", "Дедлайн", "Отмена"}))
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await message.answer("Редактирование отменено.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    if message.text == "Описание":
        await message.answer("Введите новое описание задачи:", reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(EditTask.waiting_for_new_description)
    elif message.text == "Дедлайн":
        await message.answer("Введите новый дедлайн в формате ГГГГ-ММ-ДД ЧЧ:ММ (например, 2025-12-31 18:00). Если дедлайн не нужен, напишите 'нет'.", reply_markup=types.ReplyKeyboardRemove())
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
        await message.answer(f"Описание задачи (ID: {task_id}) обновлено на: '{new_description}'")
    else:
        await message.answer("Не удалось обновить задачу. Возможно, задача не найдена или не принадлежит вам.")
    await state.clear()

@task_router.message(EditTask.waiting_for_new_deadline)
async def process_new_deadline(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data['editing_task_id']
    new_deadline = message.text.strip()

    if new_deadline.lower() == 'нет':
        new_deadline = None
    else:
        try:
            datetime.strptime(new_deadline, '%Y-%m-%d %H:%M')
        except ValueError:
            await message.answer("Неверный формат даты/времени. Пожалуйста, используйте ГГГГ-ММ-ДД ЧЧ:ММ или напишите 'нет'.")
            return

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET deadline = ? WHERE id = ? AND user_id = ?",
                   (new_deadline, task_id, message.from_user.id))
    conn.commit()
    conn.close()

    if cursor.rowcount > 0:
        await message.answer(f"Дедлайн задачи (ID: {task_id}) обновлен на: '{new_deadline if new_deadline else 'не указан'}'")
    else:
        await message.answer("Не удалось обновить задачу. Возможно, задача не найдена или не принадлежит вам.")
    await state.clear()

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

    response = "Выберите задачу для удаления, указав её ID:\n\n"
    for task_id, description, deadline in tasks:
        deadline_str = f" (Дедлайн: {deadline})" if deadline else ""
        response += f"ID: {task_id}\n   Задача: {description}{deadline_str}\n"
    response += "\nВведите ID задачи, которую хотите удалить."

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
        await message.answer("Задача с таким ID не найдена или не принадлежит вам. Пожалуйста, введите корректный ID.")
        return

    await state.update_data(deleting_task_id=task_id, deleting_task_desc=task[1])

    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Да"), types.KeyboardButton(text="Нет")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(f"Вы уверены, что хотите удалить задачу (ID: {task[0]}): '{task[1]}'? (Да/Нет)", reply_markup=keyboard)
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
            await message.answer(f"Задача '{task_description}' (ID: {task_id}) успешно удалена.", reply_markup=types.ReplyKeyboardRemove())
        else:
            await message.answer("Не удалось удалить задачу. Возможно, задача уже была удалена или не принадлежит вам.", reply_markup=types.ReplyKeyboardRemove())
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

