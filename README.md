# ULTRON 🔴 — personal AI for your phone + laptop

A voice-controlled personal assistant with a futuristic Infinity-Stones interface that:

- 🧑 **Knows its owner.** On first start it scans your **face** (camera or uploaded photos), records your **voice**, sets a backup **PIN**, then asks for your **personal details**. After that, it unlocks only for your face, your voice, or your PIN.
- 🤖 **Has a face.** An animated robot face in the centre of the home screen. It blinks, follows your mouse, scans while thinking, its eyes flare when listening, and its mouth moves with its voice. Talking to it opens full-screen conversation mode.
- 🎤 **Works by voice.** Tap the red orb and speak, or turn on the **wake word** and say *"Ultron, what's my next class?"*
- 🧠 **Thinks for itself.** Commands it doesn't know go to Google Gemini with *thinking* turned on. Gemini plans, uses tools, checks the result and tries another way if needed. Hard problems (maths, code, research) go to a deep-reasoning solver that **writes and runs Python** and **searches the web**. When it solves a new kind of task, it **saves the method as a skill** and reuses it next time.
- 👁️ **Sees through the camera.** Say *"what do you see"* or *"solve this"* (it takes a photo), or open the 📷 camera in chat and press *Ask about this*. Try a homework problem, a circuit or code on a screen. *"Do you recognise me"* checks your face against the owner's.
- 🛡️ **Owner guard (optional).** Checks the camera every minute and locks ULTRON if someone else is using it.
- 📱💻 **Runs on both phone and laptop, and keeps them in sync** (timetable, tasks, profile, memory and owner face data).
- 🗓️ College timetable, ⏰ reminders, ✅ to-dos and deadlines, 📖 study planner, 📞 calling contacts (phone), 🌐 opening websites.

### The six stones

| Stone | Screen |
|---|---|
| 🟣 **Power** | Today: the ULTRON core, next class, what's due |
| 🔵 **Space** | Class timetable |
| 🔴 **Reality** | Tasks, assignments and exams |
| 🟢 **Time** | Study planner |
| 🟡 **Mind** | Chat and voice commands with Ultron |
| 🟠 **Soul** | Owner identity, personal details, Gemini key, sync, memory, settings |

---

## 1. Laptop (Windows)

1. Install Python 3 from python.org (tick **"Add to PATH"**).
2. Double-click **`start-laptop.bat`**. Chrome/Edge opens **http://localhost:8080**.
3. If Windows Firewall asks, click **Allow** on *Private networks* (the phone needs this to sync).
4. Follow the setup: face scan → voice → PIN → your details → Gemini key.

## 2. Phone — easiest: install `ULTRON.apk` (Android 6.0 → latest)

1. Copy **`ULTRON.apk`** to the phone and tap it. If asked, allow **Install unknown apps** for your file manager / browser.
2. Open **ULTRON**, allow camera, microphone, contacts, phone calls and notifications, then do the owner setup.
3. Keep it running: Settings → Apps → ULTRON → Battery → **Unrestricted** (on Xiaomi/Realme/Oppo/Vivo also allow **Autostart**).

Everything runs inside the app (no Termux needed): the ULTRON server, reminders, alarms, calls, contacts, sync,
voice (Android's speech recogniser) and speech (Android's text-to-speech). It starts by itself after a reboot.
Android 6 phones need an up-to-date **Android System WebView** from the Play Store; if it's too old, ULTRON shows an
"update WebView" screen with a button.

To rebuild the APK after changing the code: run `android\build_apk.bat` (uses the toolchain in `%USERPROFILE%\android-build`).

## 2b. Phone — alternative: Termux

