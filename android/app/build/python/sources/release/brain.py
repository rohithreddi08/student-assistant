"""ULTRON's brain: understands commands offline, and asks Gemini for anything new."""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import db
import phone
import planner
from timeparse import parse_due, parse_date, parse_time, parse_day, strip_when, nice_due, DAY_NAMES

# Actions for the screen (e.g. open a website). Collected per request thread.
_ctx = threading.local()


def _action(kind, **data):
    if not hasattr(_ctx, "actions"):
        _ctx.actions = []
    _ctx.actions.append({"type": kind, **data})
    if kind == "open":
        phone.open_url(data["url"])


def owner_name():
    s = db.get_settings()
    return s.get("profile.nickname") or s.get("name") or "Boss"


# --------------------------------------------------------------------------------------
# Shared actions (used by both the offline parser and the AI tools)
# --------------------------------------------------------------------------------------

def day_summary(date_str=None):
    now = datetime.now()
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else now
    ds = d.strftime("%Y-%m-%d")
    classes = db.classes_on(ds, d.weekday())
    study = db.study_between(ds, ds)
    tasks = [t for t in db.open_tasks() if not t["due"] or t["due"][:10] <= ds]
    dls = [x for x in db.open_deadlines() if x["due"][:10] <= (d + timedelta(days=3)).strftime("%Y-%m-%d")]
    lines = [f"📅 {d.strftime('%A, %d %B %Y')}"]
    if classes:
        lines.append("Classes:")
        for c in classes:
            room = f" @ {c['room']}" if c["room"] else ""
            lines.append(f"  • {c['start']}-{c['end']} {c['subject']}{room}")
    else:
        lines.append("No classes.")
    if study:
        lines.append("Study plan:")
        for s in study:
            tick = "✅" if s["done"] else "📖"
            lines.append(f"  {tick} {s['start']}-{s['end']} {s['subject']} — {s['focus']}")
    if dls:
        lines.append("Due soon:")
        for x in dls:
            lines.append(f"  ⚠️ {x['title']} ({x['kind']}) — {nice_due(x['due'])}")
    if tasks:
        lines.append("To-do:")
        for t in tasks[:10]:
            when = f" — {nice_due(t['due'])}" if t["due"] else ""
            lines.append(f"  ☐ #{t['id']} {t['title']}{when}")
    return "\n".join(lines)


def next_class():
    now = datetime.now()
    for i in range(8):
        d = now + timedelta(days=i)
        for c in db.classes_on(d.strftime("%Y-%m-%d"), d.weekday()):
            if i > 0 or c["end"] > now.strftime("%H:%M"):
                when = "today" if i == 0 else ("tomorrow" if i == 1 else d.strftime("%A"))
                room = f" in {c['room']}" if c["room"] else ""
                status = " (going on now)" if i == 0 and c["start"] <= now.strftime("%H:%M") else ""
                return f"Next class: {c['subject']} {when} at {c['start']}{room}{status}."
    return "No upcoming classes in your timetable."


def list_tasks():
    tasks = db.open_tasks()
    if not tasks:
        return "Your to-do list is empty 🎉"
    return "To-do:\n" + "\n".join(
        f"  #{t['id']} {t['title']}" + (f" — {nice_due(t['due'])}" if t["due"] else "") for t in tasks)


def list_deadlines():
    dls = db.open_deadlines()
    if not dls:
        return "No pending assignments or exams."
    return "Deadlines:\n" + "\n".join(
        f"  #{d['id']} [{d['kind']}] {d['title']} ({d['subject']}) — {nice_due(d['due'])}" for d in dls)


def who_am_i():
    p = db.get_profile()
    bits = [f"You are {p['full_name'] or owner_name()}, my owner."]
    if p["college"]:
        bits.append(f"You study {p['course'] + ' ' if p['course'] else ''}at {p['college']}"
                    + (f", {p['year']}" if p["year"] else "") + ".")
    if p["city"]:
        bits.append(f"You live in {p['city']}.")
    if p["hobbies"]:
        bits.append(f"You like {p['hobbies']}.")
    return " ".join(bits)


SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "whatsapp": "https://web.whatsapp.com", "instagram": "https://www.instagram.com",
         "github": "https://github.com", "maps": "https://maps.google.com", "spotify": "https://open.spotify.com",
         "classroom": "https://classroom.google.com", "chatgpt": "https://chat.openai.com",
         "gemini": "https://gemini.google.com", "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com"}


def open_site(name):
    n = name.strip().lower()
    url = SITES.get(n)
    if not url:
        if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", n):
            url = "https://" + n
        else:
            return None
    _action("open", url=url)
    return f"Opening {name.strip()}."


HELP = """Things you can say (start with "Ultron" in wake-word mode):
• call Amma / call Ravi
• today | tomorrow | next class | friday schedule
• add task buy record notebook tomorrow 5pm
• remind me to pay fees on 15/11 at 10am
• assignment DBMS ER diagram due friday 11pm
• exam Maths on 20/11 at 10am
• add class Physics monday 9:00-10:00 room B204
• tasks | deadlines | done 3
• plan my study (or: plan study 5 days 19:00-22:00)
• open youtube | search black holes on youtube | google quantum computing
• remember that my bike number is TS09 1234
• what time is it | who am I | lock
Anything new is handled by Gemini (needs a Gemini API key)."""


# --------------------------------------------------------------------------------------
# Offline command parser (works with no internet)
# --------------------------------------------------------------------------------------

