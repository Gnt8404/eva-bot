"""Проверка обязательной подписки на канал и группу Aetherlyn."""
from . import config


def links_text():
    return ("Чтобы пользоваться Евой, подпишись на официальный канал Aetherlyn:\n"
            "• Канал: %s\n\n"
            "После подписки напиши /start ещё раз."
            % config.CHANNEL_INVITE_URL)


def _status(tg, chat_id, user_id):
    try:
        res = tg.call("getChatMember", chat_id=chat_id, user_id=user_id)
        return ((res or {}).get("result") or {}).get("status") or "left"
    except Exception:
        return "left"


def subscribed(tg, user_id):
    """Подписан ли пользователь на канал и группу (не left/kicked)."""
    if not getattr(config, "SUB_CHECK_ON", True):
        return True
    chat_id = getattr(config, "REQUIRED_CHANNEL_ID", 0)
    if chat_id and _status(tg, chat_id, user_id) in ("left", "kicked"):
        return False
    return True
