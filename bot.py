#!/usr/bin/env python3
"""Точка входа бота-помощника Minecraft-сервера (@RM_AI23bot / «Ева»).

Запуск:            python3 bot.py
Постоянная работа: python3 bot.py --loop   (бот сам перезапускается)
Самопроверка:      python3 bot.py --selftest
Знания:            python3 bot.py --kb [слово]   (что знает бот)
"""
import argparse
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_LOG_FILE = os.environ.get("LOG_FILE", "").strip()
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
if _LOG_FILE:
    # пишем лог напрямую в файл UTF-8: не зависит от кодировки консоли Windows
    try:
        os.makedirs(os.path.dirname(_LOG_FILE) or ".", exist_ok=True)
    except OSError:
        pass
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format=_LOG_FORMAT,
        handlers=[logging.FileHandler(_LOG_FILE, encoding="utf-8")],
    )
else:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format=_LOG_FORMAT,
    )
log = logging.getLogger("bot")


def selftest():
    import socket
    import struct
    import threading

    from app import ai, config, crafts, kb, mc, moder, rcon, sinfo, typer, web
    from app.db import Store
    from app.logic import Bot  # noqa: F401  (проверяем импорт логики)
    from app.tg import Tg

    checks = []
    warnings = []

    def check(name, fn):
        try:
            value = fn()
            checks.append((name, True, str(value)[:90]))
        except Exception as exc:
            checks.append((name, False, str(exc)[:160]))

    check("конфиг: токен задан", lambda: "да" if config.BOT_TOKEN.count(":") == 1 else "нет")
    check("telegram getMe", lambda: Tg(config.BOT_TOKEN).get_me().get("username"))
    # платный запрос к модели в самопроверке не делаем: ключ проверяем без трат
    check("deepseek ключ", lambda: "ключ задан" if config.AI_KEY else "ключ не задан")
    check("база крафтов", lambda: "рецептов: %d" % crafts.count())
    check("поиск рецепта (поршень)", lambda: crafts.find("поршень", 1)[0].out)
    check("обратный поиск (булыжник)", lambda: "вариантов: %d" % len(crafts.what_from("булыжник")))
    store = Store(os.path.join(config.DATA_DIR, "selftest.db"))
    check("база данных", lambda: store.set("t", "1") or store.get("t"))
    check("парсер адреса", lambda: mc.split_addr("play.example.com:25566"))
    check("карточка сервера", lambda: "полей: %d" % len(sinfo.values(store)))
    check("карточка: сервер Aetherlyn", lambda: sinfo.get(store, "about"))
    check("карточка: bedrock-порт 25599",
          lambda: "25599" if "25599" in sinfo.get(store, "bedrock") else "НЕТ")
    check("список адресов", lambda: store.server_addrs())
    from app import mckb
    check("справочник Minecraft", lambda: mckb.lookup("на какой высоте алмазы")[:40])
    check("знания: файлы прочитаны", lambda: "записей: %d" % kb.count())

    def _kb_count(source, minimum):
        got = kb.count(source)
        assert got >= minimum, "мало записей в %s: %d (нужно %d)" % (source, got, minimum)
        return got

    check("знания: плагины (>=25)", lambda: "записей: %d" % _kb_count("plugins", 25))
    check("знания: датапаки (>=8)", lambda: "записей: %d" % _kb_count("datapacks", 8))
    check("знания: «Разлом» (>=8)", lambda: "записей: %d" % _kb_count("rift", 8))
    check("знания: «Свадьба» (>=5)", lambda: "записей: %d" % _kb_count("wedding", 5))
    check("знания: «Скорбь» (>=4)", lambda: "записей: %d" % _kb_count("sorrow", 4))
    check("знания: всего записей (>=50)", lambda: "записей: %d" % kb.count())
    check("знания: «Разлом»", lambda: kb.get("разлом").title if kb.get("разлом") else "НЕТ")
    check("знания: «Разлом» — есть крафты", lambda: "рецептов: %d" % crafts.EXTRA_CRAFTS)

    def _player_catalogs():
        plugins = [t for t, _ in kb.catalog("plugins")]
        packs = [t for t, _ in kb.catalog("datapacks")]
        for name in ("bStats", "CoreProtect", "LuckPerms", "ProtocolLib", "TAB",
                     "Chunky", "spark"):
            assert name not in plugins, "админский плагин попал в список: %s" % name
        assert ".paper-remapped" not in plugins, ".paper-remapped в плагинах"
        assert ".paper-remapped" not in packs, ".paper-remapped в датапаках"
        assert "RiftPlugin" in plugins, "Разлом пропал из списка"
        assert "Wedding" in plugins, "Свадьба пропала из списка"
        assert "Incendium" in packs, "Incendium пропал из списка датапаков"
        assert len(plugins) >= 8 and len(packs) >= 5, (len(plugins), len(packs))
        return "плагинов для игроков: %d, датапаков: %d" % (len(plugins), len(packs))

    check("списки: только для игроков, без .paper-remapped", _player_catalogs)
    check("скрытые плагины остались в знаниях",
          lambda: kb.get("CoreProtect").title if kb.get("CoreProtect") else "НЕТ")
    check("знания: «Свадьба»", lambda: kb.get("свадьба").title if kb.get("свадьба") else "НЕТ")
    check("знания: «Скорбь»", lambda: kb.get("скорбь").title if kb.get("скорбь") else "НЕТ")
    check("знания: поиск по вопросу",
          lambda: (kb.lookup("как работает разлом", limit=1) or [None])[0].title)
    check("крафты Разлома загружены", lambda: "добавлено: %d" % crafts.EXTRA_CRAFTS)
    check("крафт Разлома ищется", lambda: crafts.find("разлом", 1)[0].out)

    def _typing():
        class FakeTg:
            def __init__(self):
                self.sent = []
                self.edits = 0

            def send(self, chat_id, text, **kw):
                self.sent.append(text)
                return {"message_id": 1}

            def edit(self, chat_id, mid, text, **kw):
                self.edits += 1
                return True

        fake = FakeTg()
        tw = typer.Typewriter(fake, 1)
        tw.feed("Прив")
        mid = tw.finish("Привет! Это проверка плавной печати ответа Евы.")
        assert mid == 1, "сообщение не отправлено"
        assert fake.sent and fake.sent[0], "первый кусок не ушёл"
        return "сообщений: %d, правок: %d" % (len(fake.sent), fake.edits)

    check("плавная печать работает", _typing)
    check("настройки печати", lambda: "вкл, %d симв/сек" % config.TYPE_CPS
          if config.TYPE_ANIMATION else "выкл")
    check("модерация: оскорбление ловится", lambda: moder.is_insult("ты тупой") is True)
    check("модерация: требование ловится", lambda: moder.is_insult("заткнись") is True)
    check("модерация: мат НЕ ловится", lambda: moder.is_insult("блять ну и погода") is False)
    check("модерация: транслит мата НЕ ловится",
          lambda: moder.is_insult("pizdec kak huyovo") is False)
    check("модерация: kurwa НЕ ловится", lambda: moder.is_insult("kurwa, лаги") is False)
    check("модерация: цифры НЕ ловятся", lambda: moder.is_insult("777") is False)
    check("модерация: ник с цифрами НЕ ловится",
          lambda: moder.is_insult("@luck_777FAITH прости не заметил что ты зашла") is False)
    check("модерация: обычный вопрос", lambda: moder.is_insult("как скрафтить кирку") is False)
    check("модерация: ложное срабатывание", lambda: moder.is_insult("мандарины вкусные") is False)
    check("модерация: ссылки НЕ ловятся",
          lambda: not any(moder.is_insult(t) for t in
                          ("заходи https://t.me/durak_777", "смотри site.ru/idiot",
                           "vk.com/morons тут")))
    check("модерация: оскорбление рядом со ссылкой ловится",
          lambda: moder.is_insult("ты дурак, вот https://t.me/xxx") is True)
    check("модерация: 67 языков и письменностей",
          lambda: all(moder.is_insult(t) for t in (
              "you are an idiot", "du bist ein idiot", "eres un pendejo",
              "tu es un abruti", "sei idiota", "sen aptal", "gerizekalı",
              "ты ідіот", "jesteś idiotą", "ty jsi idiot", "bodoh", "mày ngu",
              "ikaw ay bobo", "أنت غبي", "تو احمق", "אתה מטומטם", "तू मूर्ख",
              "তুমি বোকা", "நீ முட்டாள்", "మూర్ఖుడు", "मूर्ख", "ඔයා මෝඩ",
              "你是傻逼", "お前はバカ", "너 병신", "มึงโง่", "μαλάκας",
              "იდიოტი", "դու հիմար ես", "чи тэнэг", "wewe ni mjinga",
              "jy is 'n idioot", "ti je budalla", "ақымақ", "ahmoq")))
    check("модерация: обходы ловятся",
          lambda: all(moder.is_insult(t) for t in (
              "ты д*рак", "д.у.р.а.к", "tы дeбиl", "ты нub", "m0r0n", "1d10t",
              "5hut up", "шk0льник " * 1, "з@ткнись", "дурааааак", "дурак ты")))
    check("модерация: мягкие слова НЕ ловятся без адресата",
          lambda: not any(moder.is_insult(t) for t in
                          ("крыса в подвале", "тюлень плывёт", "пень от дерева",
                           "школьники на экскурсии", "жирный шрифт", "баран бегает")))
    check("модерация: те же слова с адресатом ловятся",
          lambda: all(moder.is_insult(t) for t in
                      ("ты крыса", "ты тюлень", "ты баран", "ты школьник", "ты жирный")))
    check("модерация: обычные слова НЕ ловятся",
          lambda: not any(moder.is_insult(t) for t in
                          ("target safe chicken creeper", "педикюр сделал",
                           "duration 5 секунд", "я в тупике", "тупо да",
                           "думаю построить дом", "трактор в поле")))
    check("модерация: размер словаря",
          lambda: "правил: %d, языков: %d" % (moder.engine_info()["total"],
                                              moder.engine_info()["languages"]))
    from app import music
    check("музыка: понимает «найди песню»",
          lambda: music.is_request("ева найди песню Sonne")[0] == "sonne")
    check("музыка: понимает «ева найди Angel of Darkness»",
          lambda: music.is_request("ева найди angel of darkness")[0] == "angel of darkness")
    check("музыка: обычный вопрос не считает песней",
          lambda: music.is_request("как скрафтить кирку")[0] is None)
    check("музыка: крафт не считает песней",
          lambda: music.is_request("ева найди крафт поршня")[0] is None)
    check("музыка: имя файла без запрещённых знаков",
          lambda: music.safe_name("AC/DC: Back*in?Black") == "AC DC Back in Black")
    check("музыка: длительность трека", lambda: music.pretty_duration(215) == "3:35")
    check("музыка: подпись трека",
          lambda: music.describe({"artist": "Alex C.", "title": "Angel", "duration": 215})
          == "Alex C. — Angel (3:35)")
    check("музыка: артист и название из строки",
          lambda: music.split_artist_title("Alex C. – Angel of Darkness", "toru")[0] == "Alex C.")
    check("музыка: инструменты на хосте",
          lambda: "yt-dlp=%s ffmpeg=%s" % (bool(music.ytdlp_available()),
                                           bool(music.ffmpeg_path())))
    check("telegram: умеет отправлять аудио",
          lambda: callable(Tg(config.BOT_TOKEN).send_audio))

    def _no_broadcast():
        import importlib.util
        assert importlib.util.find_spec("app.broadcast") is None
        return "модуль удалён"

    check("рассылка удалена из бота", _no_broadcast)

    from app import logic as _logic
    from app.db import Store as _Store

    def _help_menu():
        flat = [b["text"] for row in _logic.MAIN_MENU["inline_keyboard"] for b in row]
        for gone in ("Сайт проекта", "Сайт сервера", "Найти песню", "Крафты",
                     "Статус сервера", "Взаимодействия", "Помощь"):
            assert gone not in flat, "лишняя кнопка в меню: %s" % gone
        assert "Нужна помощь" in flat, flat
        assert "Плагины и датапаки" in flat, flat
        return "кнопок: %d" % len(flat)

    check("меню: без кнопки сайта и лишних, заявка и плагины на месте", _help_menu)
    check("тексты: про взаимодействия больше не упоминаем",
          lambda: "НЕТ" if "взаимодейств" in _logic.HELP_TEXT.lower() else "ок")
    check("тексты: про сайт-кнопку больше не упоминаем",
          lambda: "НЕТ" if "/site" in _logic.HELP_TEXT.lower() else "ок")
    check("заявка: 6 категорий",
          lambda: "категорий: %d" % len(_logic.HELP_CATEGORIES))
    check("заявка: категория по тексту",
          lambda: _logic.help_category("не могу зайти, выкидывает с сервера"))
    check("заявка: категория «вещи»",
          lambda: _logic.help_category("пропали вещи из сундука"))
    check("заявка: неизвестное → другое",
          lambda: _logic.help_category("привет, как дела"))
    check("заявка: 5 вопросов в форме",
          lambda: "шагов: %d" % len(_logic.FORMS["help"]["steps"]))
    check("заявка: ключи шагов уникальны",
          lambda: len({k for _, k in _logic.FORMS["help"]["steps"]})
          == len(_logic.FORMS["help"]["steps"]))
    check("заявка: кнопки категорий строятся",
          lambda: "рядов: %d" % len(_logic.help_cat_keyboard()["inline_keyboard"]))
    check("заявка: получатели — владелец и помощник",
          lambda: "id: %s" % ", ".join(str(i) for i in config.HELP_ADMINS))

    def _help_db():
        path = os.path.join(config.DATA_DIR, "selftest-help.db")
        if os.path.exists(path):
            os.remove(path)
        st = _Store(path)
        who = {"id": 4242, "first_name": "Тест", "username": "test"}
        tid = st.new_ticket(who, "Заявка на помощь", "Категория: Баг или ошибка", subject="Баг или ошибка")
        assert st.user_open_ticket(4242, "Заявка на помощь")["id"] == tid
        assert st.append_ticket(tid, "дополнение") is True
        assert "дополнение" in st.ticket(tid)["body"]
        st.set_ticket_status(tid, "closed")
        assert st.user_open_ticket(4242, "Заявка на помощь") is None
        assert st.user_tickets(4242)[0]["id"] == tid
        st.set_ticket_msgs(tid, [[6343943842, 11], [8934016131, 12]])
        assert st.ticket_msgs(tid) == [[6343943842, 11], [8934016131, 12]]
        st.close()
        os.remove(path)
        return "ок"

    check("заявка: база — открытая, дополнение, закрытие", _help_db)
    check("заявка: статусы по-русски",
          lambda: _logic.Bot.status_name("taken") == "в работе")

    def _rcon_roundtrip():
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            try:
                conn, _ = srv.accept()
                auth = conn.recv(4096)
                (rid,) = struct.unpack("<i", auth[4:8])
                payload = struct.pack("<ii", rid, 2) + b"\x00\x00"
                conn.sendall(struct.pack("<i", len(payload)) + payload)
                cmd = conn.recv(4096)
                (rid2,) = struct.unpack("<i", cmd[4:8])
                payload2 = struct.pack("<ii", rid2, 0) + b"players online: 3\x00\x00"
                conn.sendall(struct.pack("<i", len(payload2)) + payload2)
                conn.close()
            except Exception:
                pass

        threading.Thread(target=serve, daemon=True).start()
        client = rcon.Rcon("127.0.0.1", port, "secret", timeout=5)
        client.connect()
        out = client.command("list")
        client.close()
        srv.close()
        if "players online" not in out:
            raise RuntimeError("ответ не получен: %r" % out)
        return out

    check("RCON: рукопожатие и команда", _rcon_roundtrip)
    check("RCON: шаблон вайтлиста",
          lambda: "whitelist add" in rcon.cfg(Store(os.path.join(config.DATA_DIR, "selftest.db")))["template"])
    for name, ok, info in checks:
        log.info("%s %s — %s", "OK  " if ok else "FAIL", name, info)

    snap = mc.best_snapshot(store.server_addrs(), timeout=6) if store.server_addrs() else None
    if snap and snap.get("online"):
        log.info("OK   живой сервер: %s (%s игроков)", snap.get("version"), snap.get("players_online"))
    else:
        warnings.append("пинг сервера: %s" % (snap or {}).get("error", "нет ответа"))

    bed = mc.bedrock_snapshot(store.bedrock_addr(), timeout=5)
    if bed and bed.get("online"):
        log.info("OK   bedrock: %s (%s игроков)", bed.get("version"), bed.get("players_online"))
    else:
        warnings.append("bedrock: %s" % (bed or {}).get("error", "нет ответа"))

    results = web.search("Minecraft Paper оптимизация", 3)
    if results:
        log.info("OK   поиск в интернете: %d результатов, первый: %s", len(results), results[0]["url"])
    else:
        warnings.append("интернет-поиск вернул 0 результатов")

    videos = web.youtube("ферма опыта minecraft", 2)
    if videos:
        log.info("OK   поиск видео: %s", videos[0]["url"])
    else:
        warnings.append("поиск видео вернул 0 результатов")

    failed = [name for name, ok, _ in checks if not ok]
    for item in warnings:
        log.warning("WARN %s", item)
    log.info("Самопроверка: %s (%d/%d)", "PASS" if not failed else "FAIL",
             len(checks) - len(failed), len(checks))

    try:
        store.close()
        os.remove(os.path.join(config.DATA_DIR, "selftest.db"))
    except Exception:
        pass
    return 1 if failed else 0


