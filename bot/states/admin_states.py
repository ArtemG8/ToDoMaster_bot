from aiogram.fsm.state import State, StatesGroup

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

