from aiogram import types
# Correct import for InlineKeyboardBuilder
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

from config import PAGE_SIZE
from db_utils import format_deadline

simple_calendar = SimpleCalendar()

class TaskListFilterCallback(CallbackData, prefix="task_filter"):
    filter_type: str

class TaskActionCallback(CallbackData, prefix="task_action"):
    action: str

class CompleteTaskCallback(CallbackData, prefix="complete_task"):
    filter_type: str
    page: int = 0
    task_number: int | None = None

class EditTaskCallback(CallbackData, prefix="edit_task"):
    page: int = 0
    task_number: int | None = None
    action: str = "view"

class DeleteTaskCallback(CallbackData, prefix="delete_task"):
    page: int = 0
    task_number: int | None = None
    action: str = "view"

class MainMenuCallback(CallbackData, prefix="main_menu"):
    action: str = "show"

class EnableReminderForTaskCallback(CallbackData, prefix="enable_task_rem"):
    task_internal_id: int

class RemindersMenuCallback(CallbackData, prefix="rem_menu"):
    page: int = 0
    action: str = "view"

class RemoveTaskReminderCallback(CallbackData, prefix="remove_task_rem"):
    task_internal_id: int
    current_page: int = 0

class DisableAllRemindersCallback(CallbackData, prefix="disable_all_rem"):
    pass

def get_main_menu_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🏠 Главное меню", callback_data=MainMenuCallback().pack()))
    return builder.as_markup()

def get_reminder_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="Все напоминания",
        callback_data=RemindersMenuCallback(page=0, action="view").pack()
    ))
    builder.row(types.InlineKeyboardButton(
        text="🏠 Главное меню",
        callback_data=MainMenuCallback().pack()
    ))
    return builder.as_markup()

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

def build_task_selection_keyboard(tasks, callback_constructor, page=0):
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_tasks = tasks[start:end]

    if not page_tasks and page > 0:
        return build_task_selection_keyboard(tasks, callback_constructor, page - 1)
    elif not page_tasks:
        builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data=MainMenuCallback().pack()))
        return builder.as_markup()

    for internal_id, task_number, description, deadline in page_tasks:
        formatted_deadline = format_deadline(deadline)
        deadline_str = f" ({formatted_deadline})" if formatted_deadline else ""
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

    for internal_id, task_number, description, deadline in page_tasks:
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

    for internal_id, task_number, description, deadline in page_tasks:
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

