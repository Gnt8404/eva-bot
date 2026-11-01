"""Поиск и скачивание песен — только стандартная библиотека Python.

Работает по слоям: сначала пробуем источники, которые отдают полноценный
mp3-файл, затем запасные (короткие превью). Если на хосте есть ffmpeg,
любой формат приводится к mp3.

Источники:
    1. yt-dlp (если установлен на хосте) — полный трек, mp3 через ffmpeg;
    2. SoundCloud — полный трек, mp3-поток (progressive);
    3. Deezer  — превью 30 секунд, готовый mp3;
    4. iTunes  — превью 30 секунд, m4a (приводим к mp3, если есть ffmpeg).

Наружу модуль отдаёт простые функции:
    find(query)            — найти лучший трек
    download(track, dest)  — скачать файл (при необходимости конвертировать)
    fetch(query, dest)     — найти и скачать одним вызовом
    is_request(text)       — распознать просьбу «найди песню»
    probe()                — быстрая проверка источников (для диагностики)
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import config

log = logging.getLogger("music")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

HTTP_TIMEOUT = int(os.environ.get("RM_MUSIC_HTTP_TIMEOUT", "15"))
DOWNLOAD_TIMEOUT = int(os.environ.get("RM_MUSIC_DL_TIMEOUT", "60"))
YTDLP_TIMEOUT = int(os.environ.get("RM_MUSIC_YTDLP_TIMEOUT", "110"))
MAX_BYTES = int(os.environ.get("RM_MUSIC_MAX_BYTES", str(45 * 1024 * 1024)))
CACHE_KEEP = int(os.environ.get("RM_MUSIC_CACHE_KEEP", "40"))
CACHE_DIR = os.path.join(config.DATA_DIR, "music")

SOURCE_NAMES = {
    "ytdlp": "полный трек",
    "soundcloud": "полный трек",
    "deezer": "превью 30 секунд",
    "itunes": "превью 30 секунд",
}


# ---------------------------------------------------------------------------
# мелочи
# ---------------------------------------------------------------------------
def _get(url, timeout=None, headers=None, raw=False):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as resp:
        return resp.read() if raw else resp.read(400000)


def _get_json(url, timeout=None, headers=None):
    data = _get(url, timeout=timeout, headers=headers)
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None


def _digits(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def pretty_duration(seconds):
    seconds = _digits(seconds)
    if seconds <= 0:
        return ""
    return "%d:%02d" % (seconds // 60, seconds % 60)


def safe_name(text, limit=80):
    text = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:limit] or "track").strip()


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def cleanup_cache():
    """Держим кэш песен в разумных размерах."""
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)]
        files = [f for f in files if os.path.isfile(f)]
        if len(files) <= CACHE_KEEP:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for path in files[:-CACHE_KEEP]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# инструменты хоста: ffmpeg и yt-dlp
# ---------------------------------------------------------------------------
_tools_lock = threading.Lock()
_tools_state = {"ffmpeg": None, "ytdlp": None}
_install_tried = {"ytdlp": False, "ffmpeg": False}


def _which(name):
    return shutil.which(name)


def ffmpeg_path():
    """Путь к ffmpeg: системный, из переменной или из пакета imageio-ffmpeg."""
    if _tools_state["ffmpeg"]:
        return _tools_state["ffmpeg"]
    found = _which("ffmpeg") or os.environ.get("FFMPEG_BINARY") or ""
    if not found:
        try:
            import imageio_ffmpeg  # type: ignore
            found = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            found = ""
    if found and os.path.exists(found):
        _tools_state["ffmpeg"] = found
        return found
    return ""


def ytdlp_available():
    if _tools_state["ytdlp"]:
        return _tools_state["ytdlp"]
    if _which("yt-dlp"):
        _tools_state["ytdlp"] = _which("yt-dlp")
        return _tools_state["ytdlp"]
    try:
        import yt_dlp  # noqa: F401
        _tools_state["ytdlp"] = "%s -m yt_dlp" % sys.executable
        return _tools_state["ytdlp"]
    except Exception:
        return ""


def _ytdlp_command():
    """Команда запуска yt-dlp в виде списка аргументов."""
    tool = ytdlp_available()
    if not tool:
        return []
    if tool.endswith("-m yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    return [tool]


def install_tools(background=True):
    """Доустановка yt-dlp/ffmpeg, если их нет на хосте. Один раз за запуск."""
    missing = []
    if not ytdlp_available():
        missing.append("yt-dlp")
    if not ffmpeg_path():
        missing.append("imageio-ffmpeg")
    if not missing:
        return False

    def work():
        for pkg in missing:
            key = "ytdlp" if pkg == "yt-dlp" else "ffmpeg"
            if _install_tried[key]:
                continue
            _install_tried[key] = True
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet", "--no-input", pkg],
                    capture_output=True, timeout=300)
                log.info("музыка: установлен пакет %s", pkg)
            except Exception as exc:  # noqa: BLE001
                log.info("музыка: пакет %s поставить не удалось (%s)", pkg, type(exc).__name__)

    if background:
        threading.Thread(target=work, daemon=True).start()
        return True
    work()
    return True


# ---------------------------------------------------------------------------
# распознавание просьбы
# ---------------------------------------------------------------------------
MUSIC_WORDS = (r"песн|песен|трек|музык|мелоди|композици|song|music|track|mp3|мр3|аудио"
               r"|исполнител|групп[аыу]")
FIND_VERBS = (r"найд|поищ|поиск|скинь|скид|скач|загруз|качн|включ|постав|вруб|запуст"
              r"|дай|хочу послушать|послушать|find|search|play|download|get")
FORMAT_WORDS = r"mp3|мр3|аудио|файл|скинь|скачать|скачай"
NOT_MUSIC = (r"видео|клип|youtube|ютуб|крафт|рецепт|ферм|мод|плагин|скин|шейдер"
             r"|моб|руду|алмаз|незерит|вайтлист|правил|адрес|айпи|статус|верси"
             r"|песочниц|печь|кост[её]р")


def is_request(text):
    """Вернёт (найденный запрос или None, уверенно ли это песня).

    Первый элемент — текст для поиска, второй — True, если это точно песня
    (в сообщении есть слово «песня/трек/музыка» либо формат mp3).
    """
    low = (text or "").lower().strip()
    if not low or len(low) < 3:
        return None, False
    if re.search(NOT_MUSIC, low) and not re.search(MUSIC_WORDS, low):
        return None, False

    sure = bool(re.search(MUSIC_WORDS, low) or re.search(FORMAT_WORDS, low))
    verb = bool(re.search(FIND_VERBS, low))
    if not verb and not re.search(MUSIC_WORDS, low):
        return None, False

    query = low
    # срезаем обращение и служебные слова
    query = re.sub(r"\b(ева|эва|eva|eve|ива)\b", " ", query)
    query = re.sub(r"@%s" % re.escape(config.BOT_USERNAME.lower()), " ", query)
    query = re.sub(r"\b(найди|найти|поищи|поиск|скинь|скидывай|скачай|скачать|загрузи|качни|"
                   r"включи|включай|поставь|вруби|запусти|дай|хочу|послушать|мне|пожалуйста|"
                   r"find|search|play|download|get|me)\b", " ", query)
    query = re.sub(r"\b(песню|песни|песня|песен|трек|трека|музыку|музыка|мелодию|мелодия|"
                   r"композицию|аудио|файл|mp3|мр3|song|music|track|audio)\b", " ", query)
    query = re.sub(r"\b(в|на|по|от|для|и|а|же|бы|мне|его|её|ее)\b", " ", query)
    query = re.sub(r"[?!.,;:]+", " ", query)
    query = " ".join(query.split()).strip()
    if len(query) < 2:
        return None, sure
    return query, sure


# ---------------------------------------------------------------------------
# источник 1: yt-dlp (полный трек)
# ---------------------------------------------------------------------------
def _ffmpeg_bindir():
    """Каталог с ffmpeg/ffprobe для yt-dlp — ссылки на бинарник из imageio-ffmpeg."""
    ff = ffmpeg_path()
    if not ff:
        return ""
    bindir = os.path.join(config.DATA_DIR, "bin")
    try:
        os.makedirs(bindir, exist_ok=True)
        for name in ("ffmpeg", "ffprobe"):
            link = os.path.join(bindir, name)
            if os.path.exists(link):
                continue
            try:
                os.symlink(ff, link)
            except OSError:
                shutil.copy2(ff, link)
    except OSError:
        return ""
    return bindir


def split_artist_title(title, artist):
    """«Alex C. – Angel of Darkness» → артист и название."""
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not title:
        return artist, title
    parts = re.split(r"\s+[—–-]\s+", title, maxsplit=1)
    if len(parts) == 2 and 1 < len(parts[0]) <= 45 and len(parts[1]) > 1:
        head, tail = parts[0].strip(), parts[1].strip()
        if not artist or artist.lower() not in title.lower():
            return head, tail
    return artist, title


def _parse_ytdlp_meta(output):
    title, artist, duration, path = "", "", 0, ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("MUSIC_TITLE:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("MUSIC_ARTIST:"):
            artist = line.split(":", 1)[1].strip()
        elif line.startswith("MUSIC_DURATION:"):
            duration = _digits(line.split(":", 1)[1].strip())
        elif line.startswith("MUSIC_FILE:"):
            path = line.split(":", 1)[1].strip()
    return title, artist, duration, path


YTDLP_SOURCES = ("scsearch1", "ytsearch1")


def ytdlp_fetch(query, dest_dir, want_mp3=True):
    """Скачивает первый найденный трек через yt-dlp и приводит его к mp3."""
    cmd = _ytdlp_command()
    if not cmd:
        return None
    ensure_cache_dir()
    os.makedirs(dest_dir, exist_ok=True)
    stamp = "%s-%s" % (int(time.time()), uuid.uuid4().hex[:6])
    outtmpl = os.path.join(dest_dir, "ytdlp-%s.%%(ext)s" % stamp)
    bindir = _ffmpeg_bindir()
    base = cmd + [
        "--no-playlist", "--no-warnings", "--quiet", "--no-progress",
        "--no-cache-dir", "--no-simulate", "--socket-timeout", "20", "--retries", "1",
        "--extractor-retries", "1", "--max-filesize", str(MAX_BYTES),
        "--print", "MUSIC_TITLE:%(title)s",
        "--print", "MUSIC_ARTIST:%(artist,uploader)s",
        "--print", "MUSIC_DURATION:%(duration)s",
        "--print", "MUSIC_FILE:%(filepath)s",
        "-f", "bestaudio/best",
        "-o", outtmpl,
    ]
    if bindir:
        base += ["--ffmpeg-location", bindir]
    for source in YTDLP_SOURCES:
        try:
            proc = subprocess.run(base + ["%s:%s" % (source, query)],
                                  capture_output=True, timeout=YTDLP_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log.info("yt-dlp (%s) не отработал: %s", source, type(exc).__name__)
            continue
        stdout = (proc.stdout or b"").decode("utf-8", "replace")
        title, artist, duration, path = _parse_ytdlp_meta(stdout)
        if not path or not os.path.exists(path) or os.path.getsize(path) < 20000:
            path = ""
            for name in sorted(os.listdir(dest_dir), reverse=True):
                if name.startswith("ytdlp-%s." % stamp):
                    candidate = os.path.join(dest_dir, name)
                    if os.path.getsize(candidate) > 20000:
                        path = candidate
                        break
        if not path:
            log.info("yt-dlp (%s): файла нет", source)
            continue
        if want_mp3 and not path.lower().endswith(".mp3"):
            path = _to_mp3(path, dest_dir)
        artist, title = split_artist_title(title or query, artist)
        return {
            "title": title or query,
            "artist": artist,
            "duration": duration,
            "source": "ytdlp",
            "full": True,
            "path": path,
            "ext": os.path.splitext(path)[1].lstrip(".").lower() or "m4a",
            "url": query,
            "cover": "",
        }
    return None


# ---------------------------------------------------------------------------
# источник 2: SoundCloud (полный трек, mp3)
# ---------------------------------------------------------------------------
_sc_cache = {"cid": "", "ts": 0}


def soundcloud_client_id(force=False):
    if not force and _sc_cache["cid"] and time.time() - _sc_cache["ts"] < 6 * 3600:
        return _sc_cache["cid"]
    try:
        html = _get("https://soundcloud.com/", timeout=15).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        log.info("soundcloud недоступен: %s", type(exc).__name__)
        return ""
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    scripts = [s if s.startswith("http") else "https://soundcloud.com" + s for s in scripts]
    cid = ""
    for src in scripts:
        try:
            js = _get(src, timeout=15).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        found = (re.findall(r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,})"', js)
                 or re.findall(r'client_id=([A-Za-z0-9]{20,})', js))
        if found:
            cid = found[0]
            break
    if cid:
        _sc_cache["cid"] = cid
        _sc_cache["ts"] = time.time()
    return cid


def soundcloud_search(query, limit=5):
    cid = soundcloud_client_id()
    if not cid:
        return []
    url = ("https://api-v2.soundcloud.com/search/tracks?q=%s&client_id=%s&limit=%d"
           % (urllib.parse.quote(query), cid, limit))
    data = _get_json(url, timeout=20)
    if not data:
        return []
    tracks = []
    for item in data.get("collection") or []:
        if item.get("streamable") is False:
            continue
        user = (item.get("user") or {}).get("username") or ""
        tracks.append({
            "title": item.get("title") or "Трек",
            "artist": user,
            "duration": _digits(item.get("full_duration") or item.get("duration")) // 1000,
            "source": "soundcloud",
            "full": True,
            "url": item.get("permalink_url") or "",
            "track_id": item.get("id"),
            "cover": (item.get("artwork_url") or "").replace("-large", "-t500x500"),
        })
    return tracks


def soundcloud_mp3(track):
    """Прямая ссылка на mp3-поток SoundCloud (progressive)."""
    cid = soundcloud_client_id()
    if not cid or not track.get("track_id"):
        return ""
    data = _get_json("https://api-v2.soundcloud.com/tracks/%s?client_id=%s"
                     % (track["track_id"], cid), timeout=20)
    if not data:
        return ""
    for item in (data.get("media") or {}).get("transcodings") or []:
        fmt = (item.get("format") or {}).get("protocol")
        if fmt != "progressive":
            continue
        stream = _get_json("%s?client_id=%s" % (item.get("url"), cid), timeout=20)
        if stream and stream.get("url"):
            return stream["url"]
    return ""


# ---------------------------------------------------------------------------
# источник 3: Deezer (превью 30 секунд, готовый mp3)
# ---------------------------------------------------------------------------
def deezer_search(query, limit=5):
    data = _get_json("https://api.deezer.com/search?q=%s&limit=%d"
                     % (urllib.parse.quote(query), limit))
    if not data:
        return []
    tracks = []
    for item in data.get("data") or []:
        if not item.get("preview"):
            continue
        tracks.append({
            "title": item.get("title") or "Трек",
            "artist": (item.get("artist") or {}).get("name") or "",
            "duration": _digits(item.get("duration")),
            "source": "deezer",
            "full": False,
            "url": item["preview"],
            "cover": (item.get("album") or {}).get("cover_big")
                     or (item.get("album") or {}).get("cover_medium") or "",
            "link": item.get("link") or "",
        })
    return tracks


# ---------------------------------------------------------------------------
# источник 4: iTunes (превью 30 секунд, m4a)
# ---------------------------------------------------------------------------
def itunes_search(query, limit=5):
    data = _get_json("https://itunes.apple.com/search?media=music&limit=%d&term=%s"
                     % (limit, urllib.parse.quote(query)))
    if not data:
        return []
    tracks = []
    for item in data.get("results") or []:
        if not item.get("previewUrl"):
            continue
        tracks.append({
            "title": item.get("trackName") or "Трек",
            "artist": item.get("artistName") or "",
            "duration": _digits(item.get("trackTimeMillis")) // 1000,
            "source": "itunes",
            "full": False,
            "url": item["previewUrl"],
            "cover": item.get("artworkUrl100") or "",
            "link": item.get("trackViewUrl") or "",
        })
    return tracks


# ---------------------------------------------------------------------------
# поиск
# ---------------------------------------------------------------------------
def _clean_for_match(text):
    return re.sub(r"[^a-zа-я0-9]+", " ", (text or "").lower()).strip()


def _score(track, query):
    """Насколько трек похож на запрос: сравниваем словами и подстрокой."""
    q = _clean_for_match(query)
    hay = _clean_for_match("%s %s" % (track.get("title"), track.get("artist")))
    if not q:
        return 0
    score = 0
    if q in hay:
        score += 60
    q_words = [w for w in q.split() if len(w) > 2]
    hits = sum(1 for w in q_words if w in hay)
    if q_words:
        score += int(40 * hits / len(q_words))
    if track.get("full"):
        score += 12
    if track.get("duration", 0) > 90:
        score += 6
    if not track.get("artist"):
        score -= 4
    return score


def candidates(query, limit=5):
    """Все найденные варианты, отсортированные по похожести."""
    out = []
    for provider in (soundcloud_search, deezer_search, itunes_search):
        try:
            out.extend(provider(query, limit))
        except Exception as exc:  # noqa: BLE001
            log.info("источник %s: %s", provider.__name__, type(exc).__name__)
    for track in out:
        track["score"] = _score(track, query)
    out.sort(key=lambda t: (-t["score"], not t["full"]))
    return out


def find(query):
    """Лучший вариант по запросу (без скачивания)."""
    items = candidates(query)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# скачивание
# ---------------------------------------------------------------------------
MIME_BY_EXT = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "wav": "audio/wav",
}


def _download_file(url, path, limit=MAX_BYTES, timeout=DOWNLOAD_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    written = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(path, "wb") as fh:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                fh.close()
                os.remove(path)
                raise ValueError("файл больше допустимого размера")
            fh.write(chunk)
    return written


def _download_cover(url, dest_dir, stem):
    if not url:
        return ""
    try:
        data = _get(url, timeout=15, raw=True)
    except Exception:  # noqa: BLE001
        return ""
    if not data or len(data) > 200 * 1024:
        return ""
    path = os.path.join(dest_dir, stem + ".jpg")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _to_mp3(path, dest_dir):
    """Конвертация в mp3, если под рукой есть ffmpeg."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return path
    if path.lower().endswith(".mp3"):
        return path
    target = os.path.join(dest_dir, os.path.splitext(os.path.basename(path))[0] + ".mp3")
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", path,
             "-vn", "-acodec", "libmp3lame", "-b:a", "192k", target],
            capture_output=True, timeout=180)
    except Exception as exc:  # noqa: BLE001
        log.info("конвертация не удалась: %s", type(exc).__name__)
        return path
    if proc.returncode == 0 and os.path.exists(target) and os.path.getsize(target) > 20000:
        try:
            os.remove(path)
        except OSError:
            pass
        return target
    return path


