"""Проверка обязательной подписки на канал и группу Aetherlyn."""
from . import config


def links_text():
    return ("Чтобы пользоваться Евой, подпишись на официальные канал и группу "
            "Aetherlyn:\n"
            "• Канал: %s\n"
            "• Группа: %s\n\n"
            "После подписки напиши /start."
            % (config.CHANNEL_INVITE_URL, config.GROUP_INVITE_URL))


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
    for chat_id in (config.REQUIRED_CHANNEL_ID, config.REQUIRED_GROUP_ID):
        if not chat_id:
            continue
        if _status(tg, chat_id, user_id) in ("left", "kicked"):
            return False
    return True
