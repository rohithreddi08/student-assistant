"""Small helpers to understand dates/times like 'tomorrow 5pm', 'friday', '12/11', '17:30'."""
import re
from datetime import datetime, timedelta

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_SHORT = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_day(word):
    w = word.strip().lower()
    for i, (full, short) in enumerate(zip(DAY_NAMES, DAY_SHORT)):
        if w == full or w == short or w.startswith(short):
            return i
    return None


TIME_RE = re.compile(r"\b(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm)?\b", re.I)


def norm(text):
    """Speech-recogniser spellings -> plain: '6 p.m.' -> '6 pm', '6 o'clock' -> '6:00'."""
    t = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text, flags=re.I)
    t = re.sub(r"\b(\d{1,2})\s*o'?\s?clock\b", r"\1:00", t, flags=re.I)
    return t


REL_RE = re.compile(r"\b(?:in|after)\s+(an?|half an|\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b", re.I)


def parse_relative(text, now=None):
    """'in 10 minutes' / 'after 2 hours' / 'in half an hour' -> datetime or None."""
    m = REL_RE.search(text)
    if not m:
        return None
    n = {"a": 1, "an": 1, "half an": 0.5}.get(m.group(1).lower())
    n = float(m.group(1)) if n is None else n
    mins = n * 60 if m.group(2).lower().startswith(("h")) else n
    return (now or datetime.now()) + timedelta(minutes=round(mins))


def parse_time(text):
    """'5pm' -> '17:00', '9:30 am' -> '09:30', '14:15' -> '14:15'. Returns None if not found."""
    text = norm(text)
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b|\b(\d{1,2})[:.](\d{2})\b", text, re.I):
        if m.group(1):
            h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
            if ap == "pm" and h != 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
        else:
            h, mi = int(m.group(4)), int(m.group(5))
        if 0 <= h < 24 and 0 <= mi < 60:
            return f"{h:02d}:{mi:02d}"
    return None


def parse_date(text, now=None):
    """Finds a date in text. Returns 'YYYY-MM-DD' or None."""
    now = now or datetime.now()
    t = text.lower()
    if "day after tomorrow" in t:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    if "tomorrow" in t or "tmrw" in t:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "today" in t or "tonight" in t:
        return now.strftime("%Y-%m-%d")
    m = re.search(r"in (\d+) days?", t)
    if m:
        return (now + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if "next week" in t:
        return (now + timedelta(days=7)).strftime("%Y-%m-%d")
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:  # Indian style: day/month[/year]
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        if y < 100:
            y += 2000
        try:
            dt = datetime(y, mo, d)
            if not m.group(3) and dt.date() < now.date():
                dt = datetime(y + 1, mo, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    for word in re.findall(r"[a-z]+", t):
        idx = parse_day(word) if len(word) >= 3 else None
        if idx is not None and word in DAY_NAMES + DAY_SHORT:
            ahead = (idx - now.weekday()) % 7 or 7
            return (now + timedelta(days=ahead)).strftime("%Y-%m-%d")
    return None


def parse_due(text, default_time="23:59", now=None):
    """Returns 'YYYY-MM-DD HH:MM' or None."""
    now = now or datetime.now()
    rel = parse_relative(text, now)
    if rel:
        return rel.strftime("%Y-%m-%d %H:%M")
    text = norm(text)
    d = parse_date(text, now)
    tm = parse_time(text)
    if not d and not tm:
        return None
    if not d:
        d = now.strftime("%Y-%m-%d")
        if tm and tm < now.strftime("%H:%M"):
            d = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{d} {tm or default_time}"


def strip_when(text):
    """Remove date/time words so 'submit lab record by friday 5pm' -> 'submit lab record'."""
    t = REL_RE.sub("", norm(text))
    t = re.sub(r"\b(by|on|at|before|due|for)\s+(?=(today|tomorrow|tonight|next|in \d|\d|mon|tue|wed|thu|fri|sat|sun))", "", t, flags=re.I)
    t = re.sub(r"\b(day after tomorrow|tomorrow|today|tonight|tmrw|next week|in \d+ days?)\b", "", t, flags=re.I)
    t = re.sub(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b", "", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}(?:[:.]\d{2})?\s*(am|pm)\b|\b\d{1,2}[:.]\d{2}\b", "", t, flags=re.I)
    t = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", "", t)
    return re.sub(r"\s+", " ", t).strip(" ,.-")


def to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_minutes(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def nice_due(due):
    """'2026-11-10 17:00' -> 'Tue 10 Nov, 5:00 PM (tomorrow)'."""
    if not due:
        return ""
    try:
        dt = datetime.strptime(due, "%Y-%m-%d %H:%M")
    except ValueError:
        return due
    days = (dt.date() - datetime.now().date()).days
    rel = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days, f"in {days} days" if days > 0 else f"{-days} days ago")
    return f"{dt.strftime('%a %d %b, %I:%M %p')} ({rel})"