def download(track, dest_dir=None):
    """Скачивает трек. Возвращает тот же словарь с полями path/ext/size."""
    dest_dir = dest_dir or ensure_cache_dir()
    os.makedirs(dest_dir, exist_ok=True)
    stem = safe_name("%s - %s" % (track.get("artist") or track["source"],
                                  track.get("title") or "track"), 60)
    url = track.get("url") or ""
    if track.get("source") == "soundcloud":
        url = soundcloud_mp3(track) or ""
    if not url:
        return None
    path = os.path.join(dest_dir, "%s-%s" % (stem, uuid.uuid4().hex[:6]))
    try:
        size = _download_file(url, path)
    except Exception as exc:  # noqa: BLE001
        log.info("скачивание не удалось (%s): %s", track.get("source"), type(exc).__name__)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        return None
    ext = os.path.splitext(url.split("?")[0])[1].lstrip(".").lower() or "mp3"
    final = "%s.%s" % (path, ext)
    os.replace(path, final)
    if ext != "mp3":
        final = _to_mp3(final, dest_dir)
    track = dict(track)
    track["path"] = final
    track["ext"] = os.path.splitext(final)[1].lstrip(".").lower()
    track["mime"] = MIME_BY_EXT.get(track["ext"], "audio/mpeg")
    track["size"] = os.path.getsize(final)
    track["filename"] = "%s.%s" % (stem, track["ext"])
    cover = _download_cover(track.get("cover"), dest_dir, stem)
    track["cover_path"] = cover
    cleanup_cache()
    return track


