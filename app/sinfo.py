"""Информация о сервере Aetherlyn. Хранится в БД, меняется командой /setinfo."""

# Версия путеводителя. При смене все поля карточки обновляются на актуальные.
GUIDE_VERSION = "2026-11-01"

# ключ -> (подпись, значение по умолчанию)
FIELDS = [
    ("about", "Название", "Aetherlyn"),
    ("java", "Адрес Java",
     "aetherlyn.mcmod.ru\n"
     "для обхода блокировки: aetherlyn34.mcmod.ru"),
    ("javaver", "Версия Java", "1.21.1 - 1.21.11"),
    ("bedrock", "Адрес Bedrock", "107.161.154.218, порт 25599"),
    ("bedrockver", "Версия Bedrock", "26 - 26.60"),
    ("howto", "Как подключиться",
     "Java: aetherlyn.mcmod.ru (если не заходит — aetherlyn34.mcmod.ru). "
     "Bedrock: 107.161.154.218, порт 25599. "
     "Анкета на вход подаётся только через @RM_AI23bot, и быть в чате проекта обязательно."),
    ("whitelist", "Вход / анкета",
     "Анкеты принимаются исключительно через @RM_AI23bot, а не через администрацию сервера. "
     "Чтобы анкету приняли, нужно обязательно быть в чате проекта."),
    ("info", "Вся информация о сервере", "https://t.me/c/4322573414/609"),
    ("chat", "Чат группы", "https://t.me/+8QHP3S2DRsAzNjli"),
    ("channel", "Основной канал владельца", "https://t.me/+M7d86tzMexYyN2Zi"),
    ("spoilers", "Спойлеры обновлений", "https://t.me/+dIIFpC19MrdiNzRi"),
    ("support", "Финансовая поддержка",
     "обсуждается только в ЛС @belyak345 — звёздами либо в какой-нибудь другой валюте."),
    ("donate", "Донат-привилегии", "уточняются только в ЛС @belyak345"),
    ("friend", "Ссылка для друга", "https://t.me/+utbI0nXnzAhmMWYy"),
    ("respack", "Ресурс-пак (только Java)", "https://t.me/c/4322573414/704"),
    ("discord", "Discord", "https://discord.gg/ws4Z8jbzG"),
    ("rules", "Правила и информация", "https://t.me/c/4322573414/609"),
]

# значения, которые уже неактуальны и молча заменяются на стандартные
DEAD_VALUES = ("ricemine.mcmod.ru", "ricemine34.mcmod.ru", "107.161.154.218:25926",
               "25926", "26 - 26.45", "https://t.me/+jId217SGcuxmMTY6",
               "https://discord.gg/5aqmYpKnE", "https://t.me/c/4322573414/383",
               "https://t.me/c/4322573414/590")
DEAD_KEYS = ("info:mine",)

KEYS = [k for k, _, _ in FIELDS]
LABELS = {k: label for k, label, _ in FIELDS}
DEFAULTS = {k: v for k, _, v in FIELDS}

WELCOME = """Добро пожаловать на сервер Aetherlyn!

Версия Java: {javaver}
Версия Bedrock: {bedrockver}

Java: {java}
Bedrock: {bedrock}

Как подключиться:
{howto}

{whitelist}

Вся информация о сервере: {info}
Чат группы: {chat}
Основной канал владельца: {channel}
Спойлеры обновлений: {spoilers}
Финансовая поддержка: {support}
Ссылка для друга: {friend}

Ресурс-пак (только Java): {respack}
Правила и информация: {rules}"""


def ensure_current(store):
    """Если путеводитель обновился — карточка перезаписывается актуальной версией."""
    if store.get("info:guide_version") == GUIDE_VERSION:
        return False
    for key in KEYS:
        store.set("info:" + key, "")
    store.set("info:guide_version", GUIDE_VERSION)
    return True


def values(store):
    """Все поля: из БД, иначе значения по умолчанию.

    Устаревшие значения и настройки удалённых разделов не отдаём наружу —
    бот всегда показывает актуальные данные.
    """
    ensure_current(store)
    out = {}
    for dead in DEAD_KEYS:
        if store.get(dead) is not None:
            store.set(dead, "")
    for key in KEYS:
        raw = store.get("info:" + key)
        if raw and any(dead in raw for dead in DEAD_VALUES):
            raw = None
        out[key] = (raw if raw not in (None, "") else DEFAULTS[key])
    return out


def set_field(store, key, value):
    if key not in KEYS:
        return False
    store.set("info:" + key, value)
    return True


def get(store, key):
    return values(store).get(key, "")


def render(store):
    """Полная карточка сервера."""
    v = values(store)
    lines = ["Информация о сервере %s" % v["about"], ""]
    lines.append("Версия Java: %s" % v["javaver"])
    lines.append("Версия Bedrock: %s" % v["bedrockver"])
    lines.append("")
    lines.append("Java: %s" % v["java"])
    lines.append("Bedrock: %s" % v["bedrock"])
    lines.append("")
    lines.append("Как подключиться: %s" % v["howto"])
    lines.append("Вход: %s" % v["whitelist"])
    lines.append("")
    lines.append("Вся информация о сервере: %s" % v["info"])
    lines.append("Чат группы: %s" % v["chat"])
    lines.append("Основной канал владельца: %s" % v["channel"])
    lines.append("Спойлеры обновлений: %s" % v["spoilers"])
    lines.append("Донат-привилегии: %s" % v["donate"])
    lines.append("Финансовая поддержка: %s" % v["support"])
    lines.append("Ссылка для друга: %s" % v["friend"])
    lines.append("Ресурс-пак (только Java): %s" % v["respack"])
    lines.append("Правила и информация: %s" % v["rules"])
    return "\n".join(lines)


def welcome(store):
    return WELCOME.format(**values(store))


def block(store):
    """Текстовый блок для ИИ — единственный источник правды о сервере."""
    v = values(store)
    lines = ["ПОСТОЯННАЯ ИНФОРМАЦИЯ О СЕРВЕРЕ (факты, выдумывать нельзя):"]
    for key, label, _ in FIELDS:
        if key == "discord" and not v[key]:
            continue
        lines.append("- %s: %s" % (label, v[key]))
    return "\n".join(lines)


def block_compact(store):
    """То же, что block(), но короче — для промпта модели.

    Длинные подсказки «как подключиться» бот и так отдаёт напрямую, без модели,
    поэтому в контекст они не попадают: меньше токенов на каждый ответ.
    """
    v = values(store)
    lines = ["ПОСТОЯННАЯ ИНФОРМАЦИЯ О СЕРВЕРЕ (факты, выдумывать нельзя):"]
    for key, label, _ in FIELDS:
        if key == "howto":
            continue
        if key == "discord" and not v[key]:
            continue
        lines.append("- %s: %s" % (label, v[key]))
    return "\n".join(lines)


def edit_list(store):
    """Список полей для админа."""
    v = values(store)
    lines = ["Настройка информации о сервере.", "Меняй так: /setinfo <ключ> <значение>", ""]
    for key, label, _ in FIELDS:
        lines.append("%s — %s = %s" % (key, label, v[key]))
    return "\n".join(lines)
