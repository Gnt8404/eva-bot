"""Роутер запросов: понять намерение и решить, что делать без лишнего LLM."""
import re

INTENTS = [
    ("restart", r"(перезапус|рестарт|restart|перезагрузи\s+сервер)"),
    ("ban", r"(^|\s)/(ban|unban|banlist|pardon)(\s|$)|\bзабан|разбан|банлист"),
    ("ticket", r"(тикет|заявк|жалоб|обратиться|обращение|не\s+работает|сломал|баг|"
               r"проблем|помогите|поддержк|верните|апелляц)"),
    ("rules", r"(правил|обязанност|права|гарантии|наказан|санкц|модерац|апелляц|"
              r"персональн\w*\s+данн|авторск|сборк\w*\s+мод|whitelist|вайтлист|"
              r"бан|грифер|разруш|жалоб|обжал|конфликт)"),
    ("status", r"(статус|онлайн|сколько\s+игрок|ip\b|айпи|верси[яю]|адрес\s+сервер|"
               r"лагает|лаги|tps)"),
    ("craft", r"(крафт|рецепт|скрафт|изготов)"),
    ("plugins", r"(плагин|датапак|команд[аы]\s+плагин|разлом|свадьб|скорб|rift|wedding|sorrow)"),
    ("web", r"(найди|поищи|погугл|в\s+интернете|что\s+такое|кто\s+такой|курс|"
             r"новост|погод|обзор|туториал)"),
]

COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in INTENTS]

ROLE_ACTIONS = {
    "restart": ("owner", "admin"),
    "ban": ("owner", "admin"),
}


def classify(text):
    low = (text or "").strip()
    for name, rx in COMPILED:
        if rx.search(low):
            return name
    return "chat"


def command_action(text):
    m = re.match(r"^/([a-zа-я_]+)", (text or "").strip().lower())
    return m.group(1) if m else ""


QUESTION_START = r"^(когда|как|можно\s+ли|нужно\s+ли|почему|зачем|сколько|что\s+такое|есть\s+ли|будет\s+ли)"
RESTART_VERBS = r"^(перезапусти|перезапустите|перезапустить|перезапускай|рестартни|рестартните|restart|перезагрузи|перезагрузите)"
BAN_VERBS = r"^(забань|забаньте|забанить|разбань|разбаньте|разбанить|удали\s+бан)"


def _imperative(low, verbs):
    return bool(re.match(verbs, low)) and not re.search(QUESTION_START, low)


def route(text, role="player"):
    """Решение: intent, нужен ли LLM, RAG, инструмент, разрешено ли действие."""
    low = (text or "").strip()
    intent = classify(low)
    action = command_action(low)
    if action in ("restart", "ban", "unban", "banlist", "pardon"):
        intent = "restart" if action == "restart" else "ban"
    elif intent == "restart" and not _imperative(low.lower(), RESTART_VERBS):
        intent = "chat"
    elif intent == "ban" and not (_imperative(low.lower(), BAN_VERBS)
                                  or low.lower().startswith("/")):
        intent = "rules"
    allowed = True
    if intent in ROLE_ACTIONS:
        allowed = role in ROLE_ACTIONS[intent]
    return {
        "intent": intent,
        "role": role,
        "allowed": allowed,
        "use_rag": intent in ("rules", "ticket", "chat"),
        "use_llm": intent not in ("status", "restart", "ban"),
        "tool": intent if intent in ("restart", "ban", "ticket") else "",
        "cache": intent in ("rules", "plugins", "craft", "chat"),
    }


def normalize_question(text):
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"[^a-zа-я0-9 ]+", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    return low
