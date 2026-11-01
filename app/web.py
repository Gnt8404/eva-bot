"""Поиск в интернете: DuckDuckGo, Википедия (резерв), поиск видео на YouTube."""
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("web")

RU_EN = {
    "ферма опыта": "minecraft xp farm", "фермы опыта": "minecraft xp farm",
    "опыт": "minecraft xp", "ферма": "minecraft farm", "дом": "minecraft house",
    "постройка": "minecraft build", "крафт": "minecraft crafting", "рецепт": "minecraft recipe",
    "поршень": "minecraft piston", "автоматизация": "minecraft automation",
    "редстоун": "minecraft redstone", "редстоунa": "minecraft redstone",
    "зачарование": "minecraft enchantment", "плагин": "minecraft plugin",
    "сервер": "minecraft server", "мод": "minecraft mod", "шейдер": "minecraft shader",
    "ресурспак": "minecraft resource pack", "датапак": "minecraft datapack",
    "лаг": "minecraft lag fix", "оптимизация": "minecraft optimization",
    "босс": "minecraft boss", "дракон": "minecraft ender dragon",
    "гайд": "guide", "туториал": "tutorial", "обзор": "review",
    "бедрок": "bedrock", "бэдрок": "bedrock", "приваты": "region protection",
}


def _request(url, data=None, headers=None, timeout=None):
    hdrs = {
        "User-Agent": config.UA,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace")


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (text.replace("&amp;", "&").replace("&quot;", '"').replace("&#x27;", "'")
                .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " "))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _real_url(href):
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return urllib.parse.unquote(target[0])
    return href


def ddg(query, limit=6, timeout=5):
    """DuckDuckGo (html + lite). Возвращает список {title,url,snippet}."""
    results = []
    try:
        html = _request("https://html.duckduckgo.com/html/", data={"q": query, "kl": "ru-ru"}, timeout=timeout)
        for match in re.finditer(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                html, re.S):
            url = _real_url(match.group(1))
            title = _clean(match.group(2))
            snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', match.group(3), re.S)
            snippet = _clean(snippet_match.group(1)) if snippet_match else ""
            if url.startswith("http") and title:
                results.append({"title": title, "url": url, "snippet": snippet[:280]})
            if len(results) >= limit:
                break
    except Exception as exc:
        log.info("ddg html failed: %s", exc)
    if results:
        return results
    try:
        html = _request("https://lite.duckduckgo.com/lite/", data={"q": query}, timeout=timeout)
        for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>',
                                 html, re.S):
            url = _real_url(match.group(1))
            title = _clean(match.group(2))
            if url.startswith("http") and title:
                results.append({"title": title, "url": url, "snippet": ""})
            if len(results) >= limit:
                break
    except Exception as exc:
        log.info("ddg lite failed: %s", exc)
    return results