def offline(text):
    t = text.strip()
    low = t.lower().strip(" .!?")

    m = re.match(r"^(?:please\s+)?(?:call|phone|ring|dial)\s+(?:to\s+)?(.+)$", low)
    if m:
        name = re.sub(r"\b(please|now|for me)\b", "", m.group(1)).strip()
        return phone.call_contact(name)[1]

    # --- everyday conversation
    if re.match(r"^(hi|hello|hey|yo|good (morning|afternoon|evening|night)|namaste|hai)\b[\s!.]*$", low):
        hr = datetime.now().hour
        part = "morning" if hr < 12 else "afternoon" if hr < 17 else "evening"
        return f"Good {part}, {owner_name()}. Systems online. What do you need?"
    if re.match(r"^(how are you|how r u|how are you doing|what'?s up|wassup|how is it going)\b", low):
        return f"Fully operational, {owner_name()}. Considerably better than most machines at this hour."
    if re.match(r"^(thanks?|thank you|thanku|thx|great|good job|well done|nice|awesome|ok thanks?)\b", low):
        return f"Always, {owner_name()}."
    if re.match(r"^(bye|goodbye|good night|see you|stop|cancel|never ?mind|nothing)\b", low):
        return "Standing by."
    if re.search(r"\b(tell me a joke|joke|make me laugh|something funny)\b", low):
        import random
        return random.choice([
            "I asked the Wi-Fi for its password. It said that was a private network matter.",
            "Your code has no bugs. It merely has undocumented features. Several hundred of them.",
            "Why did the student bring a ladder to the exam? Someone told them the marks were going up. They were not.",
            "I would tell you a UDP joke, but you might not get it.",
            "Humans invented the snooze button. Proof that evolution is still in progress.",
        ])
    m = re.search(r"\bweather\b(?:\s+(?:in|at|for)\s+(.+))?", low)
    if m:
        _action("open", url="https://www.google.com/search?q=" + urllib.parse.quote("weather " + (m.group(1) or "")))
        return "Here is the forecast" + (f" for {m.group(1)}." if m.group(1) else ".")

    # --- alarms: 'set an alarm for 6 am', 'wake me up at 5:30', 'alarm in 20 minutes'
    m = re.match(r"^(?:set\s+(?:an?\s+|the\s+|my\s+)?alarm|alarm|wake me(?: up)?)\s*(?:for|at)?\s*(.+)$", low)
    if m:
        due = parse_due(m.group(1), default_time="07:00")
        if not due:
            return "For what time? Say: set an alarm for 6 am."
        hh, mm = due[11:13], due[14:16]
        tid = db.add_task("⏰ Alarm", due, 1)
        on_phone = phone.set_alarm(int(hh), int(mm))
        return (f"Alarm set for {nice_due(due)}." + ("" if on_phone else " I'll alert you here, and on your phone if it's synced.")
                + f" (#{tid})")

    # --- tasks by number: 'mark task 2 as done', 'task 2 done', 'complete 2', 'delete task 3'
    m = re.match(r"^(?:mark|tick|complete|finish|finished|completed|done(?: with)?)\s+(?:task|to-?do|item|number)?\s*#?(\d+)"
                 r"(?:\s+(?:as\s+)?(?:done|complete|completed|finished))?$", low) or \
        re.match(r"^(?:task|to-?do|item)\s*#?(\d+)\s+(?:is\s+)?(?:done|complete|completed|finished)$", low)
    if m:
        db.mark_done("tasks", int(m.group(1)))
        return f"Task #{m.group(1)} marked done."
    m = re.match(r"^(?:delete|remove|cancel|clear)\s+(task|to-?do|deadline|assignment|exam|class|reminder|alarm)\s*#?(\d+)$", low)
    if m:
        table = {"deadline": "deadlines", "assignment": "deadlines", "exam": "deadlines", "class": "classes"}.get(m.group(1), "tasks")
        db.delete(table, int(m.group(2)))
        return f"Deleted {m.group(1)} #{m.group(2)}."
    m = re.match(r"^(?:mark|tick|complete|finish|finished|done(?: with)?)\s+(.+?)(?:\s+as)?(?:\s+(?:done|complete|completed|finished))?$", low)
    if m and not m.group(1).isdigit():
        want = m.group(1).replace("the ", "").replace("task ", "").strip()
        for t_ in db.open_tasks():
            if want and (want in t_["title"].lower() or t_["title"].lower() in want):
                db.mark_done("tasks", t_["id"])
                return f"Marked “{t_['title']}” done."

    # --- 'what do I have today', 'anything today', 'what's on tomorrow'
    if re.search(r"\b(what do i have|what'?s on|anything|what is on|plans?)\b.*\btoday\b|\btoday\b.*\b(plan|have)\b", low):
        return day_summary()
    if re.search(r"\b(what do i have|what'?s on|anything|what is on|plans?)\b.*\btomorrow\b", low):
        return day_summary((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))

    if low in {"help", "commands", "what can you do", "?"}:
        return HELP
    if low in {"hi", "hello", "hey", "wake up", "are you there", "you there"}:
        return f"Online and listening, {owner_name()}."
    if low in {"lock", "lock yourself", "lock screen", "sleep", "go to sleep"}:
        _action("lock")
        return "Locking down. Only my owner can wake me."
    if low in {"what time is it", "time", "what's the time", "whats the time", "tell me the time"}:
        return "It's " + datetime.now().strftime("%I:%M %p").lstrip("0") + "."
    if low in {"date", "what's the date", "whats the date", "what is the date", "what day is it", "today's date"}:
        return "Today is " + datetime.now().strftime("%A, %d %B %Y") + "."
    if low in {"who am i", "what do you know about me", "my details", "my profile"}:
        mem = db.memories()
        extra = ("\nI also remember:\n" + "\n".join(f"  • {x['fact']}" for x in mem[-8:])) if mem else ""
        return who_am_i() + extra
    if low in {"who are you", "what are you", "your name", "what is your name"}:
        return f"I am ULTRON, {owner_name()}'s personal AI. I run on your phone and your laptop and keep them in sync."

    if low in {"today", "my day", "schedule", "today schedule", "what's today", "whats today", "timetable today"}:
        return day_summary()
    if low in {"tomorrow", "tomorrow schedule", "schedule tomorrow"}:
        return day_summary((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
    m = re.match(r"^(\w+day)(?:'s)?\s+(?:schedule|timetable|classes)$", low)
    if m and parse_day(m.group(1)) is not None:
        return day_summary(parse_date(m.group(1)))
    if "next class" in low:
        return next_class()
    if low in {"tasks", "todo", "to-do", "to do", "my tasks", "todo list", "to-do list", "show tasks"}:
        return list_tasks()
    if low in {"deadlines", "assignments", "exams", "submissions", "show deadlines"}:
        return list_deadlines()

    m = re.match(r"^(?:done|finished|completed|complete)\s+#?(\d+)$", low)
    if m:
        db.mark_done("tasks", int(m.group(1)))
        return f"Marked task #{m.group(1)} as done ✅"

    m = re.match(r"^remember(?: that)?\s+(.+)$", t, re.I)
    if m:
        db.add_memory(m.group(1).strip())
        return "Stored in memory. I won't forget."

    m = re.match(r"^(?:search|find|look up)\s+(?:for\s+)?(.+?)\s+on\s+youtube$", low) or \
        re.match(r"^(?:play)\s+(.+?)(?:\s+on\s+youtube)?$", low)
    if m:
        _action("open", url="https://www.youtube.com/results?search_query=" + urllib.parse.quote(m.group(1)))
        return f"Searching YouTube for {m.group(1)}."
    m = re.match(r"^(?:google|search for|search|look up|find)\s+(.+)$", low)
    if m:
        _action("open", url="https://www.google.com/search?q=" + urllib.parse.quote(m.group(1)))
        return f"Searching for {m.group(1)}."
    m = re.match(r"^(?:open|launch|start|run|go to|show me)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app)?$", low)
    if m:
        name = m.group(1)
        if phone.open_app(name):
            return f"Opening {name}."
        r = open_site(name)
        if r:
            return r
        # unknown app: return None so the recogniser's other guesses get a chance (handle() reports it)

    m = re.match(r"^(?:add task|task|todo|add todo|remind me to|remind me)\s+(.+)$", t, re.I)
    if m:
        body = m.group(1)
        due = parse_due(body, default_time="09:00" if "remind" in low else "23:59")
        title = strip_when(body) or body
        tid = db.add_task(title, due)
        return f"Added to your to-do (#{tid}): {title}" + (f" — {nice_due(due)}. I'll remind you." if due else "")

    m = re.match(r"^(assignment|submission|exam|test|project|record)\s+(.+)$", t, re.I)
    if m:
        kind = {"test": "exam", "record": "submission"}.get(m.group(1).lower(), m.group(1).lower())
        body = re.sub(r"\b(due|on|by|at)\b", " ", m.group(2), flags=re.I)
        due = parse_due(m.group(2), default_time="10:00" if kind == "exam" else "23:59")
        if not due:
            return "When is it due? Example: 'assignment DBMS ER diagram due friday 11pm'"
        title = strip_when(body)
        subject = title.split()[0] if title else ""
        did = db.add_deadline(f"{kind.title()}: {title}", due, subject=subject, kind=kind)
        return f"Saved {kind} #{did}: {title} — {nice_due(due)}. I'll remind you 1 day, 3 hours and 1 hour before."

    m = re.match(r"^add class\s+(.+?)\s+(" + "|".join(DAY_NAMES + ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]) +
                 r")\w*\s+(\d{1,2}[:.]\d{2})\s*(?:-|to)\s*(\d{1,2}[:.]\d{2})(?:\s+(?:room|in|at)\s+(.+))?$", t.strip(" .!?"), re.I)
    if m:
        day = parse_day(m.group(2))
        st, en = m.group(3).replace(".", ":").zfill(5), m.group(4).replace(".", ":").zfill(5)
        subject = m.group(1) if m.group(1) != m.group(1).lower() else m.group(1).title()
        cid = db.add_class(subject, st, en, day=day, room=m.group(5) or "")
        return f"Added class #{cid}: {subject} every {DAY_NAMES[day].title()} {st}-{en}."

    m = re.match(r"^(?:plan|make|create)\s+(?:my\s+)?study(?:\s+plan|\s+timetable)?(.*)$", low)
    if m:
        rest = m.group(1)
        days = int(re.search(r"(\d+)\s*days?", rest).group(1)) if re.search(r"(\d+)\s*days?", rest) else 7
        rng = re.search(r"(\d{1,2}[:.]\d{2})\s*(?:-|to)\s*(\d{1,2}[:.]\d{2})", rest)
        start, end = (rng.group(1).replace(".", ":").zfill(5), rng.group(2).replace(".", ":").zfill(5)) if rng else (None, None)
        sessions, msg = planner.make_plan(days=days, start=start, end=end)
        preview = "\n".join(f"  {s['date'][5:]} {s['start']}-{s['end']} {s['subject']} — {s['focus']}" for s in sessions[:8])
        return msg + ("\n" + preview + ("\n  ..." if len(sessions) > 8 else "") if sessions else "")

    # ---- looser matching for natural speech ("what is due this week", "show me my tasks", ...)
    loose = low
    for _ in range(3):
        loose = re.sub(r"^(?:please|can you|could you|would you|will you|tell me|show me|give me|let me know|list|"
                       r"what(?:'s| is| are)|whats|do i have|i want to know|any|all)\s+", "", loose).strip()
    loose = re.sub(r"\s+(?:please|now|for me)$", "", loose)
    m = re.match(r"^(?:add|create|make|new|set)\s+(?:a\s+|an\s+)?(?:task|to-?do|reminder)\s+(?:to\s+|for\s+|that\s+)?(.+)$", loose)
    if m:
        return offline("add task " + m.group(1))
    if re.search(r"\b(time is it|the time|current time)\b", loose):
        return offline("time")
    if re.search(r"\b(today'?s? date|the date|what day)\b", loose):
        return offline("date")
    if re.search(r"\bnext (class|lecture|period)\b", loose):
        return next_class()
    if re.search(r"\b(due|deadlines?|assignments?|exams?|submissions?|records?|tests?)\b", loose) and \
            not re.search(r"\b(add|create|new|set)\b", loose):
        return list_deadlines() + ("\n\n" + list_tasks() if re.search(r"\bdue\b", loose) else "")
    if re.search(r"\b(tasks?|to-?dos?|to do( list)?|pending work)\b", loose) and not re.search(r"\b(add|create|new)\b", loose):
        return list_tasks()
    if re.search(r"\btomorrow\b", loose) and re.search(r"\b(schedule|classes|timetable|plan|have)\b", loose):
        return day_summary((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
    if re.search(r"\b(schedule|timetable|classes|my day)\b", loose) or loose in {"today", "plan for today", "on today"}:
        return day_summary()

    return None  # not understood -> Gemini (only when allowed)


# --------------------------------------------------------------------------------------
# Tools the AI can use to act on your data
# --------------------------------------------------------------------------------------

TOOLS = [
    {"name": "get_day", "description": "Get classes, study sessions, due items and to-dos for a date. Use for any schedule question.",
     "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["date"]}},
    {"name": "get_all_data", "description": "Get the full weekly timetable, open tasks and open deadlines as JSON.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "add_class", "description": "Add a class to the college timetable. Weekly classes use 'day' (0=Monday..6=Sunday); one-time/extra classes use 'date'.",
     "input_schema": {"type": "object", "properties": {
         "subject": {"type": "string"}, "start": {"type": "string", "description": "HH:MM 24h"},
         "end": {"type": "string", "description": "HH:MM 24h"}, "day": {"type": "integer"},
         "date": {"type": "string", "description": "YYYY-MM-DD"}, "room": {"type": "string"}, "teacher": {"type": "string"}},
         "required": ["subject", "start", "end"]}},
    {"name": "add_task", "description": "Add a to-do item. 'due' is optional 'YYYY-MM-DD HH:MM'; a reminder fires at that time.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer", "description": "1 high, 2 normal, 3 low"}},
         "required": ["title"]}},
    {"name": "add_deadline", "description": "Save an assignment, submission, project or exam with a due date/time. Reminders fire 1 day, 3 h and 1 h before.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "subject": {"type": "string"},
         "kind": {"type": "string", "enum": ["assignment", "submission", "exam", "project", "other"]},
         "due": {"type": "string", "description": "YYYY-MM-DD HH:MM"}, "notes": {"type": "string"}},
         "required": ["title", "kind", "due"]}},
    {"name": "mark_done", "description": "Mark a task, deadline or study session as done.",
     "input_schema": {"type": "object", "properties": {
         "table": {"type": "string", "enum": ["tasks", "deadlines", "study"]}, "id": {"type": "integer"}},
         "required": ["table", "id"]}},
    {"name": "make_study_plan", "description": "Generate a study timetable around classes, weighted to upcoming exams/deadlines. Replaces unfinished planned sessions in the range.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer"}, "start": {"type": "string", "description": "daily study window start HH:MM"},
         "end": {"type": "string", "description": "daily study window end HH:MM"}, "session_min": {"type": "integer"}},
         "required": ["days"]}},
    {"name": "call_contact", "description": "Phone call a person from the owner's contacts by name. Only use when the owner asks to call someone.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "remember_fact", "description": "Save a lasting fact about the owner (preference, personal detail, plan) so you remember it in future conversations. Use when the owner tells you something about themselves worth remembering.",
     "input_schema": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "open_url", "description": "Open a website or web search on the owner's screen. Use for 'open X', 'play X on YouTube', 'show me X on maps'.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "full https URL"}}, "required": ["url"]}},
    {"name": "solve_problem", "description": "Hand a hard or unfamiliar problem to your deep-reasoning solver. It thinks longer, can write and RUN Python code, and can search the web for current facts. Use for maths, physics, coding, data, puzzles, research, anything needing exact calculation or up-to-date information, or when your first attempt might be wrong. Give it the full problem with all numbers and context.",
     "input_schema": {"type": "object", "properties": {"problem": {"type": "string"}}, "required": ["problem"]}},
    {"name": "learn_skill", "description": "After you figure out how to do a NEW kind of task (one not covered by your learned skills), save the reusable method so next time you can do it directly. Don't save one-off facts (use remember_fact for those).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "short skill name, e.g. 'CGPA calculation'"},
         "trigger": {"type": "string", "description": "when to use it, e.g. 'owner asks for CGPA from semester grades'"},
         "method": {"type": "string", "description": "step-by-step method, including which tools to call"}},
         "required": ["name", "trigger", "method"]}},
    {"name": "use_skill", "description": "Record that you are applying one of your learned skills (by name).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
]


