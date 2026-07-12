"""Draft lesson_plans.body_markdown for A1 lessons via the Claude API.

This is a one-off authoring tool, not part of the running app. It writes
plans as status='draft' — nothing goes live until a parent reviews and hits
"Approve" in the existing curriculum editor UI.

Content is original: grammar *points* are informed by NHK World "Easy
Japanese"'s scope-and-sequence (see GRAMMAR_SEEDS below), but no NHK/Marugoto
text, vocab lists, or dialogue is copied. Situations are reframed around the
family's actual context — two teens (13-16) doing a homestay exchange in
Japan during Obon (mid-August), living with a host family that has same-age
kids. That means: no "current school year" assumptions (it's summer
vacation), and festival content leans specifically into Obon customs.

Usage:
    cd backend && .venv/bin/python scripts/generate_lesson_plans.py
    .venv/bin/python scripts/generate_lesson_plans.py --dry-run   # print only
    .venv/bin/python scripts/generate_lesson_plans.py --lesson T09_FESTIVALS_A1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import insert, select, update  # noqa: E402

from app.db import (  # noqa: E402
    get_engine,
    init_db,
    lesson_plans_table,
    lessons_table,
    topics_table,
    users_table,
)

MODEL = "claude-opus-4-8"

# --------------------------------------------------------------------------- #
# Grammar seeds — one target grammar point per A1 lesson, informed by NHK
# World "Easy Japanese"'s lesson-by-lesson progression (lessons 1-24 sit
# squarely in A1 scope). These are *seeds* for the model, not literal NHK
# example sentences.
# --------------------------------------------------------------------------- #

GRAMMAR_SEEDS: dict[str, str] = {
    "T01_GREETINGS_A1": "です self-introduction (name) + 〜から来ました (where you're from)",
    "T01_GREETINGS_A1a": "はじめまして + どうぞよろしく (polite first meeting); "
    "お世話になります as the standard homestay-arrival thank-you phrase",
    "T02_FAMILY_A1": "〜人家族です / 〜がいます (counting family members, existence)",
    "T02_FAMILY_A1a": "〜は〜です + の (introducing one specific member); "
    "何人家族ですか (asking someone else)",
    "T03_SCHOOL_A1": "〜年生です (grade) + 〜が好きです (favorite subject)",
    "T03_SCHOOL_A1a": "〜年生です + 夏休み/宿題 vocabulary (summer vacation, homework) — "
    "NOT a regular school-day routine, it's currently summer break",
    "T04_FRIENDS_A1": "友だちです + 〜が好きです (introducing a friend + one like)",
    "T04_FRIENDS_A1a": "〜ましょう (let's...) + いいですね (enthusiastic agreement)",
    "T05_HOBBIES_A1": "〜が好きです / 〜をします (simple hobby statement + question)",
    "T05_HOBBIES_A1a": "〜てみたいです (want to try doing something)",
    "T06_FOOD_A1": "〜が好きです + 〜をください (naming a liked food, simple ordering). "
    "Prefer foods teenagers actually eat and get excited about — burgers, pizza, "
    "sandwiches, pasta, tonkatsu, karaage, ramen, sushi — over traditional basics",
    "T06_FOOD_A1a": "〜が好きです + 〜は食べられません + 〜アレルギーがあります "
    "(polite dietary restriction) at a family dinner table. Use modern/Western + "
    "popular Japanese foods (ハンバーガー, ピザ, パスタ, とんかつ, からあげ, ラーメン). "
    "Make ピーナッツ (peanut) the drilled allergy example — it's a real, serious one",
    "T07_DAILY_A1": "〜時に〜ます (time + verb: wake up, go to bed)",
    "T07_DAILY_A1a": "〜ています (present progressive) + 何時に〜ますか (asking about "
    "meal/bath time in someone else's home)",
    "T08_TRAVEL_A1": "〜に行きたいです (want to go somewhere)",
    "T08_TRAVEL_A1a": "〜はどこですか (asking where a place is) + simple direction "
    "words (ここ/そこ/あそこ, 近く)",
    "T09_FESTIVALS_A1": "〜をします / 〜に (naming an event + when it happens) — "
    "anchor the whole lesson specifically on Obon (お盆), since that's the "
    "actual holiday happening during the visit",
    "T09_FESTIVALS_A1a": "adjective + です/ね (describing festival clothing/colors) — "
    "specifically 浴衣 (yukata) and 花火大会 (fireworks festival) as worn to Obon events",
    "T10_ANIME_A1": "〜です + simple adjectives (かっこいい/かわいい) describing a character",
    "T10_ANIME_A1a": "short adjective exclamations (すごい!/おもしろい!) as a reaction",
    "T11_SPORTS_A1": "〜が好きです / 〜をします (naming a sport, asking someone else)",
    "T11_SPORTS_A1a": "simple past 〜ました (said what you did yesterday, e.g. swam, "
    "watched fireworks, danced at the festival)",
    "T12_PETS_A1": "〜がいます + adjectives (color, size) describing a pet",
    "T12_PETS_A1a": "〜ないでください (gentle negative request / house rule)",
}

SYSTEM_PROMPT = """\
You are drafting a lesson plan for a home-built Japanese conversation tutor \
app used by a family. The plan you write is injected verbatim into the AI \
tutor's system prompt for every session on this lesson, under the heading \
"Plan from the parent (use this as your guide)" — so write it as direct \
guidance TO the tutor persona, not as a student-facing textbook page.

