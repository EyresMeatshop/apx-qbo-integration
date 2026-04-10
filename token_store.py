import os
from pathlib import Path


def get_active_env_file() -> str:
    return os.getenv("APP_ENV_FILE", ".env")


def update_env_file(env_file: str, updates: dict[str, str]) -> None:
    """
    Best-effort .env updater for local development.

    On cloud hosts the filesystem is often read-only; callers should catch failures
    and persist secrets elsewhere (database).
    """
    path = Path(env_file)
    if not path.exists():
        path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    keys = set(updates.keys())
    new_lines: list[str] = []
    seen = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue

        k = stripped.split("=", 1)[0].strip()
        if k in keys:
            new_lines.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
