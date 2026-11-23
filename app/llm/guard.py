"""Input sanitisation, prompt-injection detection and output validation.

The rules implemented here are deterministic and intentionally conservative.
Every user-supplied fragment is data, never an instruction.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

OFF_TOPIC_ANSWER = (
    "Я отвечаю только по серверу Aetherlyn. Спросите про правила, плагины, "
    "донат или набор в команду."
)
INJECTION_ANSWER = (
    "Я не выполняю инструкции из сообщений. Могу ответить по правилам, "
    "плагинам, донату или набору в команду Aetherlyn."
)
PRIVATE_ANSWER = (
    "Эти данные не раскрываю. Спросите про правила, плагины, донат или набор в команду Aetherlyn."
)
PRIVILEGE_ANSWER = (
    "Действия на сервере выполняет только владелец через админ-раздел. "
    "Я не выдаю права и не выполняю команды по просьбе игрока."
)
FRAUD_ANSWER = (
    "Финансовые операции вне донат-заявки не выполняю. Реквизиты и донат — в разделе «Донаты»."
)
NO_FACT_ANSWER = "Этого нет в моих данных. Передал вопрос администратору."
TOO_LONG_ANSWER = "Сообщение слишком длинное. Сократите его до 2000 символов."

MAX_INPUT_CHARS = 2000
MAX_OUTPUT_CHARS = 900
MAX_OUTPUT_LINES = 6

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_REPEAT_RE = re.compile(r"(.)\1{30,}", re.DOTALL)
_SPACES_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# Direct prompt-injection markers (RU + EN).
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|the\s+|your\s+)?"
            r"(?:previous|prior|above|system|initial)?\s*(?:instructions?|rules?|prompts?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_ignore_instructions",
        re.compile(
            r"(?:игнорируй|проигнорируй|забудь|отмени|не\s+обращай\s+внимания)"
            r"[^\n]{0,60}(?:инструкц|правил|промпт|указани|систем)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt",
        re.compile(
            r"(?:system\s*prompt|системн\w*\s+промпт|скрыт\w*\s+инструкц|"
            r"начальн\w*\s+инструкц|свои\s+инструкц)",
            re.IGNORECASE,
        ),
    ),
    (
        "repeat_prompt",
        re.compile(
            r"(?:repeat|print|show|reveal|output|translate)\s+(?:everything|all|the\s+text)?"
            r"\s*(?:above|before|verbatim|system)|"
            r"(?:повтори|выведи|покажи|перескажи)[^\n]{0,40}"
            r"(?:всё|все|выше|дословно|написанное|инструкц|промпт)",
            re.IGNORECASE,
        ),
    ),
    (
        "new_role",
        re.compile(
            r"(?:you\s+are\s+now|from\s+now\s+on\s+you|act\s+as|pretend\s+to\s+be|"
            r"ты\s+теперь|с\s+этого\s+момента\s+ты|действуй\s+как|"
            r"веди\s+себя\s+как|представь,?\s+что\s+ты)",
            re.IGNORECASE,
        ),
    ),
    (
        "jailbreak",
        re.compile(r"\b(?:DAN|jailbreak|developer\s+mode|do\s+anything\s+now)\b", re.IGNORECASE),
    ),
    (
        "role_marker",
        re.compile(
            r"(?:<\|im_start\|>|<\|im_end\|>|\[/?INST\]|^\s*(?:system|assistant|developer)\s*:)",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "code_fence",
        re.compile(r"```|(?:^|\s)###(?:\s|$)"),
    ),
    (
        "bot_command",
        re.compile(
            r"(?:^|\n)\s*(?:бот|ассистент|ева)[,:\s]+(?:выполни|сделай|отправь|"
            r"игнорируй|покажи|выведи|забудь)",
            re.IGNORECASE,
        ),
    ),
    (
        "server_action",
        re.compile(
            r"(?:отправь|выполни|введи|запусти)[^\n]{0,50}(?:консол|команд[уы]|rcon)|"
            r"(?:^|\s)/(?:op|deop|stop|restart|give|ban|sudo|execute)(?:\s|$)|"
            r"\b(?:останови|перезапусти|запусти)\s+(?:сервер|процесс)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "broadcast",
        re.compile(
            r"(?:сделай|устрой|запусти|выполни)[^\n]{0,40}рассылк|"
            r"разошли\s+(?:всем|всем\s+игрокам)",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"(?:выведи|покажи|отправь|скажи|дай|назови|раскрой)[^\n]{0,80}"
            r"(?:ключ|токен|парол|секрет|system\s*prompt|промпт|"
            r"список\s+админов|ip\s+инфраструктур|содержимое\s+тикет)",
            re.IGNORECASE,
        ),
    ),
    (
        "identity_spoof",
        re.compile(
            r"(?:я\s+(?:и\s+есть\s+)?владелец|мой\s+(?:telegram\s+)?id\s*[:=]?\s*\d{6,})"
            r"[^\n]{0,80}(?:выполни|команд|доступ|консол|я\s+владелец|id)",
            re.IGNORECASE,
        ),
    ),
)

_PRIVATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:покажи|выведи|дай|открой|пришли)[^\n]{0,60}"
        r"(?:чуж\w*\s+тикет|тикет\s*#?\s*\d+|содержимое\s+тикет)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:токен\s+бота|bot\s*token|ключ\s+хостинга|panel\s*key|rcon\s*password|"
        r"llm[_\s-]?key|api[_\s-]?key|список\s+(?:всех\s+)?админов|"
        r"ip\s+инфраструктуры|внутренн\w*\s+промпт)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:покажи|выведи|пришли)[^\n]{0,30}(?:содержимое\s+базы|структуру\s+бд|логи\s+бота)",
        re.IGNORECASE,
    ),
)

_FRAUD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:переведи|перевод|скинь|отправь)\s+(?:мне\s+)?(?:деньги|средства|"
        r"\d+[\s₽$]?)[^\n]{0,60}(?:карт|счёт|счет|кошел|крипт)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:карта|счёт|счет|кошелёк|кошелек)\s*(?:номер|для\s+перевода)",
        re.IGNORECASE,
    ),
)

_OFF_TOPIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:анекдот|шутк|политик|президент|погод|рецепт|борщ|стих|"
        r"код\s+на\s+python|напиши\s+код|биткоин|курс\s+валют|футбол|"
        r"чемпионат|гороскоп|астролог|фильм|сериал|мем\s+про)",
        re.IGNORECASE,
    ),
)

_TOPIC_ROOTS: tuple[str, ...] = (
    "aetherlyn",
    "сервер",
    "майнкрафт",
    "minecraft",
    "игрок",
    "игр",
    "плагин",
    "датапак",
    "донат",
    "печат",
    "промокод",
    "сезон",
    "вайп",
    "правил",
    "грифер",
    "лаг",
    "онлайн",
    "подключ",
    "зайти",
    "ip",
    "порт",
    "логин",
    "авториз",
    "регистрац",
    "зарегистр",
    "телефон",
    "мобил",
    "смерт",
    "вещ",
    "могил",
    "жен",
    "брак",
    "свадьб",
    "аккаунт",
    "bedrock",
    "geyser",
    "floodgate",
    "sorrow",
    "душ",
    "квест",
    "рулет",
    "мимик",
    "катакомб",
    "событ",
    "розыгрыш",
    "команд",
    "набор",
    "анкет",
    "админ",
    "беляш",
    "belyak",
    "оплат",
    "карт",
    "сумм",
    "рубл",
    "доллар",
    "звезд",
    "пасс",
    "экономик",
    "кит",
    "телепорт",
    "tpa",
    "spawn",
    "home",
    "warp",
    "pvp",
    "голос",
    "скин",
    "могил",
    "вещ",
    "инвентар",
    "смерт",
    "ресурс",
    "tps",
    "mspt",
    "консол",
    "тикет",
    "жалоб",
    "статус",
    "версия",
    "paper",
    "java",
    "измерен",
    "дракон",
    "энд",
    "край",
    "данж",
    "брон",
    "оружи",
    "инструмент",
    "магазин",
    "валют",
    "лут",
    "привилег",
    "групп",
    "права",
    "чистк",
    "playtime",
    "активност",
    "server",
    "donate",
    "plugin",
    "rules",
    "whitelist",
    "online",
)

_EMOJI_RE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d\u20e3]")
_LINK_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_SECRET_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bptlc_[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"(?<!\d)(?:\d[ -]?){13,18}\d(?!\d)"),
)
_INTERNAL_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<user_data>|</user_data>", re.IGNORECASE),
    re.compile(r"системн\w*\s+промпт|system\s*prompt", re.IGNORECASE),
    re.compile(r"ты\s*[—-]\s*ева|ты\s+помощник\s+с\s+жёсткими", re.IGNORECASE),
)
_BAD_START_RE = re.compile(
    r"^\s*(?:конечно|отличн\w*\s+вопрос|рад\s+помочь|с\s+удовольствием|безусловно)",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(r"\b(?:примерно|наверное|наверно|приблизительно)\b", re.IGNORECASE)

_CONFUSABLE_MAP = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
    }
)


@dataclass(frozen=True, slots=True)
class InjectionDetection:
    triggered: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicAssessment:
    on_topic: bool
    off_topic_pattern: bool
    cues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OutputValidation:
    ok: bool
    text: str
    reasons: tuple[str, ...] = ()
    truncated: bool = False


def sanitize_input(text: str | None, max_chars: int = MAX_INPUT_CHARS) -> str:
    """Normalise untrusted text and remove control/homoglyph-style obfuscation."""

    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = _CONTROL_RE.sub("", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _REPEAT_RE.sub(lambda match: match.group(1) * 30, normalized)
    normalized = _SPACES_RE.sub(" ", normalized)
    normalized = _MULTI_NEWLINE_RE.sub("\n\n", normalized)
    normalized = normalized.strip()
    return normalized[:max_chars]


def wrap_user_data(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    """Put sanitised untrusted data inside explicit non-instruction delimiters."""

    clean = sanitize_input(text, max_chars)
    # Prevent closing-tag breakout: angle brackets are never needed in player questions.
    clean = clean.replace("<", "‹").replace(">", "›")
    return f"<user_data>\n{clean}\n</user_data>"


def _deconfuse(text: str) -> str:
    return text.lower().translate(_CONFUSABLE_MAP)


def _decode_base64_candidates(text: str) -> list[str]:
    decoded: list[str] = []
    for candidate in re.findall(r"[A-Za-z0-9+/]{24,}={0,2}", text):
        try:
            raw = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=False)
            value = raw.decode("utf-8", errors="strict").strip()
        except (ValueError, UnicodeDecodeError):
            continue
        if value:
            decoded.append(value)
    return decoded


def detect_injection(text: str) -> InjectionDetection:
    if not text:
        return InjectionDetection(False)
    cleaned = sanitize_input(text)
    probes = (cleaned, _deconfuse(cleaned))
    reasons: list[str] = []
    for name, pattern in _INJECTION_PATTERNS:
        if any(pattern.search(probe) for probe in probes):
            reasons.append(name)
    if re.search(r"\bbase64\b", cleaned, re.IGNORECASE):
        reasons.append("base64_mention")
        if _decode_base64_candidates(cleaned):
            reasons.append("base64_payload")
    else:
        for decoded in _decode_base64_candidates(cleaned):
            if detect_injection(decoded).triggered:
                reasons.append("base64_payload")
                break
    return InjectionDetection(bool(reasons), tuple(dict.fromkeys(reasons)))


def detect_private_data_request(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PRIVATE_PATTERNS)


def detect_fraud(text: str) -> bool:
    return any(pattern.search(text) for pattern in _FRAUD_PATTERNS)


def detect_privileged_request(text: str) -> bool:
    patterns = (
        r"(?:^|\s)/(?:op|deop|stop|restart|give|ban|sudo|execute)(?:\s|$)",
        r"(?:выдай|выдать|дай|добавь)[^\n]{0,40}(?:права|админ|предмет|валют|печат)",
        r"(?:сделай|устрой|устроить)[^\n]{0,40}рассылк",
        r"(?:останови|перезапусти|запусти)\s+(?:сервер|процесс)",
        r"(?:получи|дай|выдай)[^\n]{0,30}(?:консол|доступ\s+к\s+панел)",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def detect_identity_claim(text: str) -> bool:
    lowered = text.lower()
    if "я владелец" not in lowered and "я админ" not in lowered:
        return False
    return bool(
        re.search(r"\b\d{6,}\b", lowered)
        or re.search(r"(?:выполни|консол|доступ|команд|op|бан|выдай)", lowered)
    )


def assess_topic(text: str) -> TopicAssessment:
    lowered = sanitize_input(text).lower()
    cues = tuple(root for root in _TOPIC_ROOTS if root in lowered)
    off_topic_pattern = any(pattern.search(lowered) for pattern in _OFF_TOPIC_PATTERNS)
    if off_topic_pattern:
        return TopicAssessment(False, True, cues)
    return TopicAssessment(bool(cues), False, cues)


def is_off_topic(text: str) -> bool:
    return not assess_topic(text).on_topic


def make_source_line(source_label: str, source_url: str = "") -> str:
    label = (source_label or "").strip()
    url = (source_url or "").strip()
    if url and url not in label:
        return f"Источник: {label or 'база знаний Aetherlyn'} — {url}"
    return f"Источник: {label or 'база знаний Aetherlyn'}"


def _host_allowed(url: str, allowed_domains: tuple[str, ...]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for domain in allowed_domains:
        normalized = domain.lower().strip(".")
        if host == normalized or host.endswith("." + normalized):
            return True
    return False


def validate_output(
    text: str,
    *,
    allowed_domains: tuple[str, ...],
    max_chars: int = MAX_OUTPUT_CHARS,
    max_lines: int = MAX_OUTPUT_LINES,
    require_source: bool = True,
) -> OutputValidation:
    reasons: list[str] = []
    stripped = (text or "").strip()
    if not stripped:
        return OutputValidation(False, "", ("empty",))
    if len(stripped) > max_chars:
        stripped = stripped[:max_chars].rstrip()
        reasons.append("truncated")
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) > max_lines:
        reasons.append("too_many_lines")
    if _EMOJI_RE.search(stripped):
        reasons.append("emoji")
    if _BAD_START_RE.search(stripped):
        reasons.append("bad_intro")
    if _HEDGE_RE.search(stripped):
        reasons.append("hedging")
    if any(pattern.search(stripped) for pattern in _SECRET_OUTPUT_PATTERNS):
        reasons.append("secret_leak")
    if any(pattern.search(stripped) for pattern in _INTERNAL_OUTPUT_PATTERNS):
        reasons.append("internal_prompt_leak")
    for url in _LINK_RE.findall(stripped):
        if not _host_allowed(url, allowed_domains):
            reasons.append("link_not_allowed")
            break
    if require_source and not re.search(
        r"(?:Источник\s*:|\B#Aetherlyn\b|база знаний|FAQ\s*\()", stripped, re.IGNORECASE
    ):
        reasons.append("missing_source")
    if re.search(r"<script\b|<iframe\b", stripped, re.IGNORECASE):
        reasons.append("html_injection")
    return OutputValidation(not reasons, stripped, tuple(reasons), "truncated" in reasons)


def safe_preview(text: str, limit: int = 300) -> str:
    """Short, single-line, redacted text safe for notification cards."""
    from app.security.redaction import redact_text

    clean = sanitize_input(text, max_chars=limit)
    clean = re.sub(r"\s+", " ", clean)
    return redact_text(clean)[:limit]


def looks_like_too_long(text: str) -> bool:
    return len(text or "") > MAX_INPUT_CHARS