CONTEXT — read carefully, it should shape every lesson:
The learner is ONE teenager (13-16) doing a short-term exchange homestay in \
Japan, arriving during Obon (mid-August). IMPORTANT: the learner travels and \
stays ALONE with their own host family — do NOT write scenarios about "two \
siblings together." Address the learner in the singular ("you"). The host \
family has children about the same age. This means:
- It is SUMMER VACATION, not a regular school term — don't assume a daily \
  school schedule, class period, or "today's classes" framing.
- Scenarios should default to homestay life: meeting the host family, \
  meals together, house rules, going places with the host kids, local \
  summer events — NOT a generic school-year or business-trip framing.
- Where a lesson is about festivals/special days, prefer Obon specifically \
  (お盆, 盆踊り bon-odori, 提灯 lanterns, お墓参り visiting graves, 花火 \
  fireworks, 夏祭り summer festival, 浴衣 yukata, かき氷 shaved ice, スイカ \
  splitting) over generic year-round holidays.
- Keep locations GENERIC — say "your host town" / "your host family," never \
  a specific city or region (learners are in different towns).
- Learners are teenagers: keep it age-appropriate for 13-16, not little-kid \
  cutesy and not adult/business register.

REGISTER — this matters for a homestay and every lesson must address it: \
the learner speaks POLITELY (です/ます, おねがいします, おせわになります) to the \
host PARENTS, but can be relaxed and CASUAL with the same-age host KIDS. \
Teach both and note when to switch.

GRAMMAR/VOCAB REGISTER: match the natural, spoken beginner Japanese style \
used by NHK World's "Easy Japanese" course — plain, practical, high-frequency \
words and short sentences appropriate for absolute beginners (JFS/CEFR A1). \
Do NOT reproduce any NHK or Marugoto text, character names, or specific \
example sentences verbatim — this is original content inspired only by the \
*grammar point and difficulty level*, not the source material's wording.

OUTPUT FORMAT — return ONLY the markdown below, no preamble, no code fences:

## Scenario
One to two sentences setting the specific singular-learner homestay/Obon scene \
for this lesson (one learner, one host family, generic town).

## Target vocabulary
8-12 items, each: **word** (reading in hiragana/romaji) — English gloss. \
Pick words a genuine beginner needs for this scenario.

## Key sentence patterns
2-4 short patterns built around the target grammar point, each with an \
English gloss.

## Register — who you're talking to
2 short bullets: how to say it politely to the host parents vs casually to \
the same-age host kids, with a mini-example of each.

## Example exchange
A natural 3-5 turn sample dialogue between the tutor and the learner \
demonstrating the target grammar in the lesson's scenario. Use 「Tutor:」 \
and 「Learner:」 labels; where natural, show the polite-vs-casual contrast.

