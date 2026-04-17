from datetime import datetime, timedelta

import nightly_sync


def _ast_now() -> datetime:
    # Barbados AST is UTC-4 year-round.
    return datetime.utcnow() + timedelta(hours=-4)


def should_run_now(now_ast: datetime) -> bool:
    # Tue–Sat (Mon=0 ... Sun=6)
    if now_ast.weekday() not in (1, 2, 3, 4, 5):
        return False
    # 20:00 AST
    return now_ast.hour == 20 and now_ast.minute == 0


def main():
    now_ast = _ast_now()
    print(f"scheduled_nightly check AST={now_ast.isoformat(timespec='seconds')}")
    if not should_run_now(now_ast):
        print("Not scheduled time/day for nightly sync. Exiting.")
        return

    nightly_sync.main()


if __name__ == "__main__":
    main()

