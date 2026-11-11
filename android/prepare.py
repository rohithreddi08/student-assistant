"""Copies the ULTRON app (Python + web interface) into the Android project and makes the launcher icons.
Run before every APK build (build_apk.bat does this for you)."""
import os
import shutil
import subprocess

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "app", "src", "main")
PY_FILES = ["server.py", "brain.py", "db.py", "sync.py", "phone.py", "planner.py", "reminders.py", "timeparse.py"]

# 1. Python source (android_main.py already lives there)
py_dir = os.path.join(MAIN, "python")
os.makedirs(py_dir, exist_ok=True)
for f in PY_FILES:
    shutil.copy2(os.path.join(ROOT, f), os.path.join(py_dir, f))

# 2. Web interface -> assets/static (unpacked on the phone by UltronService), compiled for old WebViews
#    (Android 6 stock WebView = Chrome 44 ... latest) by web/build.mjs: Babel + core-js + PostCSS fallbacks.
dst = os.path.join(MAIN, "assets", "static")
subprocess.run(["node", os.path.join(HERE, "web", "build.mjs"), os.path.join(ROOT, "static"), dst], check=True)

# 3. Launcher icons from the ULTRON face icon
src = Image.open(os.path.join(ROOT, "static", "icon-512.png")).convert("RGB")
for folder, size in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
    d = os.path.join(MAIN, "res", f"mipmap-{folder}")
    os.makedirs(d, exist_ok=True)
    src.resize((size, size), Image.LANCZOS).save(os.path.join(d, "ic_launcher.png"))

print("prepared:", len(PY_FILES), "python files, static assets, icons")