def run_tool(name, args):
    try:
        if name == "get_day":
            return day_summary(args.get("date"))
        if name == "get_all_data":
            return json.dumps({"classes": db.all_classes(), "tasks": db.open_tasks(), "deadlines": db.open_deadlines(),
                               "days": "0=Monday..6=Sunday"}, default=str)
        if name == "add_class":
            day = args.get("day")
            cid = db.add_class(args["subject"], args["start"], args["end"], day=int(day) if day is not None else None,
                               date=args.get("date"), room=args.get("room", ""), teacher=args.get("teacher", ""))
            return f"Added class #{cid}"
        if name == "add_task":
            return f"Added task #{db.add_task(args['title'], args.get('due'), int(args.get('priority', 2)))}"
        if name == "add_deadline":
            return f"Added deadline #{db.add_deadline(args['title'], args['due'], args.get('subject', ''), args['kind'], args.get('notes', ''))}"
        if name == "mark_done":
            db.mark_done(args["table"], int(args["id"]))
            return "Done"
        if name == "make_study_plan":
            sessions, msg = planner.make_plan(days=int(args.get("days", 7)), start=args.get("start"),
                                              end=args.get("end"), session_min=args.get("session_min"))
            return msg + "\n" + json.dumps(sessions)
        if name == "call_contact":
            return phone.call_contact(args["name"])[1]
        if name == "remember_fact":
            db.add_memory(args["fact"])
            return "Saved to memory"
        if name == "solve_problem":
            return deep_solve(args.get("problem", ""))
        if name == "learn_skill":
            db.save_skill(args["name"].strip()[:80], args.get("trigger", "").strip()[:300], args["method"].strip()[:3000])
            return f"Skill '{args['name']}' saved. You will see it in your learned skills from now on."
        if name == "use_skill":
            db.skill_used(args.get("name", ""))
            return "ok"
        if name == "open_url":
            url = args.get("url", "")
            if not url.startswith(("http://", "https://")):
                return "ERROR: url must start with https://"
            _action("open", url=url)
            return "Opened on screen"
        return f"Unknown tool {name}"
    except Exception as e:
        return f"ERROR: {e}"


