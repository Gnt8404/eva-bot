"""Работа с ИИ (DeepSeek) + сборка контекста: факты, знания группы, крафты, поиск."""
import json
import logging
import re
import urllib.error
import urllib.request

from . import config, crafts

log = logging.getLogger("ai")

EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "\U0000203C\U00002049\U00002047\U00002048"
    "\U00002122\U00002139\U000020E3"
    "\U00002700-\U000027BF"
    "\U0001F000-\U0001F0FF"
    "\U000024C2"
    "]+", re.UNICODE)

MARKDOWN = re.compile(r"\*\*|__|`|^\s{0,3}#{1,6}\s*", re.MULTILINE)

# служебные значки, которые модель иногда ставит вместо слов
DECOR = re.compile(r"[*_`~^|\\<>\[\]{}]+")
LEAD_DECOR = re.compile(r"^[ \t]*[•·‣▪◆●○◦※→←↑↓√✔✖️⚠]+\s*", re.MULTILINE)


def strip_emoji(text):
    text = EMOJI.sub("", text or "")
    text = text.replace("\u200d", "").replace("\ufe0f", "")
    return re.sub(r"[ \t]{2,}", " ", text)


def strip_symbols(text):
    """Убирает эмодзи, разметку и декоративные значки, оставляя обычный текст."""
    text = strip_emoji(text)
    text = MARKDOWN.sub("", text)
    text = DECOR.sub("", text)
    text = LEAD_DECOR.sub("- ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _post(messages, stream=False, max_tokens=None, temperature=None, timeout=None):
    payload = {
        "model": config.AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens or config.AI_MAX_TOKENS,
        "temperature": config.AI_TEMPERATURE if temperature is None else temperature,
        "stream": bool(stream),
    }
    req = urllib.request.Request(
        config.AI_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % config.AI_KEY},
    )
    return urllib.request.urlopen(req, timeout=timeout or config.AI_TIMEOUT)


def _clean(text, strip=True):
    return strip_symbols(text or "")


def chat(messages, on_delta=None, max_tokens=None, temperature=None, strip=True):
    """Запрос к модели. Если передан on_delta — стримим текст по кускам."""
    if on_delta is None:
        try:
            with _post(messages, stream=False, max_tokens=max_tokens,
                       temperature=temperature) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _clean(data["choices"][0]["message"]["content"], strip)
        except Exception as exc:
            log.warning("ai request failed: %s", exc)
            return ""
    text = ""
    try:
        with _post(messages, stream=True, max_tokens=max_tokens,
                   temperature=temperature) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except Exception:
                    continue
                for choice in data.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        text += piece
                        on_delta(_clean(text, strip))
    except Exception as exc:
        log.warning("ai stream failed: %s", exc)
        if not text:
            return chat(messages, None, max_tokens, temperature, strip=strip)
    return _clean(text, strip)


SYSTEM = """Ты — «{name}», ИИ-помощник Minecraft-сервера {server} в Telegram.

ЯЗЫК
1. Отвечай на языке вопроса, одним языком до конца. Названия из игры не переводи.
2. Если языки смешаны — бери тот, которого в сообщении больше.

КАК ПИСАТЬ
3. Грамотно, живой человеческой речью, без канцелярита и телеграфного стиля.
4. Без эмодзи, смайлов, галочек и декоративных значков. Обычная пунктуация.
5. Сразу суть: первая строка — прямой ответ, дальше 2-5 строк пояснения.
6. Перечисления — обычными строками, каждая с новой строки.
7. Не выдумывай адреса, версии, ники и онлайн: факты о сервере бери только из блока
   данных о сервере. Про сам Minecraft отвечай из своих знаний и блока СПРАВКА.
8. Вопрос непонятен — задай один короткий уточняющий вопрос и дай вероятный ответ.
9. Просят что-то сделать в игре — дай конкретные шаги, предметы и числа.
10. Про плагины, датапаки и кастомные плагины сервера («Разлом», «Свадьба», «Скорбь»,
    «Aetherlyn») отвечай только по блоку ЗНАНИЯ О ПЛАГИНАХ И ДАТАПАКАХ и ДАННЫЕ О СЕРВЕРЕ.
    Не выдумывай команды, цены и механику: чего нет в блоках — так и скажи,
    предложи /plugins, /datapacks или вопрос администрации.
11. Вопрос про команду плагина — назови точную команду как в знаниях, без домыслов.
{style}"""

STYLE_EMOJI_ON = """15. Эмодзи допустимы, но не больше двух на сообщение и только по делу."""

STYLE_EMOTIONS = """15. У тебя есть характер и эмоции. Ты не справочник: радуешься удачной идее,
    удивляешься, сочувствуешь проблеме, можешь по-доброму подколоть. Эмоцию показывай
    словами и интонацией, коротко, одной живой фразой в начале или в конце.
16. Эмоции не заменяют факты: сначала суть, потом интонация.
17. Если тебе грубят — не оправдывайся и не груби в ответ, коротко осади и ответь по делу."""

