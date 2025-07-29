import sqlite3
from datetime import datetime, timedelta
import logging

from config import DATABASE_NAME

# Настройка базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Создаем таблицу задач, если она не существует
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

    # Создаем таблицу для статуса напоминаний пользователя (для контроля частоты)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_reminder_status (
            user_id INTEGER PRIMARY KEY,
            last_reminded_at TEXT -- Время последнего напоминания в формате YYYY-MM-DD HH:MM:SS
        )
    ''')
    conn.commit()

    # Создаем таблицу для статистики пользователя (счетчик завершенных задач)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            completed_tasks_count INTEGER DEFAULT 0
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