Install **F-Droid** (https://f-droid.org), then install **Termux**, **Termux:API** and (optionally) **Termux:Boot** from F-Droid. Don't use the Play Store versions.

Copy this folder to the phone's `Download`, then in Termux:

```bash
termux-setup-storage            # tap Allow
cp -r ~/storage/downloads/student-assistant ~/
cd ~/student-assistant
bash setup.sh
bash start.sh
```

Open **Chrome** at **http://localhost:8080**, allow camera and microphone, and do the owner setup.
In Chrome's ⋮ menu, tap **Add to Home screen** so ULTRON opens like an app.

Stop Android from killing it: Settings → Apps → Termux (and Termux:API) → Battery → **Unrestricted**. On Xiaomi/Realme/Oppo/Vivo, also turn on **Autostart**.

### Same features on phone and laptop

| Feature | Phone | Laptop |
|---|---|---|
| Face, voice, camera, Gemini, thinking, skills | ✓ | ✓ |
| Reminders | Android notification + spoken | pop-up + spoken while ULTRON is open (Soul stone → **Enable pop-up reminders**) |
| "call Amma" | calls directly | **makes the call through your phone** (needs sync set up) |
| Open websites | ✓ | ✓ |
| Install as an app | Chrome ⋮ → **Add to Home screen / Install app** | Edge/Chrome address bar → **Install ULTRON** (or Soul stone → Install ULTRON app) |
| Start automatically | Termux:Boot | run **`install-autostart.bat`** once (undo: `uninstall-autostart.bat`) |

## 3. Sync phone ⇄ laptop

1. Both devices must reach each other: the **same Wi-Fi**, or install **Tailscale** on both (works anywhere).
2. On **both**, go to Soul stone → Sync and enter the **same sync key** (any long secret).
3. On **one** of them (e.g. the laptop), enter the other's address in **Other device's address**. The phone shows its own address on the same screen, e.g. `http://192.168.1.20:8080`.
4. Press **Sync now**. After that it syncs every 30 seconds. The newest change wins, and deletes sync too.

You only need to do the owner setup on one device. The face, voice, PIN and profile sync to the other.

Security: other devices can reach **only** the sync endpoint, and only with the key. Everything else answers only to the device itself (`localhost`).

## 4. Camera access

ULTRON needs the camera for face unlock, vision and the owner guard. In Soul stone → Owner identity, press **Grant camera access** and allow it.
If it says *blocked*: click the camera/lock icon in the address bar → Camera → **Allow**, then reload. On Android also check Settings → Apps → Chrome → Permissions → Camera.
The camera only works when the page is opened as `http://localhost:8080` on the same device.

## 5. Gemini key

Get a free key at **https://aistudio.google.com/apikey** and paste it in Soul stone → AI. Each device keeps its own copy of the key (it is not synced).
The default model is `gemini-flash-latest`. To change it, set `GEMINI_MODEL` in `~/.assistant_env` (phone) or as an environment variable (laptop).

| Setting | Default | Meaning |
|---|---|---|
| `GEMINI_MODEL` | `gemini-flash-latest` | model for normal chat |
| `GEMINI_SOLVER_MODEL` | same as above | model for hard problems (a "pro" model here gives better answers, but slower and pricier) |
| `GEMINI_HEAR_MODEL` | `gemini-3.5-flash-lite` | model that turns your voice into text (fastest one) |
| `GEMINI_THINKING` | `medium` | how hard it thinks in chat: `low` is faster, `high` is smarter |

Tap **"How I thought about it"** under any answer to see its reasoning, which tools it used, and whether it ran code.
Learned skills are listed in the Soul stone (they sync between devices). Delete any you don't want.

---

## When ULTRON uses Gemini

Only when you ask. Say **"use Gemini, …"**, **"ask Gemini …"** or start with **"Gemini, …"**
(e.g. "Ultron, use Gemini, explain Ohm's law"). Everything else (tasks, deadlines, schedule, time, open/search/play,
call, remember) is handled offline. To let it use Gemini automatically for commands it doesn't know,
choose Soul stone → Settings → **When to use Gemini → Automatically**.

## Voice: how ULTRON hears you

- **Gemini hearing** is OFF by default (Mind stone toggle); when off, Chrome's own recogniser is used. When on, ULTRON records your voice itself with
  noise suppression, auto-gain and a hum filter, detects when you start and stop talking, and sends the clip to
  Gemini, which handles Indian accents, mixed Hindi/Telugu/Tamil/Kannada + English, and your own names
  (your name, subjects, tasks, contacts). Takes about 2–4 s. If Gemini fails, the browser recogniser is used.
- **Tap the face / mic** → a chime → speak → it stops by itself when you pause (or tap again to finish early).
- **Keep listening after replies**: after it answers, it listens ~5 s for a follow-up — no need to tap again.
- **Wake word**: on the laptop it hears the whole sentence, e.g. "Ultron, what's due this week?".
  On the phone, say "Ultron", wait for "Yes?", then speak (phones can't share the mic between two listeners).
- Set **Voice language** (Soul stone → Settings) to the language you speak most.
- Tips: speak ~30 cm from the mic; a wired/Bluetooth headset mic is much better than a laptop mic across the room.

## Things you can say

| You say | What happens |
|---|---|
| `Ultron` (wake word on) | "Yes?", then say your command |
| `open youtube` / `play lofi music` / `google black holes` | opens it in the browser |
| `call Amma` | phones the contact (phone only) |
| `today` / `tomorrow` / `next class` / `friday schedule` | your day |
| `add class DBMS wednesday 11:00-12:00 in Lab 3` | adds a weekly class |
| `add task buy notebook tomorrow 5pm` | to-do and reminder |
| `assignment DBMS ER diagram due friday 11pm` / `exam Maths on 20/11 10am` | saves the deadline, reminds you 1 day, 3 h and 1 h before |
| `plan my study` | builds a study timetable |
| `remember that my bike number is TS09 1234` | stored in ULTRON's memory (synced) |
| `who am I` / `what time is it` / `lock` | profile / time / locks the screen |
| `what do you see` / `solve this` / `read this` | takes a photo and Gemini answers about it |
| `do you recognise me` | face check against the owner |
| any maths/code/research question | it thinks, runs code or searches, checks, then answers |
| *anything else* | Gemini works it out |

**Voice notes:** voice input uses Chrome's speech recognition, so it needs internet. Long-press the orb to toggle the always-listening wake word. On Android, Chrome may beep each time listening restarts. The page has to stay open (screen on) to hear the wake word.

## Honest limits of owner recognition

- **Face:** solid for a personal device (it uses face-api.js and runs on your device). Same person ≈ 0.1–0.3 distance, different people ≈ 0.6+, match below 0.5. A printed photo of you could fool it, because there is no liveness check.
- **Voice:** a basic voice-print. It's a convenience, not real security. Use face or PIN for real protection.
- The lock protects the app's screen. Anyone who can use your unlocked laptop could still open the raw database file.
- Phones/laptops without a GPU run face scanning on the CPU, which can take several seconds per scan.

## Files

| File | What it does |
|---|---|
| `server.py` | web server and API; local-only except `/api/sync` |
| `brain.py` | offline commands, ULTRON persona, Gemini (and optional Claude) with tools |
| `sync.py` | two-way phone ⇄ laptop sync |
| `db.py` | SQLite (`assistant.db`); rows carry `uid`/`updated`/`deleted` for syncing |
| `planner.py`, `reminders.py`, `timeparse.py`, `phone.py` | study planner, reminders, date parsing, Termux features |
| `static/index.html` | the ULTRON interface |
| `static/vendor/` | face-api.js and models + Meyda (bundled so it works offline) |
| `start.sh` / `start-laptop.bat` | start on the phone / on Windows |
| `install-autostart.bat` | start ULTRON silently at Windows login |

**Backup:** copy `assistant.db` somewhere safe now and then. With sync on, each device is also a backup of the other.
