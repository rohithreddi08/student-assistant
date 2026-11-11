#!/data/data/com.termux/files/usr/bin/bash
# One-time setup inside Termux. Run:  bash setup.sh
set -e
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

echo "== Updating Termux packages =="
pkg update -y && pkg upgrade -y
pkg install -y python termux-api

echo
echo "== Gemini AI brain (optional, you can also add the key later in the app: Soul stone -> AI) =="
if [ ! -f ~/.assistant_env ] || ! grep -q GEMINI_API_KEY ~/.assistant_env; then
  read -r -p "Paste your Gemini API key (from aistudio.google.com/apikey), or press Enter to skip: " KEY
  if [ -n "$KEY" ]; then
    echo "export GEMINI_API_KEY=\"$KEY\"" >> ~/.assistant_env
    chmod 600 ~/.assistant_env
  fi
fi

echo "== Auto-start when the phone boots (needs the Termux:Boot app) =="
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-assistant.sh <<EOF
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
bash "$APP_DIR/start.sh" > "$APP_DIR/assistant.log" 2>&1
EOF
chmod +x ~/.termux/boot/start-assistant.sh start.sh

echo "== Asking for contacts / phone permissions (tap ALLOW) =="
termux-contact-list > /dev/null || true

echo
echo "Done! Start the assistant with:   bash start.sh"
echo "Then open  http://localhost:8080  in Chrome on this phone. ULTRON will ask to scan your face first."
