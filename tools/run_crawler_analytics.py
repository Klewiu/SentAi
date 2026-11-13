"""MyDevil/FreeBSD Cron entry point for crawler analytics aggregation."""
import os
from pathlib import Path
import sys


def main():
    import fcntl

    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    os.chdir(project)
    os.environ["DJANGO_SETTINGS_MODULE"] = "sentai.settings.prod"
    runtime = project / "logs"
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / "crawler-analytics.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Crawler analytics aggregation is already running; skipping this invocation.")
            return

        from datetime import datetime, timezone
        from django.core.management import execute_from_command_line

        print(f"Crawler analytics started: {datetime.now(timezone.utc).isoformat()}", flush=True)
        execute_from_command_line(["manage.py", "process_crawler_analytics", "--batch-size=2000"])
        print(f"Crawler analytics completed: {datetime.now(timezone.utc).isoformat()}", flush=True)


if __name__ == "__main__":
    main()
