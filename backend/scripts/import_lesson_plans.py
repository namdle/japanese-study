"""Apply the committed A1 lesson plans (scripts/lesson_plans_a1.json) to the DB.

Container-safe: uses get_engine() (settings-based) so it targets whichever DB
the app uses. Upsert semantics per lesson code:
  - plan exists → update body_markdown (bump version), KEEP its status
    (an already-approved lesson keeps serving with the improved content);
  - plan missing → insert as DRAFT (an admin approves it later);
  - body unchanged → skip (idempotent, no version churn).

Usage (local):      .venv/bin/python scripts/import_lesson_plans.py
Usage (prod):       docker exec japanese-study python scripts/import_lesson_plans.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

try:  # best-effort; in the container env vars come from docker --env-file
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except Exception:  # pragma: no cover - dotenv optional at runtime
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import insert, select, update  # noqa: E402

from app.db import (  # noqa: E402
    get_engine,
    init_db,
    lesson_plans_table,
    lessons_table,
)

DATA_PATH = Path(__file__).resolve().parent / "lesson_plans_a1.json"


def main() -> None:
    engine = get_engine()
    init_db(engine)  # ensure schema + curriculum taxonomy exist first
    plans = json.loads(DATA_PATH.read_text())
    now = datetime.now(UTC).replace(tzinfo=None)

    with engine.connect() as conn:
        code_to_id = {
            r.code: r.id
            for r in conn.execute(select(lessons_table.c.code, lessons_table.c.id))
        }

    inserted = updated = skipped = 0
    for entry in plans:
        code = entry["code"]
        body = entry["body_markdown"]
        lesson_id = code_to_id.get(code)
        if lesson_id is None:
            print(f"WARNING: lesson {code} not found, skipping")
            continue
        with engine.begin() as conn:
            existing = conn.execute(
                select(
                    lesson_plans_table.c.id,
                    lesson_plans_table.c.version,
                    lesson_plans_table.c.body_markdown,
                ).where(lesson_plans_table.c.lesson_id == lesson_id)
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(lesson_plans_table).values(
                        lesson_id=lesson_id,
                        body_markdown=body,
                        status="draft",
                        version=1,
                        updated_at=now,
                        updated_by=None,
                    )
                )
                inserted += 1
            elif existing.body_markdown == body:
                skipped += 1
            else:
                conn.execute(
                    update(lesson_plans_table)
                    .where(lesson_plans_table.c.id == existing.id)
                    .values(
                        body_markdown=body,
                        version=existing.version + 1,
                        updated_at=now,
                    )  # status intentionally preserved
                )
                updated += 1

    print(
        f"lesson plans: +{inserted} inserted (draft), {updated} updated "
        f"(status preserved), {skipped} unchanged"
    )


if __name__ == "__main__":
    main()
