"""FSM-состояния для диалогов редактирования и перегенерации черновика."""
from aiogram.fsm.state import State, StatesGroup


class DraftEditing(StatesGroup):
    waiting_for_manual_text = State()
    waiting_for_regen_note = State()
