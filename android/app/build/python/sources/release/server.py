"""ULTRON web server: open http://localhost:8080 in the browser (phone or laptop)."""
import hashlib
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain
import db
import phone
import planner
import reminders
import sync

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.environ.get("ULTRON_STATIC") or os.path.join(HERE, "static")   # the Android app unpacks it elsewhere
# Listens on the network so the other device can sync; everything except /api/sync is local-only.
HOST = os.environ.get("ASSISTANT_HOST", "0.0.0.0")
PORT = int(os.environ.get("ASSISTANT_PORT", "8080"))


def today_payload():
    now = datetime.now()
    ds = now.strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=6)).strftime("%Y-%m-%d")
    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "classes": db.classes_on(ds, now.weekday()),
        "study": db.study_between(ds, ds),
        "tasks": db.open_tasks(),
        "deadlines": db.open_deadlines(),
        "next_class": brain.next_class(),
        "week_study": db.study_between(ds, week_end),
        "ai": brain.ai_available(),
        "owner_enrolled": owner_enrolled(),
        "on_phone": phone.ON_PHONE,
        "settings": db.get_settings(),
    }


MANIFEST = {
    "name": "ULTRON", "short_name": "ULTRON", "description": "Personal AI for phone and laptop",
    "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any",
    "background_color": "#05060b", "theme_color": "#05060b",
    "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}
# Minimal service worker: makes ULTRON installable as an app; always uses the live server (no stale cache).
SW_JS = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => { if (e.request.mode === 'navigate') e.respondWith(fetch(e.request)); });
"""


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _hash_pin(pin, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.sha256((salt + str(pin)).encode()).hexdigest()


def pin_ok(pin):
    stored = db.get_setting("owner.pin") or ""
    if "$" not in stored:
        return False
    return secrets.compare_digest(_hash_pin(pin, stored.split("$")[0]), stored)


def owner_enrolled():
    return bool(db.get_setting("owner.face") or db.get_setting("owner.pin"))


def owner_payload():
    def j(k, default):
        try:
            return json.loads(db.get_setting(k) or "null") or default
        except ValueError:
            return default
    return {"enrolled": owner_enrolled(), "face": j("owner.face", []), "voice": j("owner.voice", None),
            "photos": j("owner.photos", []), "has_pin": bool(db.get_setting("owner.pin")),
            "name": brain.owner_name(), "profile": db.get_profile()}


def config_payload():
    return {"gemini_key_set": bool(brain.gemini_key()), "gemini_model": brain.GEMINI_MODEL,
            "sync_peer": sync.peer(), "sync_key_set": bool(sync.key()), "sync_status": sync.status,
            "this_address": f"http://{lan_ip()}:{PORT}", "on_phone": phone.ON_PHONE}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the Termux screen clean

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _static(self, rel):
        path = os.path.realpath(os.path.join(STATIC, rel))
        if not path.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "text/javascript"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if "vendor" in rel else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if not self._local():
            return self._send(403, {"error": "ULTRON only answers its owner's device"})
        if p in ("/", "/index.html"):
            return self._static("index.html")
        if p.startswith("/static/"):
            return self._static(p[len("/static/"):])
        if p == "/manifest.webmanifest":
            return self._send(200, MANIFEST, "application/manifest+json")
        if p == "/sw.js":
            return self._send(200, SW_JS.encode(), "text/javascript; charset=utf-8")
        if p == "/api/notifications":
            m = re.search(r"after=(\d+)", self.path)
            return self._send(200, phone.feed_after(int(m.group(1)) if m else 0))
        if p == "/api/owner":
            return self._send(200, owner_payload())
        if p == "/api/profile":
            return self._send(200, db.get_profile())
        if p == "/api/config":
            return self._send(200, config_payload())
        if p == "/api/memories":
            return self._send(200, db.memories())
        if p == "/api/skills":
            return self._send(200, db.skills())
        if p == "/api/today":
            return self._send(200, today_payload())
        if p == "/api/classes":
            return self._send(200, db.all_classes())
        if p == "/api/tasks":
            return self._send(200, db.all_tasks())
        if p == "/api/deadlines":
            return self._send(200, db.all_deadlines())
        if p == "/api/study":
            ds = datetime.now().strftime("%Y-%m-%d")
            return self._send(200, db.study_between(ds, (datetime.now() + timedelta(days=13)).strftime("%Y-%m-%d")))
        if p == "/api/settings":
            return self._send(200, db.get_settings())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/sync":  # the only endpoint another device may call
            if not sync.key_ok(self.headers.get("X-Sync-Key")):
                return self._send(403, {"error": "bad sync key"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                theirs = json.loads(self.rfile.read(n)) if n else {}
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad json"})
            db.merge(theirs)
            sync.note_peer(self.client_address[0], self.headers.get("X-Sync-Port", ""), self.headers.get("X-Sync-Node", ""))
            return self._send(200, db.export_all())
        if p == "/api/remote":  # commands from the other device (same sync key)
            if not sync.key_ok(self.headers.get("X-Sync-Key")):
                return self._send(403, {"error": "bad sync key"})
            b = self._body()
            if b.get("action") == "call":
                if not phone.ON_PHONE:
                    return self._send(200, {"ok": False, "message": "That device can't make phone calls."})
                ok, msg = phone.call_contact(b.get("name", ""))
                return self._send(200, {"ok": ok, "message": msg + " (on your phone)"})
            return self._send(400, {"ok": False, "message": "unknown action"})
        if not self._local():
            return self._send(403, {"error": "ULTRON only answers its owner's device"})
        b = self._body()
        try:
            if p == "/api/classes":
                day = b.get("day")
                cid = db.add_class(b["subject"], b["start"], b["end"],
                                   day=int(day) if day not in (None, "") and not b.get("date") else None,
                                   date=b.get("date") or None, room=b.get("room", ""), teacher=b.get("teacher", ""))
                return self._send(200, {"id": cid})
            if p == "/api/tasks":
                due = b.get("due") or None
                if due:
                    due = due.replace("T", " ")[:16]
                return self._send(200, {"id": db.add_task(b["title"], due, int(b.get("priority", 2)))})
            if p == "/api/deadlines":
                return self._send(200, {"id": db.add_deadline(b["title"], b["due"].replace("T", " ")[:16],
                                                              b.get("subject", ""), b.get("kind", "assignment"),
                                                              b.get("notes", ""))})
            if p == "/api/study/plan":
                sessions, msg = planner.make_plan(days=int(b.get("days", 7)), start=b.get("start"), end=b.get("end"),
                                                  session_min=b.get("session_min"))
                return self._send(200, {"sessions": sessions, "message": msg})
            if p == "/api/settings":
                for k, v in b.items():
                    if k in db.DEFAULT_SETTINGS:
                        db.set_setting(k, v)
                return self._send(200, {"ok": True})
            if p == "/api/chat":
                image = None
                m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$", b.get("image") or "")
                if m:
                    image = (m.group(1), m.group(2))
                alts = [str(a)[:300] for a in (b.get("alternatives") or [])][:5]
                reply, actions, thoughts = brain.handle(b.get("message", ""), image, alts)
                return self._send(200, {"reply": reply, "actions": actions, "thoughts": thoughts})
            if p == "/api/owner/enroll":
                # First enrolment is open; changing it later needs the PIN
                if owner_enrolled() and not pin_ok(b.get("current_pin", "")):
                    return self._send(403, {"error": "Enter your current PIN to change owner data"})
                if b.get("pin"):
                    if not re.fullmatch(r"\d{4,8}", str(b["pin"])):
                        return self._send(400, {"error": "PIN must be 4-8 digits"})
                    db.set_setting("owner.pin", _hash_pin(b["pin"]))
                if b.get("face"):
                    db.set_setting("owner.face", json.dumps(b["face"]))
                if b.get("photos") is not None:
                    db.set_setting("owner.photos", json.dumps(b["photos"][:4]))
                if b.get("voice"):
                    db.set_setting("owner.voice", json.dumps(b["voice"]))
                return self._send(200, {"ok": True})
            if p == "/api/owner/pin":
                return self._send(200, {"ok": pin_ok(b.get("pin", ""))})
            if p == "/api/profile":
                for k, v in b.items():
                    if k in db.PROFILE_FIELDS:
                        db.set_setting("profile." + k, str(v).strip()[:500])
                if b.get("nickname") or b.get("full_name"):
                    db.set_setting("name", (b.get("nickname") or b.get("full_name")).strip())
                return self._send(200, {"ok": True})
            if p == "/api/config":
                for k in ("gemini_key", "sync_peer", "sync_key"):
                    if k in b:
                        db.set_local(k, str(b[k]).strip())
                return self._send(200, config_payload())
            if p == "/api/sync/now":
                ok, msg = sync.sync_now()
                return self._send(200, {"ok": ok, "message": msg})
            if p == "/api/chat/reset":
                brain.reset_chat()
                return self._send(200, {"ok": True})
            if p == "/api/hear":
                m = re.match(r"^data:(audio/[a-z0-9.+-]+)(?:;[^,]*)?;base64,([A-Za-z0-9+/=]+)$", b.get("audio") or "")
                if not m:
                    return self._send(400, {"error": "send audio as a base64 data URL"})
                if not brain.gemini_key():
                    return self._send(200, {"ok": False, "error": "no_key"})
                try:
                    return self._send(200, {"ok": True, **brain.transcribe(m.group(1), m.group(2), b.get("hint", ""))})
                except Exception as e:  # network / quota: the page falls back to the browser recogniser
                    return self._send(200, {"ok": False, "error": str(e)[:200]})
            if p == "/api/listen":
                return self._send(200, {"text": phone.listen()})
            if p == "/api/call":
                ok, msg = phone.call_contact(b.get("name", ""))
                return self._send(200, {"ok": ok, "message": msg})
            if p == "/api/test-notification":
                phone.notify("ULTRON", "Notifications are working ✅")
                return self._send(200, {"ok": True})
            m = re.match(r"^/api/(tasks|deadlines|study)/(\d+)/(done|undo)$", p)
            if m:
                db.mark_done(m.group(1), int(m.group(2)), 1 if m.group(3) == "done" else 0)
                return self._send(200, {"ok": True})
            m = re.match(r"^/api/(classes|tasks|deadlines|study|memories|skills)/(\d+)/delete$", p)
            if m:
                db.delete(m.group(1), int(m.group(2)))
                return self._send(200, {"ok": True})
        except (KeyError, ValueError) as e:
            return self._send(400, {"error": f"missing or bad field: {e}"})
        return self._send(404, {"error": "not found"})


def main():
    # Windows consoles/log files may not be UTF-8: never let an emoji in a log line crash a reminder
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    db.init()
    reminders.start()
    sync.start()
    print(f"ULTRON online - open http://localhost:{PORT} in your browser")
    print(f"Address for the other device to sync with: http://{lan_ip()}:{PORT}")
    print("Gemini brain:", "ON" if brain.gemini_key() else "OFF (add a key in the app: Soul stone -> AI)")
    print("Phone features:", "ON (Android app)" if phone.ANDROID_APP else "ON (Termux:API)" if phone.ON_PHONE
          else "OFF (PC test mode)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
