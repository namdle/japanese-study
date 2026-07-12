"""Seed each child's known hobbies into topic_interests.

The tutor's system prompt surfaces the learner's top-3 interests, so pre-seeding
them makes the tutor personal from the first session (it otherwise learns them
gradually from conversation). Idempotent: skips a (user, keyword) already present.

Runs against whatever DB the environment points to (local by default). Pass
nothing — it looks up the children by name.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import insert, select  # noqa: E402

from app.db import get_engine, init_db, topic_interests_table, users_table  # noqa: E402

# name -> [(keyword, weight)]; higher weight surfaces first (top 3 used).
INTERESTS: dict[str, list[tuple[str, int]]] = {
    "Mia": [
        ("manga", 6),
        ("violin", 6),
        ("art and crafts", 5),
        ("double bass", 4),
        ("creative writing", 4),
    ],
    "Khoi": [
        ("manga", 6),
        ("soccer", 6),
        ("lego", 5),
        ("3d printing", 4),
    ],
}


def main() -> None:
    engine = get_engine()
    init_db(engine)
    now = datetime.now(UTC).replace(tzinfo=None)

    with engine.connect() as conn:
        name_to_id = {
            r.name: r.id
            for r in conn.execute(select(users_table.c.name, users_table.c.id))
        }

    for name, items in INTERESTS.items():
        user_id = name_to_id.get(name)
        if user_id is None:
            print(f"WARNING: user {name!r} not found, skipping")
            continue
        with engine.connect() as conn:
            existing = {
                r.keyword
                for r in conn.execute(
                    select(topic_interests_table.c.keyword).where(
                        topic_interests_table.c.user_id == user_id
                    )
                )
            }
        rows = [
            {
                "user_id": user_id,
                "keyword": kw,
                "weight": w,
                "last_seen_at": now,
            }
            for kw, w in items
            if kw not in existing
        ]
        if rows:
            with engine.begin() as conn:
                conn.execute(insert(topic_interests_table), rows)
        print(f"{name}: +{len(rows)} interests ({len(items) - len(rows)} already present)")
    print("done.")


if __name__ == "__main__":
    main()