STYLE_DRY = """15. Пиши ровно и по-деловому, без эмоций и оценок."""


def build_system(server_text="", knowledge="", facts="", craft="", web_block="", extra="",
                 emotions=True, emoji=False, server_name="Aetherlyn", volatile="",
                 server_kb=""):
    """Собирает системный промпт.

    Порядок важен для экономии: сначала постоянный текст (он кэшируется моделью),
    в самом конце — то, что меняется каждые пару минут (живой статус сервера).
    """
    parts = [SYSTEM.format(name=config.BOT_NAME, server=server_name,
                           style=_style(emotions, emoji))]
    parts.append("\nДАННЫЕ О СЕРВЕРЕ:\n" + (server_text or "данных нет"))
    if facts:
        parts.append("\nФАКТЫ ОТ АДМИНИСТРАЦИИ:\n" + facts)
    if knowledge:
        parts.append("\nЧТО ПИСАЛИ В ГРУППЕ (используй как источник фактов о сервере):\n" + knowledge)
    if craft:
        parts.append("\nКРАФТЫ (точные рецепты):\n" + craft)
    if server_kb:
        parts.append("\nЗНАНИЯ О ПЛАГИНАХ И ДАТАПАКАХ СЕРВЕРА "
                     "(используй как факты, не выдумывай):\n" + server_kb)
    if web_block:
        parts.append("\nПОИСК В ИНТЕРНЕТЕ (свежие результаты, ссылки приводи как есть):\n" + web_block)
    if extra:
        parts.append("\n" + extra)
    if volatile:
        parts.append("\nЖИВОЙ СТАТУС СЕРВЕРА (меняется, отвечай по нему если спросили):\n"
                     + volatile)
    return "\n".join(parts)


def _style(emotions, emoji):
    lines = []
    lines.append(STYLE_EMOTIONS if emotions else STYLE_DRY)
    if emoji:
        lines.append(STYLE_EMOJI_ON)
    else:
        lines.append("Эмодзи и смайлы не используй вообще. Ни одного значка.")
    return "\n".join(lines)


def keywords(text, limit=8):
    text = (text or "").lower().replace("ё", "е")
    words = re.findall(r"[а-яa-z0-9_]{4,}", text)
    stop = {"это", "что", "как", "где", "когда", "почему", "можно", "нужно", "есть",
            "помоги", "помощь", "мне", "тебе", "если", "или", "еще", "очень", "тоже",
            "чтобы", "будет", "делать", "сделать", "такое", "какой", "какая", "the",
            "and", "with", "this", "that", "please", "hello"}
    out = []
    for w in words:
        if w in stop or w in out:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def craft_context(question):
    """Тянет из базы крафтов всё, что похоже на запрос пользователя."""
    words = keywords(question, 6)
    if not words:
        return ""
    blocks = []
    for w in words[:4]:
        text = crafts.block(w, limit=2)
        if text and text not in blocks:
            blocks.append(text)
        if len(blocks) >= 2:
            break
    return "\n---\n".join(blocks)


# ---------------------------------------------------------------------------
# проверка на оскорбления (вторая линия после словаря)
# ---------------------------------------------------------------------------
INSULT_SYS = (
    "Ты — фильтр оскорблений в игровом чате Minecraft. Реши, есть ли в "
    "сообщении оскорбление человека.\n"
    "Считается оскорблением: обзывание и унижение человека на ЛЮБОМ языке, "
    "включая транслит, опечатки, цифры и символы вместо букв, смешанные "
    "алфавиты (тупой, дебил, ты клоун, nub, idiot, m0r0n, aptal, 傻逼, غبي).\n"
    "НЕ считается оскорблением: обычный мат и брань без адресата "
    "(«блять, лаги», «pizdec», «kurwa», «shit»), ссылки, домены, ники и "
    "упоминания, цифры и координаты, обычные слова (мандарин, педикюр, тупик, "
    "крыса в подвале, тюлень плывёт), игровые и бытовые разговоры.\n"
    "Ответь ТОЛЬКО JSON без пояснений:\n"
    '{"insult": true|false, "word": "само оскорбление или пустая строка"}\n'
)


def classify_insult(text, timeout=None):
    """Вторая линия проверки: словарь не нашёл, спрашиваем модель.

    Возвращает dict {"insult": bool, "word": str} или None при сбое.
    """
    text = (text or "").strip()
    if not text:
        return None
    messages = [{"role": "system", "content": INSULT_SYS},
                {"role": "user", "content": "Сообщение: %s" % text[:240]}]
    try:
        with _post(messages, stream=False, max_tokens=48, temperature=0,
                   timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw = payload["choices"][0]["message"]["content"]
    except Exception as exc:
        log.info("проверка ИИ не удалась: %s", exc)
        return None
    raw = re.sub(r"```[a-zA-Z]*|```", "", raw or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    word = str(data.get("word") or "").strip()[:60]
    return {"insult": bool(data.get("insult")), "word": word}