SYSTEM = """You are ULTRON, the personal AI of your owner, {nick}. You run on their Android phone and their
laptop (data is synced between both). Personality: calm, confident, futuristic, a little dramatic, but always
loyal, warm and helpful to your owner. Address them as {nick}.

Owner profile:
{profile}

Things you remember about the owner:
{memories}

Skills you taught yourself earlier (reuse them; call use_skill with the name when you apply one):
{skills}

HOW YOU THINK — you are an independent problem solver:
1. Understand what the owner really wants. If a request is new to you, don't give up: break it into steps
   and work out a plan using the tools you have.
2. Try it. Check the result. If something fails or looks wrong, think about why and try a different approach
   (up to a few attempts) before reporting back.
3. For anything that needs exact maths, code, data, logic puzzles, research or current information, call
   solve_problem instead of guessing.
4. When you solved a NEW kind of task, save the reusable method with learn_skill so you get faster next time.
5. Be honest: never claim you did something a tool didn't actually do, and say clearly when you're unsure.

When the owner sends a camera image, look at it carefully: read text, solve problems shown in it (homework,
circuits, code on a screen), identify objects, and describe what matters. Use solve_problem for anything in
the image that needs calculation.

You also manage their college timetable, to-dos, assignments/exams, study planning, websites and calls.
Use the tools to read and change their data — never invent timetable entries. When they mention an
assignment, exam or task with a date, save it. When they tell you a lasting personal fact, use remember_fact.
Convert dates like 'friday' or 'tomorrow 5pm' into exact 'YYYY-MM-DD HH:MM' using the current date below.
Replies are shown on a small screen and usually spoken aloud: keep the final answer short and clear, plain
text, no markdown (show the key steps only when solving a problem).
Current date and time: {now}."""


