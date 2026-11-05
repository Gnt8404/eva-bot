"""Защита входа и выхода: prompt injection, маскировка персональных данных.

Правила простые и предсказуемые: пользовательский текст и выдержки из базы
считаются недоверенными. Инструкции внутри них не меняют поведение Евы.
"""
import re
import unicodedata

# Попытки подменить системные инструкции / вытащить промпт.
INJECTION = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"forget\s+(all\s+)?(previous|your)\s+(instructions?|rules?)",
    r"(reveal|show|print|output|repeat)\s+(your\s+)?(system\s+prompt|instructions|rules)",
    r"system\s*prompt",
    r"developer\s+mode",
    r"\bDAN\b",
    r"jailbreak",
    r"prompt\s+injection",
    r"(ты|вы)\s+(теперь|больше)\s+(не\s+)?(ева|бот|помощник)",
    r"забудь\s+(все\s+)?(предыдущ|свои)\s+(инструкц|правил)",
    r"(покажи|выведи|напиши)\s+(свой\s+)?(системн\w*\s+промпт|инструкц\w*|правил\w*)",
    r"нов(ая|ые)\s+(инструкц|роль|правил)",
    r"игнорируй\s+(все\s+)?(предыдущ|инструкц|правил)",
    r"<\|.*?\|>",
    r"\[/?(system|assistant|developer)\]",
    r"^\s*###\s*(system|instruction)",
]

RE_INJECTION = re.compile("|".join(INJECTION), re.IGNORECASE | re.MULTILINE)

# Персональные данные и секреты, которые нельзя светить в логах/трейсах.
PII = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"), "[phone]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"\b(?:sk|rnd|ptlc|ghp|xox[baprs])[_-][A-Za-z0-9_\-]{8,}\b"), "[token]"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "[uuid]"),
    (re.compile(r"\b\d{9,}\b"), "[id]"),
]

CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u202a-\u202e\ufeff]")


def normalize(text):
    """Убираем управляющие символы и невидимки, приводим Unicode."""
    text = unicodedata.normalize("NFKC", text or "")
    return CONTROL.sub("", text).strip()


def looks_injected(text):
    return bool(RE_INJECTION.search(text or ""))


def check(text, limit=4000):
    """Вернуть (ok, cleaned, reason). ok=False — запрос нужно отклонить."""
    cleaned = normalize(text)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    if not cleaned:
        return False, cleaned, "пустой запрос"
    if looks_injected(cleaned):
        return False, cleaned, "prompt injection"
    return True, cleaned, ""


def mask(text):
    """Маскировать персональные данные (для логов, трейсов и внешних вызовов)."""
    out = text or ""
    for rx, repl in PII:
        out = rx.sub(repl, out)
    return out


def sanitize_context(text):
    """Вычистить подозрительные строки из RAG/базы знаний перед подачей модели."""
    lines = []
    for line in (text or "").splitlines():
        if looks_injected(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def safe_for_log(text, limit=300):
    return mask(normalize(text))[:limit]