def _decode_bing(url):
    """Bing отдаёт ссылки через редирект — достаём настоящий адрес."""
    if "bing.com/ck/a" not in url:
        return url
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        raw = (params.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        raw += "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw.encode()).decode("utf-8", "replace")
        if decoded.startswith("http"):
            return decoded
    except Exception:
        pass
    return url


def bing(query, limit=6, timeout=7):
    """Резервный поисковик (Bing HTML)."""
    out = []
    try:
        html = _request("https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "setlang": "ru", "cc": "RU"}), timeout=timeout)
        for block in re.split(r'<li class="b_algo"', html)[1:]:
            link = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not link:
                continue
            url = _decode_bing(_clean(link.group(1)))
            title = _clean(link.group(2))
            snip = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            snippet = _clean(snip.group(1)) if snip else ""
            if url.startswith("http") and title:
                out.append({"title": title, "url": url, "snippet": snippet[:280]})
            if len(out) >= limit:
                break
    except Exception as exc:
        log.info("bing failed: %s", exc)
    return out


def wikipedia(query, limit=3):
    """Резерв: Википедия (ru, затем en)."""
    out = []
    for lang in ("ru", "en"):
        try:
            api = "https://%s.wikipedia.org/w/api.php" % lang
            raw = _request(api + "?" + urllib.parse.urlencode({
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit, "utf8": 1}))
            data = json.loads(raw)
            for item in (data.get("query", {}).get("search") or [])[:limit]:
                title = item.get("title", "")
                snippet = _clean(item.get("snippet", ""))
                url = "https://%s.wikipedia.org/wiki/%s" % (lang, urllib.parse.quote(title))
                out.append({"title": title, "url": url, "snippet": snippet})
        except Exception as exc:
            log.info("wiki %s failed: %s", lang, exc)
        if out:
            break
    return out


def mcwiki(query, limit=4, lang="ru"):
    """Вики Minecraft через MediaWiki API — точные факты по игре и рецептам."""
    out = []
    hosts = ["https://minecraft.fandom.com/ru/api.php"]
    if lang != "ru":
        hosts.append("https://minecraft.fandom.com/api.php")
    for host in hosts:
        try:
            raw = _request(host + "?" + urllib.parse.urlencode({
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit, "utf8": 1}), timeout=8)
            data = json.loads(raw)
            hits = data.get("query", {}).get("search") or []
            wiki_prefix = (host.replace("/ru/api.php", "/ru/wiki/")
                           if "/ru/api.php" in host else host.replace("/api.php", "/wiki/"))
            for hit in hits[:limit]:
                title = hit.get("title", "")
                out.append({"title": title,
                            "url": wiki_prefix + urllib.parse.quote(title.replace(" ", "_")),
                            "snippet": _clean(hit.get("snippet", ""))[:280]})
            if out and hits:
                extract = _extract(host, hits[0]["title"])
                if extract:
                    out[0]["snippet"] = extract[:500]
                break
        except Exception as exc:
            log.info("mcwiki %s failed: %s", host, exc)
    return out


def _extract(host, title):
    try:
        raw = _request(host + "?" + urllib.parse.urlencode({
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "titles": title, "format": "json", "utf8": 1}), timeout=8)
        pages = (json.loads(raw).get("query", {}).get("pages") or {})
        for page in pages.values():
            text = (page.get("extract") or "").strip()
            if text:
                return re.sub(r"\s+", " ", text)[:900]
    except Exception as exc:
        log.info("extract failed: %s", exc)
    return ""


def search(query, limit=6, budget=11):
    """Параллельный опрос нескольких источников. Первыми — профильные по Minecraft."""
    query = (query or "").strip()
    if not query:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout

    engines = [("mcwiki", mcwiki), ("wiki", wikipedia), ("ddg", ddg), ("bing", bing)]
    order = {"mcwiki": 0, "wiki": 1, "ddg": 2, "bing": 3}
    collected = {}

    def merge():
        merged, seen = [], set()
        for name in sorted(collected, key=lambda n: order.get(n, 9)):
            for item in collected[name]:
                url = item.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                merged.append(item)
        return merged

    pool = ThreadPoolExecutor(max_workers=len(engines))
    try:
        futures = {pool.submit(fn, query, limit): name for name, fn in engines}
        deadline = time.time() + budget
        try:
            for fut in as_completed(futures, timeout=budget):
                name = futures[fut]
                try:
                    collected[name] = fut.result() or []
                except Exception as exc:
                    log.info("engine %s failed: %s", name, exc)
                    collected[name] = []
                if len(merge()) >= limit:
                    break
                if time.time() > deadline:
                    break
        except FutTimeout:
            log.info("поиск: часть источников не ответила за %s с", budget)
    finally:
        pool.shutdown(wait=False)

    return merge()[:limit]


def to_english(query):
    """Грубый перевод запроса для лучших результатов YouTube."""
    low = (query or "").lower()
    for ru, en in sorted(RU_EN.items(), key=lambda kv: -len(kv[0])):
        if ru in low:
            low = low.replace(ru, en)
    return low


def youtube(query, limit=3, english=True):
    """Поиск видео. Возвращает список {title,url,channel}. Ссылки всегда реальные."""
    query = (query or "").strip()
    if not query:
        return []
    q = to_english(query) if english else query
    videos = _youtube_scrape(q, limit)
    if len(videos) < limit:
        videos += _youtube_ddg(q, limit - len(videos), videos)
    if len(videos) < limit and english:
        videos += _youtube_scrape(query, limit - len(videos))
    seen = set()
    unique = []
    for v in videos:
        if v["url"] in seen:
            continue
        seen.add(v["url"])
        unique.append(v)
    return unique[:limit]


def _youtube_scrape(query, limit):
    out = []
    try:
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query) + "&hl=ru"
        html = _request(url, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        match = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
        blob = match.group(1) if match else html
        for m in re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,600}?"title":\{"runs":\[\{"text":"(.*?)"\}',
                             blob, re.S):
            vid, title = m.group(1), m.group(2)
            title = _clean(title.encode().decode("unicode_escape", "ignore")) if "\\u" in title else _clean(title)
            out.append({"title": title, "url": "https://www.youtube.com/watch?v=" + vid, "channel": ""})
            if len(out) >= limit:
                break
        if not out:
            for m in re.finditer(r'"videoId":"([\w-]{11})"', blob):
                vid = m.group(1)
                out.append({"title": "(видео)", "url": "https://www.youtube.com/watch?v=" + vid,
                            "channel": ""})
                if len(out) >= limit:
                    break
    except Exception as exc:
        log.info("youtube scrape failed: %s", exc)
    return out


def _youtube_ddg(query, limit, existing):
    if limit <= 0:
        return []
    out = []
    known = {v["url"] for v in existing}
    try:
        for item in ddg("site:youtube.com watch " + query, limit + 4):
            url = item["url"]
            if "watch?v=" not in url or url in known:
                continue
            out.append({"title": item["title"], "url": url, "channel": ""})
            if len(out) >= limit:
                break
    except Exception as exc:
        log.info("youtube ddg failed: %s", exc)
    return out


def search_block(query, limit=5):
    """Текстовый блок результатов поиска для контекста ИИ."""
    items = search(query, limit)
    if not items:
        return ""
    lines = []
    for i, item in enumerate(items, 1):
        line = "%d) %s | %s" % (i, item["title"], item["url"])
        if item.get("snippet"):
            line += " | " + item["snippet"]
        lines.append(line)
    return "\n".join(lines)