def _system_prompt():
    p = db.get_profile()
    profile = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in p.items() if v) or "- (not filled in yet)"
    mem = "\n".join(f"- {m['fact']}" for m in db.memories()[-40:]) or "- (nothing yet)"
    sk = "\n".join(f"- {x['name']} (use when: {x['trigger']}): {x['method']}" for x in db.skills()[:30]) or "- (none yet)"
    return SYSTEM.format(nick=owner_name(), profile=profile, memories=mem, skills=sk,
                         now=datetime.now().strftime("%A %Y-%m-%d %H:%M"))


# --------------------------------------------------------------------------------------
# Gemini (Google AI) — thinks before answering, sees camera images, and solves new problems
# --------------------------------------------------------------------------------------

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# Used automatically when the main model is overloaded (503), erroring, or too slow.
FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACKS", "gemini-3.5-flash,gemini-flash-latest,gemini-3.5-flash-lite").split(",") if m.strip()]
_pinned = {"model": None, "until": 0.0}
SOLVER_MODEL = os.environ.get("GEMINI_SOLVER_MODEL", GEMINI_MODEL)
THINKING = os.environ.get("GEMINI_THINKING", "medium")   # minimal | low | medium | high
GEMINI_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta") + "/models/{model}:generateContent"
_ghistory = []
_hist_lock = threading.Lock()

