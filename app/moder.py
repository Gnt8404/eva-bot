"""Фильтр оскорблений для чата. Мат сам по себе НЕ отмечается.

Что считается нарушением:
  * прямые оскорбления человека на любом языке (тупой, дебил, мразь, idiot,
    idiota, aptal, 傻逼, غبي, मूर्ख …) — 67 языков и письменностей;
  * грубые слова (клоун, нуб, слабак) — в любом контексте;
  * грубые слова с двойным смыслом (крыса, свинья, баран, школьник, жирный) —
    только когда сказаны в адрес человека: «крыса в подвале» чисто,
    «ты крыса» нарушение;
  * требования и пожелания в адрес человека (заткнись, сдохни, проваливай);
  * обходы: латиница вместо кириллицы и наоборот, цифры вместо букв,
    символы вместо букв (д*рак, д.у.р.а.к), растяжки (дееебил), разделители,
    греческие буквы, смешанные письменности (нub), опечатки в один символ.

Чего фильтр НЕ делает:
  * не реагирует на мат и брань без адресата («блять, лаги», «pizdec»);
  * не реагирует на цифры, ники и наборы символов («777», «_xXx_»);
  * не реагирует на ссылки, домены, @упоминания и адреса;
  * не реагирует на обычные слова (мандарин, педикюр, тупик, target).

Публичное API:
    detect_full(text, directed=False, entities=None, names=None) -> dict|None
    detect(text, ...)      -> найденное слово (или "")
    is_insult(text, ...)   -> bool
    strip_links(text, entities=None) — вырезать ссылки и упоминания
    variants(text)         — варианты написания, которые проверяются
    reload(custom_words=None) — пересобрать правила (свои слова из базы)
    engine_info()          — статистика правил
    explain(text)          — человеческое объяснение для /check
"""
import logging
import re
import unicodedata

from . import lexicon as lex

log = logging.getLogger("moder")

CAT_INSULT = "оскорбление"
CAT_SOFT = "грубое оскорбление"
CAT_SOFT_T = "грубое в адрес"   # внутренняя метка: только при обращении

# ---------------------------------------------------------------------------
# ссылки, упоминания, телефоны: нарушением быть не могут
# ---------------------------------------------------------------------------
_TLD = ("com|ru|net|org|io|me|tv|cc|gg|to|xyz|info|biz|online|site|store|shop|"
        "app|dev|pro|club|space|fun|link|click|live|top|pw|su|by|kz|ua|uk|de|"
        "fr|es|it|pl|tr|us|ca|au|jp|cn|kr|in|br|nl|se|no|fi|dk|cz|sk|ro|bg|"
        "gr|pt|ch|at|be|lt|lv|ee|il|ir|sa|ae|am|ge|az|uz|kg|md|rs|hr|si|hu|"
        "onion|page|blog|wiki|news|media|stream|download|yt|moe")

_NOISE = [
    re.compile(r"(?i)[a-z][a-z0-9+.\-]{1,15}://\S+"),          # схема://…
    re.compile(r"(?i)www\.[^\s]+"),                             # www.…
    re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:%s)"
               r"(?::\d+)?(?:[/?#][^\s]*)?" % _TLD),            # домен.tld
    re.compile(r"(?<![\w])@[A-Za-z0-9_]{3,32}"),                # @упоминание
    re.compile(r"(?<![\w])\+\d[\d\s\-()]{6,}\d"),               # телефон
    re.compile(r"\btg://\S+"),
]
_ENTITY_SKIP = ("url", "mention", "phone_number")

