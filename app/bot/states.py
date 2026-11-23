"""FSM state groups."""

from aiogram.fsm.state import State, StatesGroup


class CaptchaState(StatesGroup):
    waiting = State()


class ReportForm(StatesGroup):
    offender = State()
    reporter = State()
    happened_at = State()
    description = State()
    evidence = State()
    confirm = State()


class ApplicationForm(StatesGroup):
    age = State()
    nickname = State()
    experience = State()
    hours_week = State()
    timezone = State()
    microphone = State()
    portfolio = State()
    confirm = State()


class DonationForm(StatesGroup):
    nickname = State()
    telegram = State()
    screenshot = State()
    package = State()
    confirm = State()
    new_receipt = State()


class ConsoleForm(StatesGroup):
    argument = State()


class PluginSearch(StatesGroup):
    waiting = State()


class FaqAnswer(StatesGroup):
    waiting = State()


class BroadcastForm(StatesGroup):
    text = State()
    confirm = State()


class HelpForm(StatesGroup):
    admin_prompt = State()
