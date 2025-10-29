#!/bin/bash
set -e

echo "Starting background processes..."
# Start the admin bot in the background
python -u bot.py &

# Start the session worker in the background
python -u worker.py &

echo "Starting web service in foreground..."
# Start Gunicorn in the foreground
# Koyeb will health-check this process
exec gunicorn --bind 0.0.0.0:$PORT main:app