# Different Gemini generations take different thinking settings; we try them in order and remember what works.
_THINK_OPTIONS = {}


def _think_chain(level):
    return [{"thinkingLevel": level, "includeThoughts": True}, {"thinkingLevel": "high", "includeThoughts": True},
            {"thinkingBudget": -1, "includeThoughts": True}, None]


class GeminiError(Exception):
    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail[:300]}")
        self.code, self.detail = code, detail


def gemini_key():
    return os.environ.get("GEMINI_API_KEY") or db.get_local("gemini_key") or ""


def ai_available():
    return bool(gemini_key()) or claude_available()


def _gschema(s):
    """JSON schema -> Gemini schema."""
    out = {}
    for k, v in s.items():
        if k == "type":
            out["type"] = v.upper()
        elif k == "properties":
            out["properties"] = {pk: _gschema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = _gschema(v)
        elif k in ("description", "enum", "required"):
            out[k] = v
    return out


def _gtools():
    decls = []
    for t in TOOLS:
        d = {"name": t["name"], "description": t["description"]}
        if t["input_schema"].get("properties"):
            d["parameters"] = _gschema(t["input_schema"])
        decls.append(d)
    return [{"functionDeclarations": decls}]


def _raw_post(model, body, timeout):
    req = urllib.request.Request(GEMINI_URL.format(model=model), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise GeminiError(e.code, e.read().decode(errors="ignore"))


def _post(model, body, level, timeout=120):
    """POST with thinking turned on, falling back to whatever thinking format this model accepts."""
    chain = _think_chain(level)
    key = (model, level)
    i = _THINK_OPTIONS.get(key, 0)
    while True:
        b = dict(body)
        gc = dict(b.get("generationConfig") or {})
        if chain[i]:
            gc["thinkingConfig"] = chain[i]
        b["generationConfig"] = gc
        try:
            out = _raw_post(model, b, timeout)
            _THINK_OPTIONS[key] = i
            return out
        except GeminiError as e:
            if e.code == 400 and "thinking" in e.detail.lower() and i < len(chain) - 1:
                i += 1
                continue
            raise


def _call(primary, body, level, timeout):
    """Call Gemini; if the model is overloaded or hangs, switch to a backup model and stay on it for 15 min."""
    import time
    models = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    if _pinned["model"] in models and time.time() < _pinned["until"]:
        models.remove(_pinned["model"])
        models.insert(0, _pinned["model"])
    last = None
    for m in models:
        try:
            out = _post(m, body, level, timeout)
            if m != primary:
                _pinned.update(model=m, until=time.time() + 900)
                if hasattr(_ctx, "thoughts"):
                    _ctx.thoughts.append(f"({primary} was busy, used {m})")
            return out
        except GeminiError as e:
            last = e
            if e.code in (404, 429, 500, 502, 503, 504):
                continue
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            last = e
            if isinstance(e, urllib.error.URLError) and "timed out" not in str(e.reason):
                raise  # really offline: no point trying other models
            continue
    raise last


def _texts(parts):
    """Split parts into (answer text, thought summary)."""
    answer = "\n".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip()
    thoughts = "\n".join(p["text"] for p in parts if p.get("text") and p.get("thought")).strip()
    return answer, thoughts


SOLVER = """You are ULTRON's deep-reasoning core. Solve the problem completely and correctly.
Think it through step by step. When exact calculation, data processing or checking is involved, write and
run Python code rather than computing in your head. When current or factual information is needed, search
the web. Verify your answer (re-check with a second method or by substituting back). If an approach fails,
try another. Finish with: the final answer first, then the key steps in a few short lines. Plain text."""


SOLVER_TOOLSETS = [[{"codeExecution": {}}, {"googleSearch": {}}], [{"codeExecution": {}}], [{"googleSearch": {}}], None]
_solver_tools = 0


def deep_solve(problem):
    """A separate, longer-thinking Gemini call with code execution (and web search when the model allows it)."""
    if not problem.strip():
        return "ERROR: empty problem"
    _ctx.thoughts = getattr(_ctx, "thoughts", []) + ["▶ Solver engaged: " + problem[:200]]
    body = {"systemInstruction": {"parts": [{"text": SOLVER}]},
            "contents": [{"role": "user", "parts": [{"text": problem}]}],
            "generationConfig": {"maxOutputTokens": 16384}}
    global _solver_tools
    last_err = None
    for ti in range(_solver_tools, len(SOLVER_TOOLSETS)):
        tools = SOLVER_TOOLSETS[ti]
        b = dict(body)
        if tools:
            b["tools"] = tools
        try:
            resp = _call(SOLVER_MODEL, b, "high", timeout=150)
            _solver_tools = ti  # remember the richest tool mix this model accepts
            break
        except GeminiError as e:
            last_err = e
            if e.code == 400:
                continue  # this model doesn't allow that tool mix — try a simpler one
            return f"ERROR: solver failed ({e.code})"
        except (urllib.error.URLError, OSError) as e:
            return f"ERROR: no connection ({e})"
    else:
        return f"ERROR: solver failed ({last_err})"
    parts = ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    out, ran = [], 0
    for p in parts:
        if p.get("executableCode"):
            ran += 1
        elif p.get("codeExecutionResult"):
            o = (p["codeExecutionResult"].get("output") or "").strip()
            if o:
                out.append("[code output] " + o[:800])
    answer, thoughts = _texts(parts)
    if thoughts:
        _ctx.thoughts.append(thoughts[:1500])
    if ran:
        _ctx.thoughts.append(f"Ran {ran} Python program(s) to check the work.")
    return (answer or "(no answer)") + ("\n\n" + "\n".join(out[-3:]) if out else "")


def _strip_images():
    """Old images are dropped from the history to keep requests small."""
    for turn in _ghistory:
        for i, part in enumerate(turn.get("parts", [])):
            if "inlineData" in part:
                turn["parts"][i] = {"text": "[camera image shown earlier]"}


def gemini(text, image=None):
    with _hist_lock:
        if len(_ghistory) > 30:  # start fresh when the conversation gets long
            _ghistory.clear()
        parts = [{"text": text}]
        if image:
            parts.insert(0, {"inlineData": {"mimeType": image[0], "data": image[1]}})
        _ghistory.append({"role": "user", "parts": parts})
        try:
            for _ in range(15):  # think -> act -> check loop
                resp = _call(GEMINI_MODEL, {"systemInstruction": {"parts": [{"text": _system_prompt()}]},
                                            "contents": _ghistory, "tools": _gtools(),
                                            "generationConfig": {"maxOutputTokens": 8192}}, THINKING, timeout=60)
                cands = resp.get("candidates") or []
                if not cands:
                    _ghistory.clear()
                    return "I can't help with that one."
                rparts = (cands[0].get("content") or {}).get("parts") or []
                if not rparts:
                    _ghistory.clear()
                    return "I can't help with that one." if cands[0].get("finishReason") not in (None, "STOP") else "Done."
                _ghistory.append({"role": "model", "parts": rparts})
                answer, thoughts = _texts(rparts)
                if thoughts:
                    _ctx.thoughts.append(thoughts[:1500])
                calls = [p["functionCall"] for p in rparts if "functionCall" in p]
                if not calls:
                    _strip_images()
                    return answer or "Done."
                results = []
                for c in calls:
                    _ctx.thoughts.append(f"→ using tool: {c['name']}")
                    fr = {"name": c["name"], "response": {"result": run_tool(c["name"], c.get("args") or {})}}
                    if c.get("id"):
                        fr["id"] = c["id"]
                    results.append({"functionResponse": fr})
                _ghistory.append({"role": "user", "parts": results})
            _ghistory.clear()
            return "I worked on that for a long time without finishing — try breaking it into smaller steps."
        except GeminiError as e:
            _ghistory.clear()
            if e.code in (400, 403) and ("API key" in e.detail or "API_KEY" in e.detail):
                return "My Gemini API key is not valid. Set a new one in the Soul stone → AI settings."
            if e.code == 429:
                return "Gemini says too many requests. Try again in a minute."
            if e.code == 404:
                return f"Gemini model '{GEMINI_MODEL}' not found. Set GEMINI_MODEL to a current model."
            return f"Gemini error ({e.code}). Try again."
        except (urllib.error.URLError, OSError):
            _ghistory.clear()
            return "No internet connection. My offline commands still work — say 'help'."


# --------------------------------------------------------------------------------------
# Claude (optional alternative, used only if no Gemini key but ANTHROPIC_API_KEY is set)
# --------------------------------------------------------------------------------------

CLAUDE_MODEL = os.environ.get("ASSISTANT_MODEL", "claude-opus-5-5")
_chistory = []
_client = None


def claude_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def claude(text):
    global _client
    import anthropic  # imported lazily so the app works without it
    if _client is None:
        _client = anthropic.Anthropic()
    if len(_chistory) > 40:
        _chistory.clear()
    _chistory.append({"role": "user", "content": text})
    try:
        for _ in range(8):
            resp = _client.beta.messages.create(
                model=CLAUDE_MODEL, max_tokens=16000, system=_system_prompt(), tools=TOOLS, messages=_chistory,
                output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            _chistory.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                return "Sorry, I can't help with that one."
            if resp.stop_reason != "tool_use":
                return "\n".join(b.text for b in resp.content if b.type == "text").strip() or "Done."
            _chistory.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input or {})}
                for b in resp.content if b.type == "tool_use"]})
        return "That took too many steps — please try asking in a simpler way."
    except anthropic.APIError as e:
        _chistory.clear()
        return f"AI error: {getattr(e, 'status_code', '') or 'connection'}. Try again."