def fetch(query, dest_dir=None, allow_preview=True):
    """Находит и скачивает песню. Возвращает (трек, ошибка-или-None)."""
    query = (query or "").strip()
    if not query:
        return None, "пустой запрос"

    # 1) полный трек через yt-dlp, если он есть на хосте
    if ytdlp_available():
        dest_dir = dest_dir or ensure_cache_dir()
        os.makedirs(dest_dir, exist_ok=True)
        track = ytdlp_fetch(query, dest_dir, want_mp3=bool(ffmpeg_path()))
        if track:
            track["mime"] = MIME_BY_EXT.get(track["ext"], "audio/mpeg")
            track["size"] = os.path.getsize(track["path"])
            track["filename"] = "%s.%s" % (safe_name("%s - %s" % (track["artist"],
                                                                 track["title"]), 60),
                                           track["ext"])
            cleanup_cache()
            return track, None
    else:
        install_tools()

    # 2) SoundCloud — полный mp3
    try:
        for track in soundcloud_search(query, 5):
            if _score(track, query) < 45:
                continue
            got = download(track, dest_dir)
            if got:
                return got, None
    except Exception as exc:  # noqa: BLE001
        log.info("soundcloud-поиск не удался: %s", type(exc).__name__)

    # 3) превью Deezer (готовый mp3), 4) превью iTunes
    if not allow_preview:
        return None, "не нашлось полного трека"
    for provider in (deezer_search, itunes_search):
        try:
            items = provider(query, 5)
        except Exception as exc:  # noqa: BLE001
            log.info("превью-источник %s: %s", provider.__name__, type(exc).__name__)
            continue
        for track in sorted(items, key=lambda t: -_score(t, query)):
            if _score(track, query) < 35:
                continue
            got = download(track, dest_dir)
            if got:
                return got, None
    return None, "ничего не нашлось"


# ---------------------------------------------------------------------------
# диагностика
# ---------------------------------------------------------------------------
def probe():
    """Быстрая проверка: что доступно на этом хосте прямо сейчас."""
    result = {
        "ffmpeg": ffmpeg_path() or "",
        "ytdlp": ytdlp_available() or "",
        "deezer": False,
        "itunes": False,
        "soundcloud": False,
    }
    try:
        result["deezer"] = bool(deezer_search("test", 1))
    except Exception:  # noqa: BLE001
        pass
    try:
        result["itunes"] = bool(itunes_search("test", 1))
    except Exception:  # noqa: BLE001
        pass
    try:
        result["soundcloud"] = bool(soundcloud_get_cid_ok())
    except Exception:  # noqa: BLE001
        pass
    return result


def soundcloud_get_cid_ok():
    return bool(soundcloud_client_id())


def describe(track):
    """Короткая подпись трека для сообщения."""
    parts = []
    if track.get("artist"):
        parts.append(track["artist"])
    parts.append(track.get("title") or "трек")
    line = " — ".join(parts[:2]) if len(parts) == 2 else parts[0]
    duration = pretty_duration(track.get("duration"))
    if duration:
        line += " (%s)" % duration
    return line
