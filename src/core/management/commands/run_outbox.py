import signal
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.module_loading import autodiscover_modules

from core.outbox import process_due


class Command(BaseCommand):
    help = "Deliver queued outbox messages (emails, webhooks, snapshots) with retries."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true", help="Process due messages and exit.")
        parser.add_argument("--interval", type=float, default=1.0, help="Seconds between polls.")

    def handle(self, *args: Any, **options: Any) -> None:
        autodiscover_modules("handlers")
        if options["once"]:
            delivered = process_due()
            self.stdout.write(f"delivered {delivered}")
            return

        running = True

        def stop(signum: int, frame: Any) -> None:
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.stdout.write("raptor-desk worker: processing the outbox")
        while running:
            process_due()
            time.sleep(options["interval"])
        self.stdout.write("raptor-desk worker: stopped")
