"""Builds a study timetable that avoids your classes and focuses on whatever is due soonest."""
from datetime import datetime, timedelta

import db
from timeparse import to_minutes, from_minutes

KIND_WEIGHT = {"exam": 3.0, "project": 2.0, "assignment": 1.5, "submission": 1.5, "other": 1.0}


def subject_weights(start_day):
    """Each subject you attend gets a base weight; upcoming deadlines/exams add urgency."""
    weights = {}
    for c in db.all_classes():
        weights.setdefault(c["subject"], 1.0)
    for d in db.open_deadlines():
        subj = d["subject"] or d["title"]
        try:
            due = datetime.strptime(d["due"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        days_left = (due.date() - start_day.date()).days
        if days_left < 0:
            continue
        urgency = KIND_WEIGHT.get(d["kind"], 1.0) * 6.0 / (days_left + 1)
        weights[subj] = weights.get(subj, 1.0) + urgency
    return weights


def focus_for(subject, day):
    """Tells you what to do in that session."""
    for d in db.open_deadlines():
        if (d["subject"] or d["title"]) == subject:
            due = datetime.strptime(d["due"], "%Y-%m-%d %H:%M")
            if due.date() >= day.date():
                left = (due.date() - day.date()).days
                if d["kind"] == "exam":
                    return f"Revise for {d['title']} ({left} days left)" if left > 1 else f"Final revision: {d['title']}"
                return f"Work on {d['title']} (due in {left} days)"
    return "Revise today's class notes + practice problems"


def make_plan(days=7, start=None, end=None, session_min=None, break_min=None, replace=True):
    s = db.get_settings()
    start = start or s["study_start"]
    end = end or s["study_end"]
    session_min = int(session_min or s["session_min"])
    break_min = int(break_min or s["break_min"])

    today = datetime.now()
    first = today if today.strftime("%H:%M") < end else today + timedelta(days=1)
    last = first + timedelta(days=days - 1)

    if replace:  # remove old not-done planned sessions in this range
        db.execute("UPDATE study SET deleted = 1, updated = ? WHERE done = 0 AND deleted = 0 AND date BETWEEN ? AND ?",
                   (db.now_utc(), first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")))

    weights = subject_weights(first)
    if not weights:
        return [], "Add your timetable or some deadlines first, so I know which subjects to plan."

    credit = {k: 0.0 for k in weights}  # weighted round-robin
    created = []
    for i in range(days):
        day = first + timedelta(days=i)
        ds = day.strftime("%Y-%m-%d")
        busy = [(to_minutes(c["start"]), to_minutes(c["end"])) for c in db.classes_on(ds, day.weekday())]
        t = to_minutes(start)
        if i == 0 and day.date() == today.date():
            now_m = today.hour * 60 + today.minute + 5
            t = max(t, now_m - now_m % 5)
        stop = to_minutes(end)
        last_subject = None
        while t + session_min <= stop:
            clash = next(((a, b) for a, b in busy if t < b and t + session_min > a), None)
            if clash:
                t = clash[1] + 5
                continue
            for k in credit:
                credit[k] += weights[k]
            ranked = sorted(credit, key=lambda k: -credit[k])
            subject = ranked[0] if ranked[0] != last_subject or len(ranked) == 1 else ranked[1]
            credit[subject] -= sum(weights.values())
            st, en = from_minutes(t), from_minutes(t + session_min)
            focus = focus_for(subject, day)
            sid = db.insert("study", date=ds, start=st, end=en, subject=subject, focus=focus)
            created.append({"id": sid, "date": ds, "start": st, "end": en, "subject": subject, "focus": focus})
            last_subject = subject
            t += session_min + break_min
    msg = f"Planned {len(created)} study sessions over {days} days ({start}-{end}, {session_min} min each)."
    return created, msg
