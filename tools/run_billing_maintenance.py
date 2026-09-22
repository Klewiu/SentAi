"""Cron entry point for MyDevil/FreeBSD. Run with the project's virtualenv Python."""
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
    # The OS releases this lock even if the job crashes. Keep the lock file.
    with (runtime / "billing-maintenance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Billing maintenance is already running; skipping this invocation.")
            return
        from datetime import datetime, timezone
        from django.core.management import execute_from_command_line

        print(f"Billing maintenance started: {datetime.now(timezone.utc).isoformat()}", flush=True)
        execute_from_command_line(["manage.py", "maintain_billing"])
        print(f"Billing maintenance completed: {datetime.now(timezone.utc).isoformat()}", flush=True)


if __name__ == "__main__":
    main()