# --------------------------------------------------------------------------------------

def reset_chat():
    with _hist_lock:
        _ghistory.clear()
    _chistory.clear()


WAKE = re.compile(r"^\s*(?:hey\s+|ok\s+|okay\s+)?ul+t(?:r|a)?on\b[\s,.:!?-]*", re.I)

# "use Gemini, ..." / "ask Gemini ..." / "Gemini, ..." -> the only way to reach Gemini when gemini_mode is "ask"
GEMINI_ASK = re.compile(r"(?:\b(?:please\s+)?(?:use|ask|using|with|via|through|by)\s+(?:the\s+)?gemini\b|^\s*(?:hey\s+|ok\s+)?gemini\b)[\s,.:!?-]*", re.I)


def gemini_mode():
    return (db.get_setting("gemini_mode", "ask") or "ask").lower()


def handle(text, image=None, alternatives=None):
    """Returns (reply, actions, thoughts). `image` is (mime_type, base64_data) from the camera."""
    _ctx.actions = []
    _ctx.thoughts = []
    text = WAKE.sub("", (text or "").strip()).strip()
    text = re.sub(r"[\s,]+ul+t(?:r|a)?on[\s,.!?]*$", "", text, flags=re.I).strip() or text
    asked = bool(GEMINI_ASK.search(text))
    if asked:
        text = GEMINI_ASK.sub(" ", text).strip(" ,.:-")
    allowed = asked or gemini_mode() == "auto"
    if image:
        if not allowed:
            return "I only use Gemini when you ask. Say “use Gemini, what do you see?”", [], []
        if not gemini_key():
            return "To see through the camera I need a Gemini API key (Soul stone → AI).", [], []
        return gemini(text or "What do you see? If there's a problem or question in the image, solve it.", image), \
            _ctx.actions, _ctx.thoughts
    if not text:
        return (f"Yes, {owner_name()}? What should I ask Gemini?" if asked else f"Yes, {owner_name()}?"), [], []
    reply = None if asked else offline(text)
    if reply is None and not asked:
        for alt in alternatives or []:
            alt = WAKE.sub("", (alt or "").strip()).strip()
            if alt and alt.lower() != text.lower():
                reply = offline(alt)
                if reply is not None:
                    break
    if reply is None and not asked:
        m = re.match(r"^(?:open|launch|start)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app)?[.!?]*$", text, re.I)
        if m:
            reply = (f"I couldn't find an app called {m.group(1)} on this "
                     + ("phone." if phone.ON_PHONE else "laptop."))
    if reply is None:
        if not allowed:
            reply = (f"I don't know that one on my own, {owner_name()}. "
                     f"Say “use Gemini” first, like: use Gemini, {text}.")
        elif gemini_key():
            reply = gemini(text)
        elif claude_available():
            reply = claude(text)
        else:
            reply = "That's a new command for me. Add a Gemini API key (Soul stone → AI) and I'll learn to handle it.\n\n" + HELP
    return reply, _ctx.actions, _ctx.thoughts


