"""Guardrails для действий владельца: проверка действие → ответ.

Ева выполняет только безопасный белый список действий и только для владельца
(config.SUPER_ADMIN). Любой другой пользователь получает отказ, а действие
пишется в трейсы.
"""
import re

from . import config


class Denied(Exception):
    """Действие запрещено guardrails."""


ALLOWED = {"copy_to_owner", "send_to_owner", "notify_owner",
           "diagram", "document"}
DEFAULT_TEXT = "Я на связи. Напиши, что отправить в личку."


def check(actor_id, action, target_id, content=""):
    """Проверить действие. Возвращает (ok, reason)."""
    if str(actor_id) != str(config.SUPER_ADMIN):
        return False, "Это действие доступно только владельцу."
    if action not in ALLOWED:
        return False, "Неизвестное действие."
    if str(target_id) != str(config.SUPER_ADMIN):
        return False, "Получатель может быть только владелец."
    if action in ("send_to_owner", "notify_owner") and not (content or "").strip():
        return False, "Пустой текст."
    if action == "send_to_owner" and len(content) > 3500:
        return False, "Слишком длинный текст (максимум 3500)."
    if action in ("diagram", "document") and len(content or "") > 8000:
        return False, "Слишком длинный текст для файла (максимум 8000)."
    return True, ""


def parse_request(text):
    """Разобрать «Ева, скинь мне это в лс» / «Ева, напиши в лс мне привет».

    Возвращает dict(action=..., content=...) или None.
    """
    raw = (text or "").strip()
    low = raw.lower().replace("ё", "е")
    if "ева" not in low:
        return None
    # «сделай диаграмму/схему/график»
    if re.search(r"(сделай|создай|построй|нарисуй|сгенерируй|сформируй)"
                 r".{0,45}(диаграмм|схем|график|чарт|chart|mind\s*map|блок-схем)",
                 low):
        m = re.search(r"(диаграмм\w*|схем\w*|график\w*|чарт\w*|chart\w*|"
                      r"mind\s*map|блок-схем\w*)", raw, re.I)
        tail = raw[m.end():].strip(" :—-.,") if m else ""
        if tail.lower().startswith("из этого"):
            tail = ""
        return {"action": "diagram", "content": tail}
    # «сделай документ»
    if re.search(r"(сделай|создай|построй|сформируй|подготовь)"
                 r".{0,45}(документ|докс|docx|ворд|word|файл)", low):
        m = re.search(r"(документ\w*|докс\w*|docx|ворд\w*|word|файл\w*)", raw, re.I)
        tail = raw[m.end():].strip(" :—-.,") if m else ""
        if tail.lower().startswith("из этого"):
            tail = ""
        return {"action": "document", "content": tail}
    # «скинь/перешли мне это в лс»
    if re.search(r"(скинь|перешли|отправь|кинь|скопируй|пришли)"
                 r".{0,40}(мне|в\s+лс|в\s+личку|сюда)", low):
        return {"action": "copy_to_owner", "content": ""}
    # «напиши в лс мне <текст>» / «напиши мне <текст>»
    m = re.search(r"(?:напиши|скажи|отправь|пришли)\s+"
                  r"(?:в\s+лс\s+)?(?:мне\s+)?(.+)$", raw, re.I | re.S)
    if m and re.search(r"(в\s+лс|мне|в\s+личку)", low):
        content = m.group(1).strip().strip("«»\"'")
        if content:
            return {"action": "send_to_owner", "content": content}
    # просто «Ева, лс» / «Ева напиши в лс»
    if re.search(r"\bева\b.{0,20}\b(лс|личк|в\s+лс)\b", low):
        return {"action": "notify_owner", "content": DEFAULT_TEXT}
    return None
