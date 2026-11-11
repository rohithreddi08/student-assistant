#!/data/data/com.termux/files/usr/bin/bash
# Start the assistant. Keeps running in the background so reminders keep firing.
cd "$(dirname "$0")"
[ -f ~/.assistant_env ] && source ~/.assistant_env
# Keep the CPU awake so reminders aren't delayed when the screen is off
command -v termux-wake-lock >/dev/null && termux-wake-lock
# Restart automatically if it ever crashes
while true; do
  python server.py
  echo "Assistant stopped, restarting in 5 s..."
  sleep 5
done
