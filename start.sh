#!/bin/bash
set -e

echo "🧹 Cleaning up old X11 locks..."
rm -f /tmp/.X*-lock || true

echo "🖥️ Starting Xvfb virtual frame buffer on display :99..."
Xvfb :99 -ac -screen 0 1920x1080x24 > /dev/null 2>&1 &

# Wait briefly for Xvfb to start
sleep 2

# Export display variable
export DISPLAY=:99

echo "🏁 Running Hot Wheels alert bot..."
exec python -u bot.py