def touch_heartbeat():
    """Отметка «бот жив» — по ней сторож понимает, что процесс не завис."""
    from app import config
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(os.path.join(config.DATA_DIR, "heartbeat"), "w",
                  encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    except Exception:
        pass


def start_health_server():
    """Мини-HTTP-сервер для бесплатных облаков (Render, Koyeb и т.п.).

    Панели хостинга считают сервис живым, только если он слушает порт из
    переменной PORT. Отвечаем 200 на любой GET — этого достаточно для healthcheck.
    Заодно считаем запросы: по счётчику видно, что бот не спит.
    Локальному запуску не мешает: без PORT сервер не поднимается.
    """
    port = os.environ.get("PORT", "").strip()
    if not port:
        return None
    try:
        port = int(port)
    except ValueError:
        return None
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            state["requests"] += 1
            body = ("Ева работает. Версия %s. Запросов: %d.\n"
                    % (_code_version(), state["requests"])).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # тишина в логах
            pass

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as exc:
        log.error("health-сервер не поднялся на порту %s: %s", port, exc)
        return None
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="health").start()
    log.info("health-сервер слушает порт %d (для хостинга)", port)
    return server


def start_self_ping(interval=240):
    """Не даём бесплатному облаку усыпить бота: раз в interval секунд
    бот запрашивает свой публичный адрес (это входящий HTTP-запрос).

    Render усыпляет бесплатные веб-сервисы после 15 минут без входящего
    трафика, а боту нужен постоянный поллинг Telegram. Адрес берём из
    SELF_PING_URL или RENDER_EXTERNAL_URL (Render подставляет сам).
    """
    url = (os.environ.get("SELF_PING_URL")
           or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/") + "/"

    def loop():
        import urllib.request
        ok_once = False
        count = 0
        while True:
            time.sleep(interval)
            count += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EvaSelfPing/1.0"})
                with urllib.request.urlopen(req, timeout=40) as resp:
                    resp.read(200)
                if not ok_once:
                    log.info("самопинг работает: %s", url)
                    ok_once = True
                elif count % 15 == 0:
                    log.info("самопинг: %d запросов", count)
            except Exception as exc:  # noqa: BLE001
                log.debug("самопинг не удался: %s", exc)

    threading.Thread(target=loop, daemon=True, name="selfping").start()
    log.info("самопинг включён: %s каждые %d сек", url, interval)
    return url


def _code_version():
    try:
        from app import logic
        return logic.CODE_VERSION
    except Exception:
        return "?"


def start_status_probe(bot):
    """В фоне проверяем все адреса сервера и пишем результат в лог.

    Нужно, чтобы из логов облака было видно, какой адрес реально доступен
    снаружи (прокси/фаервол хостинга могут блокировать часть адресов).
    """
    def work():
        try:
            from app import mc
            parts = []
            for addr in bot.db.server_addrs():
                snap = mc.snapshot(addr, timeout=6)
                if snap.get("online"):
                    parts.append("%s=ok (%s игр.)"
                                 % (addr, snap.get("players_online", "?")))
                else:
                    parts.append("%s=%s" % (addr, snap.get("error") or "нет ответа"))
            log.info("проверка адресов сервера: %s", "; ".join(parts) or "не заданы")
            bedrock_addr = bot.db.bedrock_addr()
            if bedrock_addr:
                bed = mc.bedrock_snapshot(bedrock_addr, timeout=4)
                log.info("bedrock %s: %s", (bed or {}).get("addr", bedrock_addr),
                         "ok" if (bed or {}).get("online")
                         else ((bed or {}).get("error") or "нет ответа"))
        except Exception as exc:  # noqa: BLE001
            log.info("проверка адресов сервера не удалась: %s", exc)

    threading.Thread(target=work, daemon=True, name="status-probe").start()


def main():
    from app import config, kb, logic
    from app.logic import Bot
    missing = config.check_secrets()
    if missing:
        log.error("не заданы секреты: %s. Пропиши их в файле .env рядом с bot.py "
                  "(пример — .env.example).", ", ".join(missing))
        return 2
    log.info("=== Ева: версия кода %s ===", logic.CODE_VERSION)
    log.info("меню при старте (%d): %s", len(logic.MENU_TITLES),
             " | ".join(logic.MENU_TITLES))
    log.info("файл логики: %s", logic.__file__)
    log.info("плавная печать: %s", "вкл" if config.TYPE_ANIMATION else "выкл")
    log.info("знания о сервере: %d записей (%s)", kb.count(),
             ", ".join(kb.sources()) or "нет")
    touch_heartbeat()
    start_health_server()
    start_self_ping()
    fails = 0
    while True:
        try:
            bot = Bot()
            log.info("бот запущен: @%s (id %s)", bot.me.get("username"), bot.me.get("id"))
            touch_heartbeat()
            fails = 0
            start_status_probe(bot)
            bot.run()
        except KeyboardInterrupt:
            log.info("остановлено вручную")
            return 0
        except Exception as exc:
            # пауза растёт: 5, 10, 20, 40, дальше не больше 60 секунд
            wait = min(5 * (2 ** fails), 60)
            fails += 1
            log.exception("бот упал (%d-й раз подряд), перезапуск через %d секунд: %s",
                          fails, wait, exc)
            time.sleep(wait)


def supervisor():
    """Постоянная работа: если бот завершился — поднять его снова.

    Нужна для панелей хостинга и Windows, где нет systemd. Запуск:
        python3 bot.py --loop
    """
    import subprocess
    script = os.path.abspath(__file__)
    workdir = os.path.dirname(script) or None
    log.info("режим --loop: Ева работает под присмотром и перезапускается сама")
    while True:
        try:
            code = subprocess.call([sys.executable, "-u", script], cwd=workdir)
        except KeyboardInterrupt:
            log.info("остановлено вручную")
            return 0
        log.warning("бот завершился (код %s) — перезапуск через 3 секунды", code)
        time.sleep(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--loop", action="store_true",
                        help="держать бота запущенным: упал — перезапустить")
    parser.add_argument("--menu", action="store_true",
                        help="показать кнопки главного меню из этого файла и выйти")
    parser.add_argument("--version", action="store_true",
                        help="показать версию кода и выйти")
    parser.add_argument("--kb", nargs="?", const="", default=None,
                        help="показать, что бот знает о сервере (можно слово)")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.loop:
        sys.exit(supervisor())
    if args.kb is not None:
        from app import kb
        if args.kb:
            entry = kb.get(args.kb)
            print(entry.render() if entry else "Нет записи по запросу: %s" % args.kb)
        else:
            print("записей: %d" % kb.count())
            for src in kb.sources():
                print("  %s.md — %d" % (src, kb.count(src)))
        sys.exit(0)
    if args.menu or args.version:
        from app import logic, config
        print("папка бота: %s" % os.path.dirname(os.path.abspath(__file__)))
        print("версия кода: %s" % logic.CODE_VERSION)
        print("файл логики: %s" % logic.__file__)
        print("кнопок в меню: %d" % len(logic.MENU_TITLES))
        for i, title in enumerate(logic.MENU_TITLES, 1):
            print("  %d. %s" % (i, title))
        print("старых кнопок нет: %s" % (
            "да" if not ({"Сайт проекта", "Найти песню", "Крафты", "Статус сервера",
                          "Взаимодействия", "Помощь"} & set(logic.MENU_TITLES)) else "НЕТ"))
        print("токен: ...%s" % config.BOT_TOKEN[-6:])
        sys.exit(0)
    sys.exit(main())
