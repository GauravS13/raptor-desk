#!/bin/sh
# Background worker: delivers queued emails, webhooks and result snapshots.
set -eu

cd /app/src
exec python manage.py run_outbox
