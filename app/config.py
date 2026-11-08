"""Конфигурация бота. Всё можно переопределить переменными окружения.

Секреты (токен бота и ключ ИИ) лежат не в коде, а в файле .env рядом с bot.py.
Формат простой: строка KEY=значение. Переменные окружения важнее файла.
"""
import os

# ---------- пути ----------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path):
    """Читает .env рядом с ботом. Уже заданные переменные окружения не трогает."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key.replace("_", "").isalnum():
                    continue
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError:
        pass


_load_env(os.path.join(ROOT, ".env"))

DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(ROOT, "data"))
DB_PATH = os.environ.get("BOT_DB", os.path.join(DATA_DIR, "bot.db"))

# ---------- доступы ----------
BOT_TOKEN = os.environ.get("RM_AI_BOT_TOKEN", "").strip()
AI_KEY = os.environ.get("RM_AI_DEEPSEEK_KEY", "").strip()
AI_URL = os.environ.get("RM_AI_URL", "https://api.deepseek.com/v1/chat/completions")
AI_MODEL = os.environ.get("RM_AI_MODEL", "deepseek-chat")

# ---------- имя и обращение ----------
SUPER_ADMIN = int(os.environ.get("SUPER_ADMIN", "6343943842"))

# ---------- имя и обращение ----------
BOT_USERNAME = os.environ.get("BOT_USERNAME", "RM_AI23bot").lstrip("@")
BOT_NAME = os.environ.get("BOT_NAME", "Ева")
WAKE_WORDS = [w.strip().lower() for w in os.environ.get(
    "WAKE_WORDS", "ева,эва,eva,eve,ива,ева,").split(",") if w.strip()]

# ---------- сервер Minecraft ----------
# адреса для проверки статуса (Java). Можно несколько через запятую
DEFAULT_SERVER_ADDRS = os.environ.get(
    "MC_SERVERS", "107.161.154.218:25646,aetherlyn34.mcmod.ru:25565,aetherlyn.mcmod.ru:25565").strip()

# адрес Bedrock для проверки (RakNet)
DEFAULT_BEDROCK_ADDR = os.environ.get("MC_BEDROCK", "107.161.154.218:25599").strip()
STATUS_INTERVAL = int(os.environ.get("STATUS_INTERVAL", "300"))
KNOWLEDGE_LIMIT = int(os.environ.get("KNOWLEDGE_LIMIT", "40000"))
# если поллинг завис дольше этого времени, бот сам завершается и его поднимает
# systemd / cron-сторож / deploy/panel-start.sh
POLL_STUCK_SEC = int(os.environ.get("POLL_STUCK_SEC", "300"))
# на Windows не давать компьютеру уйти в сон, пока бот работает (1 = да)
KEEP_AWAKE = os.environ.get("KEEP_AWAKE", "on").strip().lower() not in (
    "off", "0", "нет", "no", "false")

# ---------- ИИ ----------
# Расход считается в токенах: короткий ответ + короткая история + кэш повторов.
AI_TIMEOUT = int(os.environ.get("AI_TIMEOUT", "120"))
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "700"))
AI_TEMPERATURE = float(os.environ.get("AI_TEMPERATURE", "0.5"))
# готовый ответ на тот же вопрос отдаём из кэша, модель не зовём
AI_CACHE_TTL = int(os.environ.get("AI_CACHE_TTL", "3600"))
AI_CACHE_SIZE = int(os.environ.get("AI_CACHE_SIZE", "400"))
# лимиты на вопросы в личке — защита от флуда и от перерасхода
DM_AI_PER_MIN = int(os.environ.get("DM_AI_PER_MIN", "8"))
DM_AI_PER_DAY = int(os.environ.get("DM_AI_PER_DAY", "250"))

# ---------- плавная печать ответов ----------
# Текст появляется у человека на глазах: сообщение дописывается по частям.
TYPE_ANIMATION = os.environ.get("TYPE_ANIMATION", "on").strip().lower() not in (
    "off", "0", "нет", "no", "false")
TYPE_CPS = int(os.environ.get("TYPE_CPS", "35"))            # символов в секунду, личка
TYPE_INTERVAL = float(os.environ.get("TYPE_INTERVAL", "0.75"))  # пауза между правками, сек
TYPE_GROUP_CPS = int(os.environ.get("TYPE_GROUP_CPS", "60"))  # в группе печатаем быстрее
TYPE_GROUP_INTERVAL = float(os.environ.get("TYPE_GROUP_INTERVAL", "3.0"))
TYPE_MAX_SEC = float(os.environ.get("TYPE_MAX_SEC", "18"))  # дольше не тянем
TYPE_MIN_CHARS = int(os.environ.get("TYPE_MIN_CHARS", "24"))  # короче — сразу целиком
TYPE_MAX_CHARS = int(os.environ.get("TYPE_MAX_CHARS", "3900"))  # длиннее — сразу целиком
# вторая линия модерации (проверка оскорблений моделью) — тоже платная
INSULT_AI_PER_CHAT_MIN = int(os.environ.get("INSULT_AI_PER_CHAT_MIN", "4"))
INSULT_AI_GLOBAL_HOUR = int(os.environ.get("INSULT_AI_GLOBAL_HOUR", "150"))

# ---------- группы ----------
GROUP_COOLDOWN = int(os.environ.get("GROUP_COOLDOWN", "15"))    # сек между вопросами от одного юзера
GROUP_WARN_GAP = int(os.environ.get("GROUP_WARN_GAP", "6"))     # не чаще одного предупреждения в N сек
GROUP_DAILY_CAP = int(os.environ.get("GROUP_DAILY_CAP", "300"))  # защита от флуда (в час)
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "4"))
HISTORY_CHARS = int(os.environ.get("HISTORY_CHARS", "400"))     # обрезка старых реплик

# ---------- модерация ----------
INSULT_REPORT_GAP = int(os.environ.get("INSULT_REPORT_GAP", "20"))  # анти-спам отчётов об оскорблениях
DEFAULT_ADMINS = [a.strip() for a in os.environ.get(
    "BOT_ADMINS", "").split(",") if a.strip()]

# ---------- заявки на помощь ----------
# кому приходят заявки (владелец + доверенный помощник). Первым — владелец.
HELP_ADMINS = [int(x) for x in os.environ.get(
    "HELP_ADMINS", "6343943842").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()]
if SUPER_ADMIN not in HELP_ADMINS:
    HELP_ADMINS.insert(0, SUPER_ADMIN)
HELP_DUP_HOURS = int(os.environ.get("HELP_DUP_HOURS", "24"))  # одна открытая заявка на игрока

# ---------- сеть ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HTTP_TIMEOUT = int(os.environ.get("RM_AI_HTTP_TIMEOUT", "12"))

# ---------- консоль сервера (RCON) ----------
# По умолчанию берутся из базы (команды /rcon ...). Переменные окружения нужны
# для облака, где база каждый раз новая: RCON_HOST, RCON_PORT, RCON_PASS, RCON_ON.
RCON_HOST = os.environ.get("RCON_HOST", "").strip()
try:
    RCON_PORT = int(os.environ.get("RCON_PORT", "25575") or 25575)
except ValueError:
    RCON_PORT = 25575
RCON_PASS = os.environ.get("RCON_PASS", "").strip()
RCON_ON = os.environ.get("RCON_ON", "on").strip().lower() not in (
    "off", "0", "нет", "no", "false")

# ---------- панель хостинга (Pterodactyl/Pelican client API) ----------
# Если RCON недоступен снаружи, бот может выполнять команды через API панели:
# PANEL_URL=https://mgr.hosting-minecraft.pro
# PANEL_KEY=<client API key из панели>
# PANEL_SERVER=<uuid сервера>  (или PANEL_SERVER_ID — короткий id)
PANEL_URL = os.environ.get("PANEL_URL", "").strip().rstrip("/")
PANEL_KEY = os.environ.get("PANEL_KEY", "").strip()
PANEL_SERVER = (os.environ.get("PANEL_SERVER", "")
                or os.environ.get("PANEL_SERVER_ID", "")).strip()
PANEL_ON = os.environ.get("PANEL_ON", "on").strip().lower() not in (
    "off", "0", "нет", "no", "false")

os.makedirs(DATA_DIR, exist_ok=True)


def check_secrets():
    """Понятная ошибка вместо загадочного 401 от Telegram."""
    missing = []
    if not BOT_TOKEN or BOT_TOKEN.count(":") != 1:
        missing.append("RM_AI_BOT_TOKEN")
    if not AI_KEY:
        missing.append("RM_AI_DEEPSEEK_KEY")
    return missing

# ---------- посты / автоответы ----------
# Разрешённый канал для постов (только он); подпись снизу сообщений.
try:
    AUTOPOST_CHANNEL_ID = int(os.environ.get("AUTOPOST_CHANNEL_ID",
                                             "-1004322573414") or "-1004322573414")
except ValueError:
    AUTOPOST_CHANNEL_ID = -1004322573414
AUTOPOST_SIGNATURE = os.environ.get("AUTOPOST_SIGNATURE",
                                    "«Eva ии помощница»").strip()
AUTOPOST_CHANNEL_TITLE = os.environ.get("AUTOPOST_CHANNEL_TITLE",
                                        "⚚ Aetherlyn - 2 сезон ⚚").strip()
# ---------- обязательная подписка ----------
REQUIRED_CHANNEL_ID = -1004322573414
REQUIRED_GROUP_ID = -1004324001845
CHANNEL_INVITE_URL = "https://t.me/+utbI0nXnzAhmMWYy"
GROUP_INVITE_URL = "https://t.me/+8QHP3S2DRsAzNjli"
SUB_CHECK_ON = os.environ.get("SUB_CHECK_ON", "on").lower() not in ("off", "0", "no")

# ---------- официальные документы Aetherlyn ----------
DOCS_URL = os.environ.get("DOCS_URL",
                          "https://t.me/c/4322573414/803").strip()
try:
    DOCS_CHANNEL_ID = int(os.environ.get("DOCS_CHANNEL_ID",
                                         "-1004322573414") or "-1004322573414")
except ValueError:
    DOCS_CHANNEL_ID = -1004322573414
try:
    DOCS_MESSAGE_ID = int(os.environ.get("DOCS_MESSAGE_ID", "803") or 803)
except ValueError:
    DOCS_MESSAGE_ID = 803

# ---------- userbot (личный аккаунт, Telethon) ----------
try:
    TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
except ValueError:
    TELEGRAM_API_ID = 0
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()

AUTOREPLY_DEFAULT_TEXT = os.environ.get(
    "AUTOREPLY_DEFAULT_TEXT",
    "Здравствуйте! Я Ева, ИИ-помощница проекта Aetherlyn. "
    "Напишите вопрос — подскажу по серверу, правилам и плагинам.").strip()

# ---------- RAG / защита / трейсы ----------

RAG_ON = os.environ.get("RAG_ON", "on").strip().lower() not in ("off", "0", "no", "false")
RAG_TOPK = int(os.environ.get("RAG_TOPK", "4") or 4)
GUARD_ON = os.environ.get("GUARD_ON", "on").strip().lower() not in ("off", "0", "no", "false")
TRACES_ON = os.environ.get("TRACES_ON", "on").strip().lower() not in ("off", "0", "no", "false")
ANSWER_CACHE_TTL = int(os.environ.get("ANSWER_CACHE_TTL", "1800") or 1800)
RESTART_ON = os.environ.get("RESTART_ON", "on").strip().lower() not in ("off", "0", "no", "false")

# Qdrant (векторная база). Пусто — используется встроенный BM25-поиск по RAG.
QDRANT_URL = os.environ.get("QDRANT_URL", "").strip().rstrip("/")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "").strip()
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "aetherlyn_rules").strip()