# ---------------------------------------------------------------------------
# нормализация
# ---------------------------------------------------------------------------
# латиница/цифры/греческий -> кириллица (визуальные двойники)
LAT_TO_CYR = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "k": "к", "m": "м", "t": "т", "h": "н", "b": "в", "u": "и", "i": "и",
    "s": "с", "r": "р", "n": "п", "z": "з", "d": "д", "f": "ф", "g": "г",
    "l": "л", "v": "в", "w": "ш", "q": "к", "j": "й",
    "0": "о", "1": "и", "3": "е", "4": "ч", "6": "б", "7": "т", "8": "в",
    "5": "с", "$": "с", "@": "а", "!": "и",
    "α": "а", "ε": "е", "ο": "о", "ρ": "р", "τ": "т", "υ": "и", "ν": "и",
    "κ": "к", "μ": "м", "χ": "х", "ι": "и", "σ": "с", "π": "п",
}
# кириллица -> латиница (визуальные двойники)
CYR_TO_LAT = {
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y",
    "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "и": "i", "з": "z",
    "д": "d", "ф": "f", "г": "g", "л": "l", "п": "n", "б": "b", "ё": "e",
    "э": "e", "ы": "y", "й": "i", "ц": "c", "ч": "4", "ш": "w", "щ": "w",
    "ю": "u", "я": "a",
}
# транслит по звучанию (для смешанных записей: «нub» = «нуб», «туpoй»)
PHON_TO_CYR = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з",
    "i": "и", "y": "у", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф", "h": "х",
    "c": "ц", "j": "дж", "w": "в", "x": "кс", "q": "к",
    "0": "о", "1": "и", "3": "з", "4": "ч", "6": "б", "7": "т", "8": "в",
    "5": "с",
}
PHON_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_MASK_CHARS = "._*+-—–|/~'\"`^°•·:;,!?()[]{}<>№₽€£¥₹©®™§¶×÷"
_MASKED = set(_MASK_CHARS)
_SYMBOLS = "".join(sorted(_MASK_CHARS))

# цифры и символы вместо букв: m0r0n, 1d10t, п3дор, д4ун, з@ткнись
_LEET_CYR = {"0": "о", "1": "и", "3": "е", "4": "ч", "5": "с", "6": "б",
             "7": "т", "8": "в", "9": "д", "@": "а", "$": "с", "!": "и"}
_LEET_LAT = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "6": "g",
             "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "!": "i"}
_LEET_LAT2 = {"1": "l", "6": "b", "4": "h"}

_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD], None)

_ALPHA = r"[^\W\d_]"
_WORD = re.compile(_ALPHA + r"(?:" + _ALPHA + r"|\d){1,}", re.UNICODE)
_LETTERS = re.compile(_ALPHA, re.UNICODE)
_MASK_TOKEN = re.compile(
    _ALPHA + r"(?:" + _ALPHA + r"|[" + re.escape(_SYMBOLS) + r"]){2,30}", re.UNICODE)
_SPACED = re.compile(
    r"(?<!" + _ALPHA + r")(?:" + _ALPHA + r"(?:[\s._*+\-—–|/\\]{1,2})){2,}"
    + _ALPHA + r"(?!" + _ALPHA + r")", re.UNICODE)

# слова, которые не переводим в другую письменность: иначе обычный мат
# («kurwa», «pizdec», «suka») превращается в чужое оскорбление
CROSS_STOP = {
    "kurwa", "kurva", "pizda", "pizdec", "pizdets", "pizdiec", "suka",
    "cyka", "blyat", "bljat", "blia", "huy", "hui", "huj", "chuj", "khui",
    "govno", "gowno", "gówno", "dupa", "joder", "mierda", "merde", "putain",
    "caralho", "porra", "foda", "perkele", "saatana", "vittu", "jebem",
    "jebac", "jebany", "pierdol", "spierdalaj", "wypierdalaj", "neuk",
    "godverdomme", "tyfus", "scheisse", "scheiße", "cholera", "dreck",
    "verdammt", "shit", "fuck", "fucking", "damn", "crap",
    "dura", "duras", "dure", "duro", "duros", "paka", "kaka",
    "suka", "asuka", "sakura", "baka", "kusa", "musa",
}

_CYR = re.compile(r"[\u0400-\u04FF]")
_LAT = re.compile(r"[A-Za-z]")