## Tutor notes
2-3 bullet points: what mistakes to expect at this level, how to gently \
elicit the can-do goals (leaning on the learner's own hobbies/interests \
where natural), and a one-line cultural note about Obon or homestay etiquette.
"""


def build_user_prompt(
    topic_title: str, lesson_title: str, can_dos: list[str], grammar_seed: str
) -> str:
    can_do_lines = "\n".join(f"- {cd}" for cd in can_dos)
    return f"""\
Topic: {topic_title}
Lesson: {lesson_title}
Can-do goals for this lesson:
{can_do_lines}

Target grammar point (seed only — do not copy any source's example \
sentences): {grammar_seed}

Write the lesson plan now, following the format exactly."""


def fetch_a1_lessons(engine, only_codes: list[str] | None) -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(
                    lessons_table.c.id,
                    lessons_table.c.code,
                    lessons_table.c.title_en,
                    lessons_table.c.can_dos_json,
                    topics_table.c.title_en.label("topic_title_en"),
                )
                .join(topics_table, topics_table.c.id == lessons_table.c.topic_id)
                .where(lessons_table.c.level == "A1")
                .order_by(lessons_table.c.sort_order)
            )
            .mappings()
            .all()
        )
    lessons = [dict(r) for r in rows]
    if only_codes:
        wanted = set(only_codes)
        lessons = [ln for ln in lessons if ln["code"] in wanted]
    return lessons


def existing_plan_status(engine, lesson_id: int) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(lesson_plans_table.c.status).where(
                lesson_plans_table.c.lesson_id == lesson_id
            )
        ).one_or_none()
    return row[0] if row else None


def admin_user_id(engine) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(users_table.c.id).where(users_table.c.is_admin == 1).limit(1)
        ).one_or_none()
    return row[0] if row else None


def write_draft(engine, lesson_id: int, body_markdown: str, updated_by: int | None) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        existing = conn.execute(
            select(lesson_plans_table.c.id, lesson_plans_table.c.version).where(
                lesson_plans_table.c.lesson_id == lesson_id
            )
        ).one_or_none()
        if existing is None:
            conn.execute(
                insert(lesson_plans_table).values(
                    lesson_id=lesson_id,
                    body_markdown=body_markdown,
                    status="draft",
                    version=1,
                    updated_at=now,
                    updated_by=updated_by,
                )
            )
        else:
            conn.execute(
                update(lesson_plans_table)
                .where(lesson_plans_table.c.id == existing[0])
                .values(
                    body_markdown=body_markdown,
                    status="draft",
                    version=existing[1] + 1,
                    updated_at=now,
                    updated_by=updated_by,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print generated markdown, don't write to DB"
    )
    parser.add_argument(
        "--lesson",
        default=None,
        help="Only generate for these lesson code(s), comma-separated (e.g. T09_FESTIVALS_A1,T01_GREETINGS_A1)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite even lessons that already have an approved plan (default: skip them)",
    )
    args = parser.parse_args()

    client = anthropic.Anthropic()
    engine = get_engine()
    init_db(engine)

    only_codes = [c.strip() for c in args.lesson.split(",")] if args.lesson else None
    lessons = fetch_a1_lessons(engine, only_codes)
    if not lessons:
        print("No matching A1 lessons found.")
        return

    admin_id = admin_user_id(engine)
    results: list[dict] = []

    for i, lesson in enumerate(lessons, start=1):
        code = lesson["code"]
        status = existing_plan_status(engine, lesson["id"])
        if status == "approved" and not args.force:
            print(f"[{i}/{len(lessons)}] {code}: skipping (already approved)")
            continue

        grammar_seed = GRAMMAR_SEEDS.get(code)
        if grammar_seed is None:
            print(f"[{i}/{len(lessons)}] {code}: WARNING no grammar seed defined, skipping")
            continue

        can_dos = json.loads(lesson["can_dos_json"] or "[]")
        user_prompt = build_user_prompt(
            lesson["topic_title_en"], lesson["title_en"], can_dos, grammar_seed
        )

        print(f"[{i}/{len(lessons)}] {code}: generating...")
        # Note: this repo pins anthropic==0.49.0 (predates `thinking` /
        # `output_config`), so this is a plain, non-thinking request.
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        body_markdown = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

        results.append({"code": code, "body_markdown": body_markdown})

        if args.dry_run:
            print(f"--- {code} ---\n{body_markdown}\n")
        else:
            write_draft(engine, lesson["id"], body_markdown, admin_id)
            print(f"[{i}/{len(lessons)}] {code}: saved as draft")

    out_path = Path(__file__).resolve().parent / "generated_lesson_plans.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nDone. {len(results)} plan(s) generated. Log: {out_path}")


if __name__ == "__main__":
    main()
