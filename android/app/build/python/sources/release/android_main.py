"""Entry point used by the Android app (UltronService.java). Runs the normal ULTRON server inside the APK."""
import os


def start(files_dir, static_dir):
    os.environ["ULTRON_ANDROID_APP"] = "1"          # phone.py -> use the app's Java bridge
    os.environ["ASSISTANT_DB"] = os.path.join(files_dir, "assistant.db")
    os.environ["ULTRON_STATIC"] = static_dir
    os.environ.setdefault("ASSISTANT_PORT", "8080")
    import server                                   # imported after the environment is ready
    server.main()                                   # blocks: serves http://localhost:8080
