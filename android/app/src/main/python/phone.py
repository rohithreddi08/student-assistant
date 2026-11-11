"""Android phone features through Termux:API (notifications, voice, contacts, calls).

On a PC (no Termux) everything falls back to printing, so you can test the app anywhere.
"""
import difflib
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time

# Three ways to run: the ULTRON Android app (Java bridge), Termux (Termux:API commands), or a PC.
ANDROID_APP = os.environ.get("ULTRON_ANDROID_APP") == "1"
_Bridge = None
if ANDROID_APP:
    try:
        from java import jclass  # provided by Chaquopy inside the APK
        _Bridge = jclass("com.ultron.app.Bridge")
    except Exception as _e:  # pragma: no cover
        print("[phone] Android bridge unavailable:", _e)
        ANDROID_APP = False
ON_PHONE = ANDROID_APP or shutil.which("termux-notification") is not None

# Recent notifications/speech, so the laptop's browser can show pop-ups and speak reminders.
_feed, _feed_lock, _feed_ids = [], threading.Lock(), itertools.count(int(time.time()))


def _push(kind, title, text):
    with _feed_lock:
        _feed.append({"id": next(_feed_ids), "kind": kind, "title": title, "text": text})
        del _feed[:-50]


def feed_after(after):
    with _feed_lock:
        return [n for n in _feed if n["id"] > after]


def _run(args, timeout=30):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        print("[phone] command failed:", args[0], e)
        return ""


def notify(title, text, nid=None):
    print(f"[NOTIFY] {title}: {text}")
    if not ON_PHONE:
        _push("notify", title, text)
        return
    if ANDROID_APP:
        _Bridge.notify(str(title), str(text), int(nid) if nid is not None else -1)
        return
    args = ["termux-notification", "--title", title, "--content", text,
            "--priority", "high", "--sound", "--vibrate", "500,300,500"]
    if nid is not None:
        args += ["--id", str(nid)]
    _run(args)


def speak(text):
    print(f"[SPEAK] {text}")
    if not ON_PHONE:
        _push("speak", "", text)
    if ANDROID_APP:
        _Bridge.speak(str(text))
        return
    if ON_PHONE:
        # Run in background so the server doesn't wait for speech to finish
        try:
            subprocess.Popen(["termux-tts-speak", text])
        except OSError:
            pass


def set_alarm(hour, minute, label="ULTRON"):
    """Sets an Android alarm (phone only). Returns True if the request was sent."""
    if not ON_PHONE:
        return False
    if ANDROID_APP:
        return bool(_Bridge.setAlarm(int(hour), int(minute), str(label)))
    _run(["am", "start", "-a", "android.intent.action.SET_ALARM", "--ei", "android.intent.extra.alarm.HOUR", str(hour),
          "--ei", "android.intent.extra.alarm.MINUTES", str(minute), "--es", "android.intent.extra.alarm.MESSAGE", label,
          "--ez", "android.intent.extra.alarm.SKIP_UI", "true"])
    return True


# app name -> how to open it (Windows: a command or URI; Android: an intent action)
WIN_APPS = {
    "calculator": "calc", "calc": "calc", "notepad": "notepad", "paint": "mspaint", "settings": "ms-settings:",
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer", "my files": "explorer",
    "this pc": "explorer", "downloads": "shell:Downloads", "documents": "shell:Personal",
    "camera": "microsoft.windows.camera:", "task manager": "taskmgr", "control panel": "control",
    "command prompt": "cmd", "cmd": "cmd", "terminal": "wt", "powershell": "powershell",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code",
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge", "firefox": "firefox",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "spotify": "spotify:", "calendar": "outlookcal:", "mail": "outlookmail:", "store": "ms-windows-store:",
    "clock": "ms-clock:", "alarms": "ms-clock:", "snipping tool": "snippingtool", "photos": "ms-photos:",
    "whatsapp": "whatsapp:", "telegram": "tg:", "teams": "msteams:", "zoom": "zoommtg:", "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi", "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound", "volume": "ms-settings:sound",
}
ANDROID_APPS = {
    "settings": "android.settings.SETTINGS", "wifi": "android.settings.WIFI_SETTINGS",
    "wi-fi": "android.settings.WIFI_SETTINGS", "bluetooth": "android.settings.BLUETOOTH_SETTINGS",
    "camera": "android.media.action.STILL_IMAGE_CAMERA", "dialer": "android.intent.action.DIAL",
    "phone": "android.intent.action.DIAL", "clock": "android.intent.action.SHOW_ALARMS",
    "alarms": "android.intent.action.SHOW_ALARMS", "alarm": "android.intent.action.SHOW_ALARMS",
    "display settings": "android.settings.DISPLAY_SETTINGS", "sound settings": "android.settings.SOUND_SETTINGS",
}