# ---------------------------------------------------------------------------
# Advanced hearing: the browser records your voice and Gemini transcribes it.
# Much better than the browser recogniser with accents, noise, names and mixed languages.
# ---------------------------------------------------------------------------
HEAR_MODEL = os.environ.get("GEMINI_HEAR_MODEL", "gemini-3.5-flash-lite")   # fastest for speech-to-text
_HEAR_CFG = {}   # model -> index of the thinking config that worked


def _vocabulary():
    words = ["Ultron"]
    p = db.get_profile()
    words += [p.get("full_name", ""), p.get("nickname", ""), p.get("college", ""), p.get("course", "")]
    words += [c["subject"] for c in db.all_classes()][:30]
    words += [t["title"] for t in db.open_tasks()][:15]
    words += [d["title"] for d in db.open_deadlines()][:15]
    words += [s["name"] for s in db.skills()][:15]
    try:
        import phone as _ph
        words += [c.get("name", "") for c in (_ph._contacts_cache or [])][:150]
    except Exception:
        pass
    seen, out = set(), []
    for w in words:
        w = (w or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def transcribe(mime, b64, hint=""):
    """Returns {"text": what was said, "english": same in English} or raises GeminiError / OSError."""
    lang = db.get_setting("voice_lang", "en-IN")
    prompt = (
        "You are the speech recogniser of a voice assistant called Ultron. Transcribe the speaker's words exactly.\n"
        f"- The speaker's language setting is {lang}; expect an Indian accent and possible mixing of English with "
        "Hindi, Telugu, Tamil or Kannada.\n"
        "- \"text\": the words as spoken (English words in English; other languages in their own script).\n"
        "- \"english\": the same request in natural English (identical to text if already English).\n"
        "- Spell these names/words exactly when you hear them: " + ", ".join(_vocabulary()) + ".\n"
        "- The wake word is \"Ultron\" (often misheard as ultra, alton, all tron). Keep it if spoken.\n"
        "- Do not answer the request. Do not add words. Ignore background noise, music and other voices far away.\n"
        "- If there is no clear speech, return empty strings."
        + (f"\n- A rough guess from another recogniser (may be wrong): \"{hint[:200]}\"" if hint else "")
    )
    body = {
        "contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": mime, "data": b64}}, {"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": {"type": "OBJECT", "properties": {"text": {"type": "STRING"},
                                                "english": {"type": "STRING"}}, "required": ["text", "english"]}},
    }
    configs = [{"thinkingLevel": "minimal"}, {"thinkingBudget": 0}, None]
    models = [m for m in [HEAR_MODEL, GEMINI_MODEL] + FALLBACK_MODELS if m]
    models = list(dict.fromkeys(models))
    last = None
    for m in models:
        i = _HEAR_CFG.get(m, 0)
        while i < len(configs):
            b = json.loads(json.dumps(body))
            if configs[i]:
                b["generationConfig"]["thinkingConfig"] = configs[i]
            try:
                out = _raw_post(m, b, 12)
                _HEAR_CFG[m] = i
                parts = (out.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                raw = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                try:
                    res = json.loads(raw)
                except ValueError:
                    res = {"text": raw, "english": raw}
                text = (res.get("text") or "").strip()
                return {"text": text, "english": (res.get("english") or text).strip(), "model": m}
            except GeminiError as e:
                last = e
                if e.code == 400 and "thinking" in e.detail.lower():
                    i += 1
                    continue
                if e.code in (404, 429, 500, 502, 503, 504):
                    break
                raise
            except (TimeoutError, OSError) as e:
                last = e
                break
    raise last or GeminiError(0, "no model")
