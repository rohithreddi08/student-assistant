"""SQLite storage for timetable, tasks, deadlines, study, memories, profile and settings.

Every synced row carries a global `uid`, an `updated` timestamp (UTC) and a `deleted` flag,
so two copies of ULTRON (phone + laptop) can merge their data: newest change wins.
"""
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get("ASSISTANT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.db"))
_lock = threading.RLock()

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    day INTEGER,              -- 0=Monday .. 6=Sunday (weekly class)
    date TEXT,                -- YYYY-MM-DD (one-time / extra class); overrides day
    start TEXT NOT NULL,      -- HH:MM
    end TEXT NOT NULL,        -- HH:MM
    room TEXT DEFAULT '',
    teacher TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT,                 -- 'YYYY-MM-DD HH:MM' or NULL
    priority INTEGER DEFAULT 2, -- 1 high, 2 normal, 3 low
    done INTEGER DEFAULT 0,
    created TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    kind TEXT DEFAULT 'assignment', -- assignment | submission | exam | project | other
    due TEXT NOT NULL,        -- 'YYYY-MM-DD HH:MM'
    notes TEXT DEFAULT '',
    done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS study (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    subject TEXT NOT NULL,
    focus TEXT DEFAULT '',
    done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (     -- methods ULTRON taught itself after solving a new kind of task
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trigger TEXT DEFAULT '',
    method TEXT NOT NULL,
    uses INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sent_reminders (
    key TEXT PRIMARY KEY,
    sent_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS settings (   -- synced: settings, profile.*, owner.*
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS local (      -- never synced: sync peer, keys for this device
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

SYNC_TABLES = {
    "classes": ["subject", "day", "date", "start", "end", "room", "teacher"],
    "tasks": ["title", "due", "priority", "done", "created"],
    "deadlines": ["title", "subject", "kind", "due", "notes", "done"],
    "study": ["date", "start", "end", "subject", "focus", "done"],
    "memories": ["fact"],
    "skills": ["name", "trigger", "method", "uses"],
}

DEFAULT_SETTINGS = {
    "name": "Boss",
    "class_remind_min": "10",
    "study_remind_min": "5",
    "study_start": "18:00",
    "study_end": "22:00",
    "session_min": "50",
    "break_min": "10",
    "speak_reminders": "1",
    "morning_brief": "07:00",
    "voice_lang": "en-IN",
    "gemini_mode": "ask",   # ask = only when you say "use Gemini"; auto = whenever I don't know a command
}

PROFILE_FIELDS = ["full_name", "nickname", "dob", "gender", "phone", "email", "college", "course", "year",
                  "roll_no", "city", "address", "emergency_contact", "languages", "hobbies", "goals", "about"]


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]


def new_uid():
    return uuid.uuid4().hex


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn = connect()


def _columns(table):
    return {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init():
    with _lock:
        _conn.executescript(SCHEMA)
        # Migrate old databases: add sync columns and give existing rows a uid
        for table in list(SYNC_TABLES) + ["settings"]:
            cols = _columns(table)
            if "uid" not in cols and table != "settings":
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")
            if "updated" not in cols:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN updated TEXT")
            if "deleted" not in cols and table != "settings":
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted INTEGER DEFAULT 0")
            if table != "settings":
                for (rid,) in _conn.execute(f"SELECT id FROM {table} WHERE uid IS NULL").fetchall():
                    _conn.execute(f"UPDATE {table} SET uid = ? WHERE id = ?", (new_uid(), rid))
                _conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid ON {table}(uid)")
            _conn.execute(f"UPDATE {table} SET updated = '2000-01-01T00:00:00.000' WHERE updated IS NULL")
        for k, v in DEFAULT_SETTINGS.items():
            # Old default timestamp so a value set on the other device always wins over a fresh default
            _conn.execute("INSERT OR IGNORE INTO settings(key, value, updated) VALUES (?, ?, '2000-01-01T00:00:00.000')",
                          (k, v))
        _conn.commit()


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


def insert(table, **fields):
    """Insert a synced row (adds uid + updated). Returns the local id."""
    fields = {k: v for k, v in fields.items() if k in SYNC_TABLES[table]}
    fields["uid"] = new_uid()
    fields["updated"] = now_utc()
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    return execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(fields.values()))


# ---------- Settings / profile / owner ----------
def get_settings():
    """Settings + profile (no biometric data)."""
    return {r["key"]: r["value"] for r in query("SELECT key, value FROM settings WHERE key NOT LIKE 'owner.%'")}


def get_setting(key, default=None):
    rows = query("SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


def set_setting(key, value):
    execute("INSERT OR REPLACE INTO settings(key, value, updated) VALUES (?, ?, ?)",
            (key, "" if value is None else str(value), now_utc()))


def get_profile():
    s = get_settings()
    return {f: s.get("profile." + f, "") for f in PROFILE_FIELDS}


def get_local(key, default=None):
    rows = query("SELECT value FROM local WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


def set_local(key, value):
    execute("INSERT OR REPLACE INTO local(key, value) VALUES (?, ?)", (key, "" if value is None else str(value)))


# ---------- Classes ----------
def add_class(subject, start, end, day=None, date=None, room="", teacher=""):
    return insert("classes", subject=subject, day=day, date=date, start=start, end=end,
                  room=room or "", teacher=teacher or "")


def classes_on(date_str, weekday):
    """Weekly classes on that weekday + one-time classes on that exact date."""
    return query(
        "SELECT * FROM classes WHERE deleted = 0 AND ((date IS NULL AND day = ?) OR date = ?) ORDER BY start",
        (weekday, date_str),
    )


def all_classes():
    return query("SELECT * FROM classes WHERE deleted = 0 ORDER BY COALESCE(date, ''), day, start")


# ---------- Tasks ----------
def add_task(title, due=None, priority=2):
    return insert("tasks", title=title, due=due, priority=priority)


def open_tasks():
    return query("SELECT * FROM tasks WHERE done = 0 AND deleted = 0 ORDER BY due IS NULL, due, priority, id")


def all_tasks():
    return query("SELECT * FROM tasks WHERE deleted = 0 ORDER BY done, due IS NULL, due, priority, id")


# ---------- Deadlines ----------
def add_deadline(title, due, subject="", kind="assignment", notes=""):
    return insert("deadlines", title=title, subject=subject or "", kind=kind or "assignment", due=due, notes=notes or "")


def open_deadlines():
    return query("SELECT * FROM deadlines WHERE done = 0 AND deleted = 0 ORDER BY due")


def all_deadlines():
    return query("SELECT * FROM deadlines WHERE deleted = 0 ORDER BY done, due")


# ---------- Study ----------
def study_between(start_date, end_date):
    return query("SELECT * FROM study WHERE deleted = 0 AND date BETWEEN ? AND ? ORDER BY date, start",
                 (start_date, end_date))


# ---------- Memories (facts ULTRON learned about the owner) ----------
def add_memory(fact):
    return insert("memories", fact=fact)


def memories():
    return query("SELECT id, fact FROM memories WHERE deleted = 0 ORDER BY id")


# ---------- Skills (self-learned methods) ----------
def save_skill(name, trigger, method):
    """Add a skill, or improve the existing one with the same name."""
    cur = query("SELECT id FROM skills WHERE deleted = 0 AND lower(name) = lower(?)", (name,))
    if cur:
        execute("UPDATE skills SET trigger = ?, method = ?, updated = ? WHERE id = ?", (trigger, method, now_utc(), cur[0]["id"]))
        return cur[0]["id"]
    return insert("skills", name=name, trigger=trigger, method=method, uses=0)


def skills():
    return query("SELECT id, name, trigger, method, uses FROM skills WHERE deleted = 0 ORDER BY uses DESC, id")


def skill_used(name):
    execute("UPDATE skills SET uses = uses + 1, updated = ? WHERE deleted = 0 AND lower(name) = lower(?)", (now_utc(), name))


# ---------- Generic ----------
TABLES = set(SYNC_TABLES)


def delete(table, row_id):
    if table not in TABLES:
        raise ValueError("bad table")
    execute(f"UPDATE {table} SET deleted = 1, updated = ? WHERE id = ?", (now_utc(), row_id))


def mark_done(table, row_id, done=1):
    if table not in {"tasks", "deadlines", "study"}:
        raise ValueError("bad table")
    execute(f"UPDATE {table} SET done = ?, updated = ? WHERE id = ?", (done, now_utc(), row_id))


def reminder_sent(key):
    return bool(query("SELECT 1 FROM sent_reminders WHERE key = ?", (key,)))


def mark_reminder(key):
    execute("INSERT OR IGNORE INTO sent_reminders(key) VALUES (?)", (key,))


# ---------- Sync ----------
def export_all():
    data = {}
    for table, cols in SYNC_TABLES.items():
        data[table] = query(f"SELECT uid, updated, deleted, {', '.join(cols)} FROM {table}")
    data["settings"] = query("SELECT key, value, updated FROM settings")
    return data


def merge(data):
    """Merge rows from the other device. Newest `updated` wins. Returns number of changed rows."""
    changed = 0
    with _lock:
        for table, cols in SYNC_TABLES.items():
            for row in data.get(table) or []:
                uid, upd = row.get("uid"), row.get("updated") or ""
                if not uid:
                    continue
                cur = _conn.execute(f"SELECT updated FROM {table} WHERE uid = ?", (uid,)).fetchone()
                vals = [row.get(c) for c in cols] + [upd, int(row.get("deleted") or 0)]
                if cur is None:
                    _conn.execute(f"INSERT INTO {table}({', '.join(cols)}, updated, deleted, uid) "
                                  f"VALUES ({', '.join('?' * (len(cols) + 3))})", vals + [uid])
                    changed += 1
                elif upd > (cur["updated"] or ""):
                    sets = ", ".join(f"{c} = ?" for c in cols)
                    _conn.execute(f"UPDATE {table} SET {sets}, updated = ?, deleted = ? WHERE uid = ?", vals + [uid])
                    changed += 1
        for row in data.get("settings") or []:
            key, upd = row.get("key"), row.get("updated") or ""
            if not key:
                continue
            cur = _conn.execute("SELECT updated FROM settings WHERE key = ?", (key,)).fetchone()
            if cur is None or upd > (cur["updated"] or ""):
                _conn.execute("INSERT OR REPLACE INTO settings(key, value, updated) VALUES (?, ?, ?)",
                              (key, row.get("value"), upd))
                changed += 1
        _conn.commit()
    return changed