def _windows_app_path(name):
    """Look up an installed program in Windows' 'App Paths' registry (e.g. 'spotify' -> Spotify.exe)."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}.exe") as k:
                return winreg.QueryValue(k, None)
        except OSError:
            continue
    return None


def open_app(name):
    """Opens a desktop/phone app by its everyday name. Returns True if it was launched."""
    n = re.sub(r"^(the|my)\s+|\s+(app|application)$", "", name.strip().lower()).strip()
    if ANDROID_APP:   # system screens first, then any installed app whose name matches
        action = ANDROID_APPS.get(n)
        if action and _Bridge.openAction(action):
            return True
        return bool(_Bridge.launchApp(n))
    if ON_PHONE:
        action = ANDROID_APPS.get(n)
        if not action:
            return False
        _run(["am", "start", "-a", action])
        return True
    if os.name != "nt":
        return False
    target = WIN_APPS.get(n) or _windows_app_path(n.replace(" ", ""))
    if not target:
        return False
    try:
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


def listen():
    """Opens Android's speech recogniser and returns what you said."""
    if not ON_PHONE or ANDROID_APP:
        return ""
    return _run(["termux-speech-to-text"], timeout=60)


_contacts_cache = None


def contacts(refresh=False):
    global _contacts_cache
    if _contacts_cache is not None and not refresh:
        return _contacts_cache
    if not ON_PHONE:
        _contacts_cache = []
        return _contacts_cache
    raw = str(_Bridge.contactsJson()) if ANDROID_APP else _run(["termux-contact-list"], timeout=60)
    try:
        _contacts_cache = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        _contacts_cache = []
    return _contacts_cache


def find_contact(name):
    """Fuzzy match a spoken/typed name against your contacts. Returns list of matches."""
    name = name.strip().lower()
    people = contacts()
    if not name or not people:
        return []
    exact = [c for c in people if c.get("name", "").lower() == name]
    if exact:
        return exact
    starts = [c for c in people if c.get("name", "").lower().startswith(name)]
    contains = [c for c in people if name in c.get("name", "").lower() and c not in starts]
    if starts or contains:
        return (starts + contains)[:5]
    names = [c.get("name", "") for c in people]
    close = difflib.get_close_matches(name, [n.lower() for n in names], n=3, cutoff=0.6)
    return [c for c in people if c.get("name", "").lower() in close]


def call_number(number):
    print(f"[CALL] {number}")
    if not ON_PHONE:
        return False
    if ANDROID_APP:
        return bool(_Bridge.call(str(number)))
    _run(["termux-telephony-call", number])
    return True


def call_contact(name):
    """Returns (ok, message)."""
    if not ON_PHONE:
        # Laptop: ask the synced phone to make the call
        import sync
        ok, msg = sync.remote("call", {"name": name})
        return ok, msg
    matches = find_contact(name)
    if not matches:
        return False, f"I couldn't find '{name}' in your contacts."
    if len(matches) > 1 and matches[0].get("name", "").lower() != name.strip().lower():
        options = ", ".join(m["name"] for m in matches)
        return False, f"I found several people: {options}. Say the full name."
    person = matches[0]
    call_number(person["number"])
    return True, f"Calling {person['name']} ({person['number']})..."


def open_url(url):
    """Open a website on this device (phone: Android browser via Termux, PC: default browser)."""
    print(f"[OPEN] {url}")
    if ANDROID_APP:
        _Bridge.openUrl(str(url))
        return
    if ON_PHONE:
        _run(["termux-open-url", url], timeout=10)
        return
    try:
        import webbrowser
        webbrowser.open(url, new=2)
    except Exception as e:
        print("[phone] could not open browser:", e)