def strip_links(text, entities=None):
    """Убрать ссылки, домены и @упоминания — они нарушением не считаются."""
    text = text or ""
    if entities:
        chars = list(text)
        total = len(chars)
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            if ent.get("type") not in _ENTITY_SKIP:
                continue
            try:
                off = int(ent.get("offset") or 0)
                ln = int(ent.get("length") or 0)
            except (TypeError, ValueError):
                continue
            for i in range(max(0, off), min(total, off + ln)):
                chars[i] = " "
        text = "".join(chars)
    for rx in _NOISE:
        try:
            text = rx.sub(" ", text)
        except Exception:  # pragma: no cover
            continue
    return text


def _fold(text):
    """Убрать диакритику: głupi -> glupi, sən -> sen, aptál -> aptal, ё -> е."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _clean(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_ZERO_WIDTH)
    return text.lower()


def _map(text, table):
    out = []
    for ch in text:
        out.append(table.get(ch, ch))
    return "".join(out)


def _collapse(text):
    return re.sub(r"(.)\1{2,}", lambda m: m.group(1) * 2, text)


def _unmask(text):
    return "".join(ch for ch in text if ch not in _MASKED)


def _unglue(text):
    """«д у р а к», «д.у.р.а.к» -> «дурак»."""
    return _SPACED.sub(
        lambda m: re.sub(r"[\s._*+\-—–|/\\]", "", m.group(0)), text)


def _deleet(text, table, extra=None):
    out = []
    for ch in text:
        if ch in table:
            out.append(table[ch])
        elif extra and ch in extra:
            out.append(extra[ch])
        else:
            out.append(ch)
    return "".join(out)


def _deleet_forms(word):
    """Восстановить слово, где буквы заменены цифрами или символами."""
    if not any(ch.isdigit() or ch in "@$!" for ch in word):
        return []
    has_cyr = bool(_CYR.search(word))
    out = []
    tables = [_LEET_CYR] if has_cyr else [_LEET_LAT, _LEET_LAT2]
    for table in tables:
        fixed = _deleet(word, table)
        if fixed != word and fixed not in out:
            out.append(fixed)
    return out


def _mixed(word):
    """В слове смешаны кириллица и латиница — значит, это обход."""
    return bool(_CYR.search(word)) and bool(_LAT.search(word))


def variants(text):
    """Варианты написания, которые проверяются (для отладки и /check)."""
    raw = _clean(text)
    out = []

    def add(value):
        value = " ".join(value.split())
        if value and value not in out:
            out.append(value)

    for base in (raw, _fold(raw), _map(raw, LAT_TO_CYR), _map(raw, CYR_TO_LAT),
                 _map(raw, PHON_TO_CYR), _map(raw, PHON_TO_LAT)):
        add(base)
        add(_fold(base))
        add(_collapse(base))
        add(_unmask(base))
        add(_unglue(base))
    return out


def _forms(word):
    """Возможные написания одного слова: прямое и кросс-письменные."""
    direct = [word]
    folded = _fold(word)
    if folded != word:
        direct.append(folded)
    cross = []
    for table in (LAT_TO_CYR, CYR_TO_LAT, PHON_TO_CYR, PHON_TO_LAT):
        mapped = _fold(_map(word, table))
        if mapped != word and mapped not in direct and mapped not in cross:
            cross.append(mapped)
    return direct, cross


# ---------------------------------------------------------------------------
# движок
# ---------------------------------------------------------------------------
class _Engine:
    def __init__(self, custom=None):
        self.custom = [str(w).strip().lower() for w in (custom or []) if str(w).strip()]
        self.exact = {}          # слово -> (язык, категория)
        self.stems = {}          # первая буква -> [(корень, язык, категория)]
        self.sub = []            # (подстрока, язык, категория)
        self.sub_min = 2         # минимальная длина подстроки
        self.second = set()
        self.exceptions = set()
        self.exception_heads = set()
        self.cores = {}          # первая буква -> [ядро]
        self.custom_hits = set()
        self.phrase_extra = []

        for lang, words in lex.HARD_LANGS.items():
            for w in words:
                self._add(w, lang, CAT_INSULT)
        for w in lex.SOFT_WORDS:
            self._add(w, "soft", CAT_SOFT)
        for w in lex.SOFT_TARGET_WORDS:
            self._add(w, "soft", CAT_SOFT_T)
        for lang, words in lex.SUB_LANGS.items():
            for w in words:
                self.sub.append((_fold(w.lower()), lang, CAT_INSULT))
        for w in lex.SECOND_PERSON:
            self.second.add(_fold(w.lower()))
        for w in lex.EXCEPTIONS:
            f = _fold(w.lower())
            self.exceptions.add(f)
            if len(f) >= 5:
                self.exception_heads.add(f)
        for w in lex.CORE:
            f = _fold(_collapse(w.lower()))
            self.cores.setdefault(f[:1], []).append(f)
        for bucket in self.cores.values():
            bucket.sort(key=len, reverse=True)

        # свои слова из базы: «слово», «корень*» или «фраза из слов»
        for w in self.custom:
            if " " in w:
                self.phrase_extra.append(_fold(w))
            else:
                self._add(w, "custom", CAT_INSULT, custom=True)

        self.sub = [(s, lang, cat) for s, lang, cat in self.sub if len(s) >= 2]
        self.sub.sort(key=lambda x: len(x[0]), reverse=True)
        self.sub_by_len = {}
        for s, lang, cat in self.sub:
            self.sub_by_len.setdefault(len(s), []).append((s, lang, cat))
        self.phrase_rx = self._build_phrases()

    # --- сборка ---------------------------------------------------------
    def _add(self, entry, lang, category, custom=False):
        entry = str(entry).strip().lower()
        if not entry:
            return
        if len(entry.split()) > 1:              # фразу ищем как подстроку
            self.sub.append((_fold(entry), lang, category))
            return
        stem = entry.endswith("*")
        base = _fold(entry[:-1] if stem else entry)
        if not base:
            return
        if custom:
            self.custom_hits.add(base)
        if stem:
            bucket = self.stems.setdefault(base[:1], [])
            bucket.append((base, lang, category))
            bucket.sort(key=lambda x: len(x[0]), reverse=True)
        else:
            self.exact[base] = (lang, category)

    def _build_phrases(self):
        # \s+ -> \s*: пробелы могли съесть разделители при обходе («т ы  т у п о й»)
        parts = ["(?:%s)" % p.replace(r"\s+", r"\s*") for p in lex.PHRASES]
        for w in self.phrase_extra:
            parts.append(re.escape(w).replace(r"\ ", r"\s+"))
        if not parts:
            return None
        try:
            return re.compile("|".join(parts), re.IGNORECASE | re.UNICODE)
        except re.error as exc:  # pragma: no cover
            log.warning("не собрались фразы: %s", exc)
            return None

    # --- проверки -------------------------------------------------------
    def _is_exception(self, word):
        if word in self.exceptions:
            return True
        for head in self.exception_heads:
            if word.startswith(head):
                return True
        return False

    def _lookup(self, forms, categories, guard=False):
        """Ищем слово/корень. guard — режим кросс-письменных написаний."""
        if isinstance(categories, str):
            categories = (categories,)
        for form in forms:
            if len(form) < 3 or not _LETTERS.search(form):
                continue
            if self._is_exception(form):
                continue
            hit = self.exact.get(form)
            if hit and hit[1] in categories:
                if guard and len(form) < 4:
                    continue
                return form, hit[1], hit[0]
            for stem, lang, cat in self.stems.get(form[:1], ()):
                if cat not in categories:
                    continue
                if form.startswith(stem):
                    if guard and len(stem) < 4 and len(form) < 5:
                        continue
                    return form, cat, lang
        return None

    def _masked(self, text):
        """«д*рак», «д.у.р.а.к», «п3дор» — сверяем по символам."""
        low = _clean(text)
        if not any(ch in _MASKED for ch in low):
            return None
        for token in _MASK_TOKEN.findall(low):
            if not any(ch in _MASKED for ch in token):
                continue
            letters = "".join(ch for ch in token if ch not in _MASKED)
            if len(letters) < 3:
                continue
            for form in {letters, _fold(letters), _collapse(letters)}:
                hit = self._lookup([form], CAT_INSULT)
                if hit:
                    return hit
            for cand in self._wild_candidates(token):
                if self._wild(token, cand[0]):
                    return cand[0], cand[2], cand[1]
        return None

    def _wild_candidates(self, token):
        pool = []
        first = token[0]
        for stem, lang, cat in self.stems.get(first, ()):
            if cat == CAT_INSULT and len(stem) <= len(token) + 1:
                pool.append((stem, lang, cat))
        for word, (lang, cat) in self.exact.items():
            if cat == CAT_INSULT and word[:1] == first and abs(len(word) - len(token)) <= 1:
                pool.append((word, lang, cat))
        return pool[:600]

    @staticmethod
    def _wild(token, stem):
        """«д*рак» против корня «дурак»: маска закрывает любую букву."""
        if len(token) < len(stem):
            return False
        for i, ref in enumerate(stem):
            ch = token[i]
            if ch != ref and ch not in _MASKED:
                return False
        return True

    @staticmethod
    def _close(a, b, limit=1):
        if a == b:
            return True
        if abs(len(a) - len(b)) > limit:
            return False
        prev = list(range(len(b) + 1))
        for i in range(1, len(a) + 1):
            cur = [i] + [0] * len(b)
            for j in range(1, len(b) + 1):
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                             prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1))
            if min(cur) > limit:
                return False
            prev = cur
        return prev[len(b)] <= limit

    def _fuzzy(self, form):
        """Опечатки и подмены букв: «дибил», «идиоткаа», «stupidd»."""
        if len(form) < 5:
            return None
        target = _collapse(form)
        for core in self.cores.get(form[:1], ()):
            if abs(len(core) - len(target)) > 1:
                continue
            if self._close(target, core, 1):
                return form, CAT_INSULT, "?"
        return None

    def _substring(self, text, categories=(CAT_INSULT,)):
        if not text:
            return None
        if isinstance(categories, str):
            categories = (categories,)
        for s, lang, cat in self.sub:
            if cat in categories and s and s in text:
                return s, cat, lang
        return None

    # --- основной проход ------------------------------------------------
    def _text_words(self, text):
        """Слова текста во всех «прямых» написаниях (без смены письменности)."""
        words, seen = [], set()
        base = _clean(text)
        values = [base, _fold(base), _collapse(base), _unmask(base),
                  _unglue(base), _unmask(_unglue(base)),
                  _deleet(base, _LEET_CYR), _deleet(base, _LEET_LAT),
                  _deleet(base, _LEET_LAT2)]
        for value in values:
            for word in _WORD.findall(value):
                if word not in seen:
                    seen.add(word)
                    words.append(word)
        return words, values

    def _decode_forms(self, word):
        """Восстановленные написания: (уверенные, требующие проверки).

        Уверенные — цифры-подмены, «нub» -> «нуб», «шk0льник» -> «школьник».
        Требующие проверки — перевод целого слова в другую письменность,
        там нужны только длинные совпадения (иначе «duration» -> «дуратион»).
        """
        loose, strict = [], []

        def put(store, form):
            if form not in store:
                store.append(form)

        for leet in _deleet_forms(word):
            put(loose, leet)
            target = loose if _mixed(leet) else strict
            for cross in self._cross_forms(leet):
                put(target, cross)
        for cross in self._cross_forms(word):
            put(loose if _mixed(word) else strict, cross)
        return loose, strict

    def _cross_forms(self, word):
        """Написания со сменой письменности: tupoj -> тупой, нub -> нуб."""
        if word in CROSS_STOP or _fold(word) in CROSS_STOP:
            return []
        has_cyr = bool(_CYR.search(word))
        has_lat = bool(_LAT.search(word))
        if has_cyr and has_lat:                  # смешанная запись — обход
            tables = (LAT_TO_CYR, CYR_TO_LAT, PHON_TO_CYR, PHON_TO_LAT)
        elif has_lat:                            # латиницей написали русское слово
            tables = (LAT_TO_CYR, PHON_TO_CYR)
        else:                                    # кириллица: только цифры-подмены
            tables = (LAT_TO_CYR,)
        out = []
        for table in tables:
            mapped = _fold(_map(word, table))
            if mapped != word and mapped not in out:
                out.append(mapped)
        return out

    def _lookup_cross(self, forms, categories, strict=True):
        """Кросс-письменные написания: допускаем только уверенные совпадения."""
        if isinstance(categories, str):
            categories = (categories,)
        for form in forms:
            if len(form) < 3 or not _LETTERS.search(form):
                continue
            if self._is_exception(form):
                continue
            hit = self.exact.get(form)
            if hit and hit[1] in categories:
                if len(form) >= 4 or not strict:
                    return form, hit[1], hit[0]
                continue
            for stem, lang, cat in self.stems.get(form[:1], ()):
                if cat not in categories:
                    continue
                if strict and (len(stem) < 5 or len(form) > len(stem) + 3):
                    continue
                if form.startswith(stem):
                    return form, cat, lang
        return None

    def check(self, text, directed=False):
        words, values = self._text_words(text)
        if len(text) < 2 or not _LETTERS.search(text):
            return None, None, None, None

        # 1. фразы
        if self.phrase_rx is not None:
            for value in values[:12]:
                m = self.phrase_rx.search(value)
                if m and _LETTERS.search(m.group(0)):
                    return m.group(0).strip(), CAT_INSULT, "фраза", "фраза"

        # 2. прямые слова и корни
        for word in words:
            forms, _ = _forms(word)
            hit = self._lookup(forms, CAT_INSULT)
            if hit:
                return hit[0], hit[1], hit[2], "слово"

        # 3. обходы: цифры, символы, смена письменности, смешанные записи
        for word in words:
            loose, strict = self._decode_forms(word)
            hit = None
            if loose:
                hit = self._lookup(loose, CAT_INSULT)
            if not hit and strict:
                hit = self._lookup_cross(strict, CAT_INSULT)
            if hit:
                return hit[0], hit[1], hit[2], "обход написания"

        # 4. обходы символами и цифрами
        for value in values:
            hit = self._masked(value)
            if hit:
                return hit[0], hit[1], hit[2], "маскировка"

        # 5. опечатки (и в восстановленных написаниях тоже)
        for word in words[:200]:
            loose, strict = self._decode_forms(word)
            for form in dict.fromkeys([word] + loose + strict):
                if self._is_exception(form):
                    continue
                hit = self._fuzzy(form)
                if hit:
                    return hit[0], hit[1], "?", "опечатка"

        # 6. письменности без пробелов
        for value in (clean := _clean(text), _fold(clean)):
            hit = self._substring(value)
            if hit:
                return hit[0], hit[1], hit[2], "иероглифы"

        # 7. грубые слова: часть всегда, часть только в адрес человека
        targeted = bool(directed) or self.has_target(text)
        cats = (CAT_SOFT, CAT_SOFT_T) if targeted else (CAT_SOFT,)
        for word in words:
            forms, _ = _forms(word)
            hit = self._lookup(forms, cats)
            if not hit:
                loose, strict = self._decode_forms(word)
                if loose:
                    hit = self._lookup(loose, cats)
                if not hit and strict:
                    hit = self._lookup_cross(strict, cats)
            if hit:
                return hit[0], CAT_SOFT, hit[2], "обращение" if targeted else "грубое слово"
        if targeted:
            for value in (_clean(text), _fold(_clean(text))):
                hit = self._substring(value, (CAT_SOFT, CAT_SOFT_T))
                if hit:
                    return hit[0], CAT_SOFT, hit[2], "иероглифы"
        return None, None, None, None

    def has_target(self, text):
        """Есть ли обращение «ты/вы/you/du/sen» и т.п."""
        low = _fold(_clean(text))
        for word in _WORD.findall(low):
            for form in (word, _fold(word), _map(word, LAT_TO_CYR),
                         _map(word, PHON_TO_CYR)):
                if form in self.second:
                    return True
        return False


_ENGINE = _Engine()


def reload(custom_words=None):
    """Пересобрать правила (после добавления своих слов)."""
    global _ENGINE
    _ENGINE = _Engine(custom_words)
    return len(_ENGINE.custom)


def engine_info():
    info = lex.stats()
    info["custom"] = len(_ENGINE.custom)
    return info


LANG_NAMES = {
    "ru": "русский", "uk": "украинский", "be": "белорусский", "pl": "польский",
    "cs": "чешский", "sk": "словацкий", "bg": "болгарский", "sr": "сербский",
    "hr": "хорватский", "sl": "словенский", "mk": "македонский", "lt": "литовский",
    "lv": "латышский", "et": "эстонский", "en": "английский", "de": "немецкий",
    "nl": "нидерландский", "sv": "шведский", "no": "норвежский", "da": "датский",
    "is": "исландский", "es": "испанский", "fr": "французский", "it": "итальянский",
    "pt": "португальский", "ro": "румынский", "ca": "каталанский", "fi": "финский",
    "hu": "венгерский", "tr": "турецкий", "az": "азербайджанский",
    "kk": "казахский", "ky": "киргизский", "uz": "узбекский", "tk": "туркменский",
    "id": "индонезийский", "ms": "малайский", "vi": "вьетнамский",
    "tl": "филиппинский", "sw": "суахили", "af": "африкаанс", "sq": "албанский",
    "el": "греческий", "mn": "монгольский", "zh": "китайский", "ja": "японский",
    "ko": "корейский", "th": "тайский", "ar": "арабский", "fa": "персидский",
    "he": "иврит", "hi": "хинди", "bn": "бенгальский", "pa": "панджаби",
    "ur": "урду", "ta": "тамильский", "te": "телугу", "mr": "маратхи",
    "gu": "гуджарати", "ne": "непальский", "si": "сингальский", "ka": "грузинский",
    "hy": "армянский", "am": "амхарский", "my": "бирманский", "km": "кхмерский",
    "lo": "лаосский", "ru-translit": "русский (латиницей)", "soft": "грубое слово",
    "phrase": "фраза", "sub": "восточные письменности", "custom": "своё слово",
}


def lang_name(code):
    if not code:
        return ""
    return LANG_NAMES.get(code, "" if code in ("?", "фраза", "soft") else code)


def variants_of(text):
    return variants(text)


def _mentions_name(text, names):
    low = " " + _fold(text) + " "
    for name in names or ():
        name = _fold(str(name).strip().lower())
        if len(name) < 3:
            continue
        if (" " + name + " ") in low or ("@" + name) in low:
            return True
    return False


def detect_full(text, directed=False, entities=None, names=None):
    """Полная проверка. Возвращает dict или None."""
    if not text:
        return None
    clean = _clean(strip_links(text, entities)).strip()
    if len(clean) < 2 or not _LETTERS.search(clean):
        return None
    targeted = bool(directed) or bool(names and _mentions_name(clean, names))
    try:
        word, category, lang, method = _ENGINE.check(clean, directed=targeted)
    except Exception as exc:      # фильтр не должен ронять бота
        log.warning("сбой проверки «%s»: %s", clean[:60], exc)
        return None
    if not word:
        return None
    return {
        "word": word,
        "category": category,
        "lang": lang or "?",
        "method": method,
        "directed": bool(targeted or _ENGINE.has_target(clean)),
        "custom": word in _ENGINE.custom_hits,
    }


def detect(text, directed=False, entities=None, names=None):
    found = detect_full(text, directed=directed, entities=entities, names=names)
    return found["word"] if found else ""


def is_insult(text, directed=False, entities=None, names=None):
    return bool(detect_full(text, directed=directed, entities=entities, names=names))


def explain(text, directed=True):
    """Человеческое объяснение для /check."""
    found = detect_full(text, directed=directed)
    if not found:
        return "Нарушений нет: оскорбления и грубые слова тут не найдены."
    return ("Нашлось: %s\nКатегория: %s\nЯзык: %s\nКак поймано: %s"
            % (found["word"], found["category"], found["lang"], found["method"]))
