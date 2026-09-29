#!/bin/sh
# Boot the portal: apply migrations, then serve. Nothing here uses the network.
set -eu

cd /app/src

echo "raptor-desk: checking configuration (every route must declare an access policy)"
python manage.py check --fail-level ERROR

echo "raptor-desk: applying database migrations"
python manage.py migrate --noinput

echo "raptor-desk: safety gate (production never starts with demo credentials)"
python manage.py safety_gate

echo "raptor-desk: seeding (demo profile loads the official fixtures)"
python manage.py seed

echo "raptor-desk: serving on http://localhost:8080"
# No gunicorn access log: it would print raw URLs, including invite and sign-in
# tokens. The portal logs each request itself, by route pattern (core/logs.py).
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8080 \
    --workers "${RD_WEB_WORKERS:-3}" \
    --timeout 60 \
    --error-logfile -
