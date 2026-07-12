"""Export the A1 lesson plans to a committed JSON file.

LLM output isn't reproducible, so the reviewed lesson content is captured as
version-controlled data (scripts/lesson_plans_a1.json) and applied to any
environment with import_lesson_plans.py. Run locally after finalizing content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import get_engine, lessons_table, lesson_plans_table  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "lesson_plans_a1.json"


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(lessons_table.c.code, lesson_plans_table.c.body_markdown)
            .join(lessons_table, lessons_table.c.id == lesson_plans_table.c.lesson_id)
            .where(lessons_table.c.level == "A1")
            .order_by(lessons_table.c.sort_order)
        ).all()
    data = [{"code": code, "body_markdown": body} for code, body in rows]
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"exported {len(data)} A1 lesson plans → {OUT_PATH}")


if __name__ == "__main__":
    main()
