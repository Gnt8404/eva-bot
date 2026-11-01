"""Хранилище на SQLite: пользователи, обращения, база знаний группы, факты, диалоги."""
import json
import logging
import sqlite3
import threading
import time

from . import config

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    chat_id     INTEGER,
    is_admin    INTEGER DEFAULT 0,
    banned      INTEGER DEFAULT 0,
    msg_count   INTEGER DEFAULT 0,
    created_at  INTEGER,
    seen_at     INTEGER
);
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    username    TEXT,
    kind        TEXT,
    subject     TEXT,
    body        TEXT,
    status      TEXT DEFAULT 'new',
    answer      TEXT,
    admin_msg   INTEGER,
    created_at  INTEGER,
    updated_at  INTEGER
);
CREATE TABLE IF NOT EXISTS knowledge (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    chat_title  TEXT,
    user_id     INTEGER,
    username    TEXT,
    text        TEXT,
    created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_knowledge_time ON knowledge(created_at);
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT,
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    role        TEXT,
    content     TEXT,
    created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
CREATE TABLE IF NOT EXISTS violations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    chat_title  TEXT,
    user_id     INTEGER,
    username    TEXT,
    target      TEXT,
    word        TEXT,
    text        TEXT,
    lang        TEXT,
    method      TEXT,
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS seen (
    chat_id     INTEGER,
    message_id  INTEGER,
    role        TEXT,
    ref         INTEGER,
    created_at  INTEGER,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id     INTEGER PRIMARY KEY,
    kind        TEXT,
    title       TEXT,
    username    TEXT,
    msg_count   INTEGER DEFAULT 0,
    last_seen   INTEGER,
    created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chats_seen ON chats(last_seen);
CREATE TABLE IF NOT EXISTS broadcasts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER,
    admin_name  TEXT,
    text        TEXT,
    targets     TEXT,
    sent        INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'draft',
    created_at  INTEGER,
    finished_at INTEGER
);
"""


class Store:
    def __init__(self, path=None):
        self.path = path or config.DB_PATH
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._drop_legacy()
            self._migrate()
            self.conn.commit()

    def _add_missing(self, table, columns):
        """Добавляет недостающие поля в старую таблицу, если её ещё нет — молчит."""
        try:
            cols = [r["name"] for r in self.q("PRAGMA table_info(%s)" % table)]
        except Exception as exc:
            log.info("миграция %s: %s", table, exc)
            return
        if not cols:
            return
        for col, ctype in columns:
            if col in cols:
                continue
            try:
                self.x("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ctype))
                log.info("в базу добавлено поле %s.%s", table, col)
            except Exception as exc:
                log.info("не смог добавить поле %s.%s: %s", table, col, exc)

    def _migrate(self):
        """Добавляет новые поля в старую базу: модерация, заявки на помощь."""
        self._add_missing("violations", [("lang", "TEXT"), ("method", "TEXT")])
        # заявки: категория (subject), карточка у админа и время обновления
        self._add_missing("tickets", [("subject", "TEXT"), ("admin_msg", "INTEGER"),
                                      ("updated_at", "INTEGER")])

    def _drop_legacy(self):
        """Убирает таблицы удалённых разделов (мини-игра «Шахта» больше не нужна)."""
        try:
            rows = self.q("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name LIKE 'mine\\_%' ESCAPE '\\'")
        except Exception as exc:
            log.info("уборка старых таблиц: %s", exc)
            return
        for row in rows:
            name = row["name"] if isinstance(row, sqlite3.Row) else row[0]
            try:
                self.x("DROP TABLE IF EXISTS %s" % name)
                log.info("удалена таблица удалённого раздела: %s", name)
            except Exception as exc:
                log.info("не смог удалить таблицу %s: %s", name, exc)

    # ---------- общее ----------
    def q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            cur.close()
            return rows

    def x(self, sql, args=()):
        with self.lock:
            for attempt in range(4):
                try:
                    cur = self.conn.execute(sql, args)
                    self.conn.commit()
                    lastrow = cur.lastrowid
                    cur.close()
                    return lastrow
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 3:
                        raise
                    time.sleep(0.4 * (attempt + 1))

    # ---------- настройки ----------
    def get(self, key, default=None):
        rows = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set(self, key, value):
        self.x("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def server_addr(self):
        addrs = self.server_addrs()
        return addrs[0] if addrs else ""

    def server_addrs(self):
        raw = (self.get("server_addr") or "").strip()
        if not raw:
            raw = config.DEFAULT_SERVER_ADDRS
        from . import mc
        return mc.split_addrs(raw)

    def bedrock_addr(self):
        raw = (self.get("bedrock_addr") or "").strip()
        return raw or config.DEFAULT_BEDROCK_ADDR

    # ---------- настройки поведения ----------
    def flag(self, key, default="off"):
        val = self.get(key)
        return (val if val not in (None, "") else default).lower()

    def set_flag(self, key, value):
        self.set(key, value)

    def cooldown_sec(self):
        try:
            return max(0, int(self.get("cooldown") or config.GROUP_COOLDOWN))
        except Exception:
            return config.GROUP_COOLDOWN

    # ---------- список администраторов ----------
    def admins(self):
        """Список админов: @username или числовой id. Первым — владелец."""
        raw = self.get("admins")
        if raw is None:
            items = list(config.DEFAULT_ADMINS)
        else:
            items = [a.strip() for a in raw.split(",") if a.strip()]
        if str(config.SUPER_ADMIN) not in items:
            items.insert(0, str(config.SUPER_ADMIN))
        return items

    def admin_ids(self):
        """Числовые id админов — для личных сообщений (@username ищем в users)."""
        ids = []
        for who in self.admins():
            uid = None
            if who.isdigit():
                uid = int(who)
            elif who.startswith("@"):
                rows = self.q("SELECT user_id FROM users WHERE lower(username)=?",
                              (who[1:].lower(),))
                if rows:
                    uid = int(rows[0]["user_id"])
            if uid and uid not in ids:
                ids.append(uid)
        return ids

    def add_admin(self, who):
        items = self.admins()
        if who in items:
            return False
        items.append(who)
        self.set("admins", ",".join(items))
        return True

    def del_admin(self, who):
        items = [a for a in self.admins() if a != who and a != str(config.SUPER_ADMIN)]
        self.set("admins", ",".join(items))
        return True

    def is_admin(self, uid, username=None):
        if uid == config.SUPER_ADMIN:
            return True
        items = self.admins()
        if str(uid) in items:
            return True
        if username and ("@" + username.lower()) in [a.lower() for a in items]:
            return True
        return False

    # ---------- пользователи ----------
    def touch_user(self, user, chat_id=None, is_admin=False):
        if not user or not user.get("id"):
            return
        uid = user.get("id")
        now = int(time.time())
        self.x(
            "INSERT INTO users(user_id,username,first_name,last_name,chat_id,is_admin,"
            "created_at,seen_at,msg_count) VALUES(?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, first_name=excluded.first_name, "
            "last_name=excluded.last_name, chat_id=excluded.chat_id, "
            "seen_at=excluded.seen_at, msg_count=users.msg_count+1, "
            "is_admin=CASE WHEN excluded.is_admin > users.is_admin THEN excluded.is_admin "
            "ELSE users.is_admin END",
            (uid, user.get("username"), user.get("first_name"), user.get("last_name"),
             chat_id, 1 if (is_admin or uid == config.SUPER_ADMIN) else 0, now, now))

    def user(self, uid):
        rows = self.q("SELECT * FROM users WHERE user_id=?", (uid,))
        return dict(rows[0]) if rows else None

    def all_user_ids(self):
        return [r["user_id"] for r in self.q("SELECT user_id FROM users WHERE banned=0")]

    def count(self, table):
        try:
            return self.q("SELECT COUNT(*) c FROM %s" % table)[0]["c"]
        except Exception:
            return 0

    # ---------- обращения ----------
    def new_ticket(self, user, kind, body, subject=""):
        now = int(time.time())
        return self.x(
            "INSERT INTO tickets(user_id,username,kind,subject,body,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'new',?,?)",
            (user.get("id"), user.get("username") or user.get("first_name"),
             kind, subject, body, now, now))

    def ticket(self, tid):
        rows = self.q("SELECT * FROM tickets WHERE id=?", (tid,))
        return dict(rows[0]) if rows else None

    def ticket_by_admin_msg(self, admin_msg):
        rows = self.q("SELECT * FROM tickets WHERE admin_msg=? ORDER BY id DESC LIMIT 1",
                      (admin_msg,))
        return dict(rows[0]) if rows else None

    def set_admin_msg(self, tid, admin_msg):
        self.x("UPDATE tickets SET admin_msg=? WHERE id=?", (admin_msg, tid))

    def set_ticket_status(self, tid, status, answer=""):
        self.x("UPDATE tickets SET status=?, answer=?, updated_at=? WHERE id=?",
               (status, answer, int(time.time()), tid))

    def close_ticket(self, tid, answer=""):
        self.x("UPDATE tickets SET status='answered', answer=?, updated_at=? WHERE id=?",
               (answer, int(time.time()), tid))

    def open_tickets(self, limit=10):
        return [dict(r) for r in self.q(
            "SELECT * FROM tickets WHERE status NOT IN "
            "('answered','accepted','rejected','closed') ORDER BY id DESC LIMIT ?", (limit,))]

    def recent_tickets(self, limit=10):
        return [dict(r) for r in self.q(
            "SELECT * FROM tickets ORDER BY id DESC LIMIT ?", (limit,))]

    def user_tickets(self, uid, limit=5):
        return [dict(r) for r in self.q(
            "SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))]

    def user_open_ticket(self, uid, kind_prefix=""):
        """Последняя незакрытая заявка игрока (опционально — по типу)."""
        rows = self.q(
            "SELECT * FROM tickets WHERE user_id=? AND status NOT IN "
            "('answered','accepted','rejected','closed') "
            "AND kind LIKE ? ORDER BY id DESC LIMIT 1", (uid, kind_prefix + "%"))
        return dict(rows[0]) if rows else None

    def append_ticket(self, tid, extra):
        """Дополнение от игрока к своей заявке."""
        row = self.ticket(tid)
        if not row:
            return False
        body = (row.get("body") or "").rstrip()
        body = (body + "\n\nДополнение: " + extra.strip()) if body else ("Дополнение: " + extra.strip())
        self.x("UPDATE tickets SET body=?, updated_at=? WHERE id=?",
               (body, int(time.time()), tid))
        return True

    def set_ticket_msgs(self, tid, items):
        """Куда отправлены карточки заявки: [[chat_id, message_id], ...]."""
        try:
            self.set("tmsgs:%d" % tid, json.dumps(items))
        except Exception:
            pass

    def ticket_msgs(self, tid):
        raw = self.get("tmsgs:%d" % tid)
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except Exception:
            return []
        out = []
        for item in items:
            try:
                out.append([int(item[0]), int(item[1])])
            except (TypeError, ValueError, IndexError):
                continue
        return out

    # ---------- база знаний группы ----------
    def add_knowledge(self, chat_id, chat_title, user, text):
        if not text or len(text.strip()) < 2:
            return
        self.x("INSERT INTO knowledge(chat_id,chat_title,user_id,username,text,created_at) "
               "VALUES(?,?,?,?,?,?)",
               (chat_id, chat_title, (user or {}).get("id"),
                (user or {}).get("username") or (user or {}).get("first_name"),
                text.strip()[:1500], int(time.time())))
        total = self.count("knowledge")
        if total > config.KNOWLEDGE_LIMIT:
            self.x("DELETE FROM knowledge WHERE id IN "
                   "(SELECT id FROM knowledge ORDER BY id LIMIT ?)", (total - config.KNOWLEDGE_LIMIT,))

    def search_knowledge(self, words, limit=10):
        if not words:
            rows = self.q("SELECT username,text,created_at FROM knowledge "
                          "ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]
        found = []
        seen = set()
        for w in words[:6]:
            if len(w) < 3:
                continue
            rows = self.q(
                "SELECT username,text,created_at FROM knowledge WHERE text LIKE ? "
                "ORDER BY id DESC LIMIT ?", ("%" + w + "%", limit))
            for r in rows:
                key = r["text"][:80]
                if key in seen:
                    continue
                seen.add(key)
                found.append(dict(r))
                if len(found) >= limit:
                    return found
        return found

    # ---------- факты ----------
    def add_fact(self, text):
        return self.x("INSERT INTO facts(text,created_at) VALUES(?,?)", (text, int(time.time())))

    def facts(self, limit=40):
        return [dict(r) for r in self.q("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,))]

    def del_fact(self, fid):
        self.x("DELETE FROM facts WHERE id=?", (fid,))

    # ---------- свои слова в фильтре оскорблений ----------
    def bad_words(self):
        raw = self.get("badwords") or ""
        return [w.strip().lower() for w in raw.split(",") if w.strip()]

    def add_bad_word(self, word):
        word = (word or "").strip().lower()
        if not word or len(word) < 2:
            return False
        items = self.bad_words()
        if word in items:
            return False
        items.append(word)
        self.set("badwords", ",".join(items))
        return True

    def del_bad_word(self, word):
        word = (word or "").strip().lower()
        items = [w for w in self.bad_words() if w != word]
        self.set("badwords", ",".join(items))
        return True

    # ---------- нарушения ----------
    def add_violation(self, chat_id, chat_title, user, target, word, text,
                      lang="", method=""):
        return self.x(
            "INSERT INTO violations(chat_id,chat_title,user_id,username,target,word,"
            "text,lang,method,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (chat_id, chat_title, (user or {}).get("id"),
             (user or {}).get("username") or (user or {}).get("first_name"),
             target, word, (text or "")[:600], lang or "", method or "",
             int(time.time())))

    def violations(self, limit=10):
        return [dict(r) for r in self.q(
            "SELECT * FROM violations ORDER BY id DESC LIMIT ?", (limit,))]

    def chat_names(self, chat_id, limit=60):
        """Имена тех, кто писал в чат — нужны, чтобы понять, оскорбление в адрес."""
        order = " ORDER BY seen_at DESC" if self._has_seen_at() else ""
        rows = self.q("SELECT username, first_name, last_name FROM users "
                      "WHERE chat_id=?" + order + " LIMIT ?", (chat_id, limit))
        names = []
        for r in rows:
            for key in ("username", "first_name", "last_name"):
                value = (r[key] or "").strip()
                if len(value) >= 3:
                    names.append(value.lower())
        return names

    def _has_seen_at(self):
        if getattr(self, "_seen_at_cache", None) is None:
            cols = [r["name"] for r in self.q("PRAGMA table_info(users)")]
            self._seen_at_cache = "seen_at" in cols
        return self._seen_at_cache

    def user_violations(self, uid, limit=100):
        return self.q("SELECT COUNT(*) c FROM violations WHERE user_id=?", (uid,))[0]["c"]

    # ---------- диалоги ----------
    def remember(self, chat_id, role, content):
        self.x("INSERT INTO history(chat_id,role,content,created_at) VALUES(?,?,?,?)",
               (chat_id, role, content[:4000], int(time.time())))
        self.x("DELETE FROM history WHERE chat_id=? AND id NOT IN "
               "(SELECT id FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
               (chat_id, chat_id, config.HISTORY_TURNS * 2))

    def history(self, chat_id):
        rows = self.q("SELECT role,content FROM history WHERE chat_id=? ORDER BY id", (chat_id,))
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def clear_history(self, chat_id):
        self.x("DELETE FROM history WHERE chat_id=?", (chat_id,))

    # ---------- реестр чатов (для рассылки) ----------
    def touch_chat(self, chat, user=None):
        """Запоминает чат, в котором бот что-то видел: нужен для рассылки."""
        if not chat or not chat.get("id"):
            return
        cid = chat.get("id")
        kind = chat.get("type") or ("private" if cid > 0 else "group")
        title = (chat.get("title") or "").strip()
        if not title:
            title = " ".join(x for x in [chat.get("first_name"), chat.get("last_name")] if x).strip()
        if not title and user:
            title = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip()
        username = (chat.get("username") or (user or {}).get("username") or "").strip()
        if not title:
            title = ("@" + username) if username else str(cid)
        now = int(time.time())
        self.x(
            "INSERT INTO chats(chat_id,kind,title,username,msg_count,last_seen,created_at) "
            "VALUES(?,?,?,?,1,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
            "kind=excluded.kind, "
            "title=CASE WHEN excluded.title IS NOT NULL AND excluded.title <> '' "
            "THEN excluded.title ELSE chats.title END, "
            "username=CASE WHEN excluded.username IS NOT NULL AND excluded.username <> '' "
            "THEN excluded.username ELSE chats.username END, "
            "msg_count=chats.msg_count+1, last_seen=excluded.last_seen",
            (cid, kind, title, username, now, now))

    def chats(self, only_groups=False, limit=800):
        sql = "SELECT * FROM chats"
        if only_groups:
            sql += " WHERE kind IN ('group','supergroup','channel')"
        sql += " ORDER BY last_seen DESC LIMIT ?"
        return [dict(r) for r in self.q(sql, (limit,))]

    def chat(self, chat_id):
        rows = self.q("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
        return dict(rows[0]) if rows else None

    def chat_name(self, chat_id):
        row = self.chat(chat_id)
        if row and row.get("title"):
            return row["title"]
        info = self.user(chat_id)
        if info:
            return info.get("username") or info.get("first_name") or str(chat_id)
        return str(chat_id)

    # ---------- рассылки ----------
    def add_broadcast(self, admin_id, admin_name, text, targets, status="running"):
        return self.x(
            "INSERT INTO broadcasts(admin_id,admin_name,text,targets,sent,failed,status,created_at) "
            "VALUES(?,?,?,?,0,0,?,?)",
            (admin_id, admin_name, (text or "")[:1000],
             json.dumps(list(targets), ensure_ascii=False), status, int(time.time())))

    def finish_broadcast(self, bid, status, sent, failed):
        self.x("UPDATE broadcasts SET status=?, sent=?, failed=?, finished_at=? WHERE id=?",
               (status, sent, failed, int(time.time()), bid))

    def broadcast_history(self, limit=5):
        return [dict(r) for r in self.q(
            "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------- привязка сообщений ----------
    def link(self, chat_id, message_id, role, ref=0):
        self.x("INSERT OR REPLACE INTO seen(chat_id,message_id,role,ref,created_at) "
               "VALUES(?,?,?,?,?)", (chat_id, message_id, role, ref, int(time.time())))

    def lookup(self, chat_id, message_id):
        rows = self.q("SELECT role,ref FROM seen WHERE chat_id=? AND message_id=?",
                      (chat_id, message_id))
        return dict(rows[0]) if rows else None

    def stats(self):
        return {
            "users": self.count("users"),
            "tickets": self.count("tickets"),
            "open": len(self.open_tickets(100)),
            "knowledge": self.count("knowledge"),
            "facts": self.count("facts"),
            "violations": self.count("violations"),
        }

    def close(self):
        """Закрыть соединение. Нужно в самопроверке: Windows не даёт удалить открытый файл."""
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass
