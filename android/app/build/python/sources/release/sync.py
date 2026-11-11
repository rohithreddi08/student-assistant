"""Two-way sync between two ULTRON devices (e.g. phone + laptop).

On ONE device set the other device's address (Soul stone → Sync), and the same sync key on both.
Every 30 s this device sends all its data to the peer; the peer merges it and answers with all of
its own data, which we merge here. Newest change wins, deletes are kept as tombstones.
"""
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request

import db
import phone

status = {"last_ok": None, "last_error": None, "changed": 0}
_lock = threading.Lock()


def peer():
    return (db.get_local("sync_peer") or "").strip().rstrip("/")


def key():
    return (db.get_local("sync_key") or "").strip()


def key_ok(given):
    k = key()
    return bool(k) and hmac.compare_digest(k.encode(), (given or "").encode())


def sync_now():
    """Returns (ok, message)."""
    url, k = peer(), key()
    if not url:
        return False, "No peer device set."
    if not k:
        return False, "Set a sync key (the same on both devices)."
    if not url.startswith("http"):
        url = "http://" + url
    with _lock:
        try:
            body = json.dumps(db.export_all(), default=str).encode()
            req = urllib.request.Request(url + "/api/sync", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Sync-Key": k,
                                                  "X-Sync-Port": os.environ.get("ASSISTANT_PORT", "8080"),
                                                  "X-Sync-Node": "phone" if phone.ON_PHONE else "laptop"})
            with urllib.request.urlopen(req, timeout=20) as r:
                theirs = json.loads(r.read())
            n = db.merge(theirs)
            status.update(last_ok=time.strftime("%Y-%m-%d %H:%M:%S"), last_error=None, changed=n)
            return True, f"Synced with {url} ({n} changes received)."
        except urllib.error.HTTPError as e:
            msg = "wrong sync key" if e.code == 403 else f"peer answered {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            msg = f"can't reach {url} ({getattr(e, 'reason', e)})"
        status["last_error"] = msg
        return False, "Sync failed: " + msg


def note_peer(ip, port, node):
    """Remember the address of a device that synced with us (so we can send it commands back)."""
    if ip and port and port.isdigit():
        db.set_local("peer_seen", f"http://{ip}:{port}")
        db.set_local("peer_seen_node", node or "")


def remote(action, data):
    """Ask the other device to do something (e.g. the laptop asks the phone to make a call). Returns (ok, message)."""
    url, k = peer() or (db.get_local("peer_seen") or ""), key()
    if not url or not k:
        return False, ("I can only call from the phone. Set up sync between this laptop and your phone "
                       "(Soul stone → Sync) and I'll make calls through the phone.")
    if not url.startswith("http"):
        url = "http://" + url
    try:
        req = urllib.request.Request(url + "/api/remote", data=json.dumps({"action": action, **data}).encode(),
                                     method="POST", headers={"Content-Type": "application/json", "X-Sync-Key": k})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        return bool(res.get("ok")), res.get("message", "")
    except urllib.error.HTTPError as e:
        return False, "The other device refused (check the sync key)." if e.code == 403 else f"Other device error {e.code}."
    except (urllib.error.URLError, OSError, ValueError):
        return False, f"I can't reach your other device at {url}. Is ULTRON running there?"


def _loop():
    while True:
        if peer() and key():
            try:
                sync_now()
            except Exception as e:  # never let the thread die
                status["last_error"] = str(e)
        time.sleep(30)


def start():
    threading.Thread(target=_loop, daemon=True).start()
