import os
import time
from datetime import datetime, timedelta

from config import settings
import live_sync_runner


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _ast_now() -> datetime:
    """
    Barbados is Atlantic Standard Time (AST), UTC-4, no DST.
    We keep this as a fixed offset to avoid server timezone issues.
    """
    return datetime.utcnow() + timedelta(hours=-4)


def _is_active_window(now_ast: datetime) -> bool:
    # Tue–Sat (Python weekday: Mon=0 ... Sun=6)
    if now_ast.weekday() not in (1, 2, 3, 4, 5):
        return False

    minutes = now_ast.hour * 60 + now_ast.minute
    start_min = 7 * 60 + 30   # 07:30
    end_min = 19 * 60 + 30    # 19:30
    return start_min <= minutes < end_min


def _seconds_until_next_boundary(now_ast: datetime) -> int:
    """
    If we're outside the active window, sleep until the next start boundary.
    If we're inside, this isn't used.
    """
    # Compute today's window start/end in AST
    start = now_ast.replace(hour=7, minute=30, second=0, microsecond=0)
    end = now_ast.replace(hour=19, minute=30, second=0, microsecond=0)

    if now_ast.weekday() in (1, 2, 3, 4, 5):
        if now_ast < start:
            return max(5, int((start - now_ast).total_seconds()))
        if now_ast >= end:
            # next day start
            next_day = (now_ast + timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
            return max(5, int((next_day - now_ast).total_seconds()))

    # If it's Sun/Mon or outside Tue–Sat, find next Tuesday 07:30
    # weekday: Mon=0 ... Sun=6
    days_ahead = (1 - now_ast.weekday()) % 7  # days until Tuesday
    if days_ahead == 0 and now_ast.weekday() != 1:
        days_ahead = 7
    target = (now_ast + timedelta(days=days_ahead)).replace(hour=7, minute=30, second=0, microsecond=0)
    return max(5, int((target - now_ast).total_seconds()))

def main():
    interval_s = int((os.getenv("SYNC_INTERVAL_SECONDS") or "60").strip() or "60")
    run_once = _env_bool("RUN_ONCE", default=False)
    respect_window = _env_bool("RESPECT_ACTIVE_WINDOW", default=True)

    print(f"APP_ENV={settings.APP_ENV} DRY_RUN={settings.DRY_RUN}")
    print(
        f"Worker loop starting. interval={interval_s}s run_once={run_once} "
        f"respect_active_window={respect_window}"
    )

    while True:
        now_ast = _ast_now()
        if respect_window and not _is_active_window(now_ast):
            sleep_s = _seconds_until_next_boundary(now_ast)
            print(
                f"Outside active window (AST={now_ast.isoformat(timespec='seconds')}). "
                f"Sleeping {sleep_s}s until next window."
            )
            time.sleep(sleep_s)
            continue

        try:
            live_sync_runner.main()
        except Exception as e:
            # Keep the worker alive; errors should be visible in logs.
            print(f"WARNING: live_sync_runner crashed: {e}")

        if run_once:
            break

        time.sleep(max(5, interval_s))


if __name__ == "__main__":
    main()

