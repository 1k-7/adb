#!/bin/bash
set -e

echo "Starting background processes..."
# Start the admin bot in the background
python -u bot.py &

# Start the session worker in the background
python -u worker.py &

echo "Starting web service in foreground..."
#
# --- THIS IS THE FIX ---
# Force Gunicorn to use only ONE worker to avoid conflicts
exec gunicorn --bind 0.0.0.0:$PORT --workers=1 main:app
