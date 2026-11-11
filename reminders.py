"""Background loop: checks every 30 s and sends phone notifications (and speaks them)."""
import threading
import time
from datetime import datetime, timedelta

import db
import phone
from timeparse import nice_due

DEADLINE_ALERTS = [(24 * 60, "tomorrow"), (3 * 60, "in 3 hours"), (60, "in 1 hour")]


def _alert(key, title, text, speak_text=None, nid=None):
    if db.reminder_sent(key):
        return
    db.mark_reminder(key)
    phone.notify(title, text, nid)
    if db.get_settings().get("speak_reminders") == "1":
        phone.speak(speak_text or text)


def morning_brief(now):
    s = db.get_settings()
    classes = db.classes_on(now.strftime("%Y-%m-%d"), now.weekday())
    dls = [d for d in db.open_deadlines() if d["due"][:10] <= (now + timedelta(days=2)).strftime("%Y-%m-%d")]
    tasks = db.open_tasks()
    parts = [f"Good morning {s.get('name', '')}."]
    if classes:
        parts.append(f"You have {len(classes)} classes today. First is {classes[0]['subject']} at {classes[0]['start']}"
                     + (f" in {classes[0]['room']}." if classes[0]["room"] else "."))
    else:
        parts.append("No classes today.")
    if dls:
        parts.append("Due soon: " + ", ".join(d["title"] for d in dls[:3]) + ".")
    if tasks:
        parts.append(f"{len(tasks)} things on your to-do list.")
    return " ".join(parts)


def check(now=None):
    now = now or datetime.now()
    s = db.get_settings()
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")

    # Morning brief
    if s.get("morning_brief") and hm >= s["morning_brief"] and hm < "12:00":
        text = morning_brief(now)
        _alert(f"brief:{today}", "Today's plan", text, nid=1)

    # Classes
    lead = int(s.get("class_remind_min", 10))
    for c in db.classes_on(today, now.weekday()):
        start = datetime.strptime(f"{today} {c['start']}", "%Y-%m-%d %H:%M")
        mins = (start - now).total_seconds() / 60
        if 0 <= mins <= lead:
            where = f" in {c['room']}" if c["room"] else ""
            who = f" with {c['teacher']}" if c["teacher"] else ""
            _alert(f"class:{c['id']}:{today}", f"Class at {c['start']}: {c['subject']}",
                   f"{c['subject']}{where}{who} starts in {int(mins)} min (till {c['end']}).",
                   f"{c['subject']} class starts in {int(mins)} minutes{where}.", nid=100 + c["id"])

    # Deadlines
    for d in db.open_deadlines():
        try:
            due = datetime.strptime(d["due"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        mins = (due - now).total_seconds() / 60
        for limit, label in DEADLINE_ALERTS:
            if 0 <= mins <= limit:
                _alert(f"dl:{d['id']}:{limit}", f"{d['kind'].title()} due {label}",
                       f"{d['title']} ({d['subject']}) — {nice_due(d['due'])}",
                       f"Reminder: {d['title']} is due {label}.", nid=500 + d["id"])
                break

    # To-do tasks with a time
    for t in db.open_tasks():
        if not t["due"]:
            continue
        try:
            due = datetime.strptime(t["due"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        mins = (due - now).total_seconds() / 60
        if -1 <= mins <= 15:
            _alert(f"task:{t['id']}", "To-do reminder", t["title"], f"Reminder: {t['title']}", nid=2000 + t["id"])

    # Study sessions
    lead = int(s.get("study_remind_min", 5))
    for st in db.study_between(today, today):
        if st["done"]:
            continue
        start = datetime.strptime(f"{today} {st['start']}", "%Y-%m-%d %H:%M")
        mins = (start - now).total_seconds() / 60
        if 0 <= mins <= lead:
            _alert(f"study:{st['id']}", f"Study time: {st['subject']}",
                   f"{st['start']}-{st['end']} · {st['focus']}",
                   f"Time to study {st['subject']}. {st['focus']}", nid=3000 + st["id"])


def loop():
    while True:
        try:
            check()
        except Exception as e:  # never let the reminder thread die
            print("[reminders] error:", e)
        time.sleep(30)


def start():
    threading.Thread(target=loop, daemon=True).start()
