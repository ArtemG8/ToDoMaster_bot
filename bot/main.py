import asyncio
import logging
import sqlite3  # Keep for send_hourly_reminders direct DB access
from aiogram import Bot, Dispatcher, types
import aiogram.exceptions
from datetime import datetime, timedelta

from config import TOKEN, welcome_text
from db_utils import init_db, get_tasks_for_user
from handlers.users import welcome_router, task_router
from keyboards.inline import TaskListFilterCallback

# Инициализация бота и диспетчера
bot = Bot(TOKEN)
dp = Dispatcher()

# Включаем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')


# Фоновая задача для отправки напоминаний
async def send_hourly_reminders(bot: Bot):
    while True:
        await asyncio.sleep(3600)  # Ждем 1 час (3600 секунд)
        logging.info("Running hourly reminders check...")
        conn = sqlite3.connect('todo.db')  # Directly use 'todo.db' as it's a known constant
        cursor = conn.cursor()

        # Получаем user_id всех пользователей, у которых есть хоть одна задача с remind_me = 1
        # И где время последнего напоминания (или его отсутствие) указывает, что пора напомнить
        cursor.execute("""
            SELECT DISTINCT t.user_id, urs.last_reminded_at, COALESCE(urs.interval_hours, 1) as interval_hours
            FROM tasks t
            JOIN user_reminder_status urs ON t.user_id = urs.user_id
            WHERE t.remind_me = 1 AND t.status = 'active'
        """)
        users_to_check = cursor.fetchall()

        current_time = datetime.now()

        for user_id, last_reminded_str, interval_hours in users_to_check:
            should_remind = False
            if not last_reminded_str:
                should_remind = True  # Если никогда не напоминали, то напоминаем
            else:
                last_reminded_dt = datetime.strptime(last_reminded_str, '%Y-%m-%d %H:%M:%S')
                # Если с последнего напоминания прошел указанный интервал или более
                if (current_time - last_reminded_dt) >= timedelta(hours=interval_hours or 1):
                    should_remind = True

            if should_remind:
                # Получаем количество активных задач на СЕГОДНЯ, для которых включено напоминание
                today_date_str = current_time.strftime('%Y-%m-%d')

                # Re-fetch tasks using get_tasks_for_user from db_utils
                # We need to filter for tasks with remind_me = 1 explicitly here
                conn_inner = sqlite3.connect('todo.db')  # Separate connection for this specific query
                cursor_inner = conn_inner.cursor()
                cursor_inner.execute("""
                    SELECT COUNT(*) FROM tasks
                    WHERE user_id = ? AND status = 'active' AND deadline = ? AND remind_me = 1
                """, (user_id, today_date_str))
                active_today_remindable_task_count = cursor_inner.fetchone()[0]
                conn_inner.close()

                if active_today_remindable_task_count > 0:
                    reminder_message = f"Привет! На сегодня у тебя {active_today_remindable_task_count} незавершенных задач, по которым я должен напомнить!"
                    builder = types.InlineKeyboardBuilder()
                    builder.add(types.InlineKeyboardButton(
                        text="Посмотреть задачи",
                        callback_data=TaskListFilterCallback(filter_type="today").pack()
                        # Кнопка для просмотра задач на сегодня
                    ))
                    try:
                        await bot.send_message(chat_id=user_id, text=reminder_message, reply_markup=builder.as_markup())
                        # Обновляем время последнего напоминания в user_reminder_status
                        cursor.execute("UPDATE user_reminder_status SET last_reminded_at = ? WHERE user_id = ?",
                                       (current_time.strftime('%Y-%m-%d %H:%M:%S'), user_id))
                        conn.commit()
                        logging.info(
                            f"Reminder sent to user {user_id} for {active_today_remindable_task_count} today's remindable tasks.")
                    except aiogram.exceptions.TelegramForbiddenError:
                        logging.warning(
                            f"Bot blocked by user {user_id}. Removing from user_reminder_status and setting remind_me=0 for their tasks.")
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
    # Запускаем фоновую задачу напоминаний
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

