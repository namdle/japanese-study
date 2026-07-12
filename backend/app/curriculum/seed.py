"""Seed the kid-friendly curriculum taxonomy.

Twelve topics covering everyday family/school/social life. Each topic has
two A1 lessons (the second grammar seed informed by NHK World "Easy
Japanese"'s scope-and-sequence, reframed for an exchange-student homestay
during Obon), plus one A2 and one B1 lesson. Can-do statements are
intentionally generic and original — they reflect the *kind* of practice
without copying any copyrighted course material.

The seed function is idempotent: running it on an existing DB inserts only
missing topics/lessons and leaves user-authored plans alone. Lesson codes
are `{topic_code}_{level}` for a topic's first lesson at a level (preserving
existing codes) and `{topic_code}_{level}{letter}` for subsequent lessons at
the same level, e.g. `T01_GREETINGS_A1` then `T01_GREETINGS_A1b`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.db import lessons_table, topics_table

# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

TOPICS: list[dict[str, str | int]] = [
    {
        "code": "T01_GREETINGS",
        "title_en": "Greetings & Self-Introduction",
        "title_ja": "あいさつと自己紹介",
        "sort_order": 1,
    },
    {
        "code": "T02_FAMILY",
        "title_en": "Family & Home",
        "title_ja": "家族と家",
        "sort_order": 2,
    },
    {
        "code": "T03_SCHOOL",
        "title_en": "School Life",
        "title_ja": "学校生活",
        "sort_order": 3,
    },
    {
        "code": "T04_FRIENDS",
        "title_en": "Friends & Hangouts",
        "title_ja": "友だちと遊び",
        "sort_order": 4,
    },
    {
        "code": "T05_HOBBIES",
        "title_en": "Hobbies & Free Time",
        "title_ja": "趣味と自由時間",
        "sort_order": 5,
    },
    {
        "code": "T06_FOOD",
        "title_en": "Food & Cooking",
        "title_ja": "食べ物と料理",
        "sort_order": 6,
    },
    {
        "code": "T07_DAILY",
        "title_en": "Daily Routines",
        "title_ja": "毎日の生活",
        "sort_order": 7,
    },
    {
        "code": "T08_TRAVEL",
        "title_en": "Travel & Places",
        "title_ja": "旅行と場所",
        "sort_order": 8,
    },
    {
        "code": "T09_FESTIVALS",
        "title_en": "Festivals & Celebrations",
        "title_ja": "お祭りとお祝い",
        "sort_order": 9,
    },
    {
        "code": "T10_ANIME",
        "title_en": "Anime, Manga & Pop Culture",
        "title_ja": "アニメ・マンガ・ポップカルチャー",
        "sort_order": 10,
    },
    {
        "code": "T11_SPORTS",
        "title_en": "Sports & Outdoors",
        "title_ja": "スポーツとアウトドア",
        "sort_order": 11,
    },
    {
        "code": "T12_PETS",
        "title_en": "Pets & Animals",
        "title_ja": "ペットと動物",
        "sort_order": 12,
    },
]


# Three lessons per topic at A1 / A2 / B1.
# Each entry: (topic_code, level, title_en, title_ja, can_dos)
LESSONS: list[tuple[str, str, str, str, list[str]]] = [
    # T01 Greetings
    ("T01_GREETINGS", "A1", "Saying hi", "こんにちは", [
        "Greet someone using simple expressions",
        "Introduce yourself with your name and where you're from",
    ]),
    ("T01_GREETINGS", "A1", "Nice to meet you", "はじめまして", [
        "Introduce yourself politely when meeting a host family for the first time",
        "Say a simple thank-you for their hospitality",
    ]),
    ("T01_GREETINGS", "A2", "Meeting new friends", "新しい友だちに会う", [
        "Make small talk after a greeting",
        "Ask what someone likes to do",
    ]),
    ("T01_GREETINGS", "B1", "Catching up", "ひさしぶり", [
        "Greet someone you haven't seen in a while",
        "Briefly explain what you've been up to",
    ]),
    # T02 Family
    ("T02_FAMILY", "A1", "My family", "家族", [
        "Say how many people are in your family",
        "Name each family member",
    ]),
    ("T02_FAMILY", "A1", "This is my family", "わたしの家族です", [
        "Introduce one family member with a simple detail",
        "Ask how many people are in someone else's family",
    ]),
    ("T02_FAMILY", "A2", "What we do at home", "家ですること", [
        "Describe a typical evening at home",
        "Talk about who does which chore",
    ]),
    ("T02_FAMILY", "B1", "Family traditions", "家族の習慣", [
        "Describe a family tradition or routine",
        "Compare your family with someone else's",
    ]),
    # T03 School
    ("T03_SCHOOL", "A1", "My class", "クラス", [
        "Say what grade or class you're in",
        "Name a couple of subjects you study",
    ]),
    ("T03_SCHOOL", "A1", "My summer vacation", "夏休み", [
        "Say what grade you're in",
        "Talk about summer vacation and homework",
    ]),
    ("T03_SCHOOL", "A2", "A day at school", "学校の一日", [
        "Talk about your school schedule",
        "Say which subject is your favorite and why",
    ]),
    ("T03_SCHOOL", "B1", "School events", "学校行事", [
        "Describe a school event you joined",
        "Share what was fun and what was hard",
    ]),
    # T04 Friends
    ("T04_FRIENDS", "A1", "My friend", "友だち", [
        "Introduce a friend by name",
        "Say one thing your friend likes",
    ]),
    ("T04_FRIENDS", "A1", "Let's do it together", "いっしょにやろう", [
        "Invite someone to do something together",
        "Agree enthusiastically to a suggestion",
    ]),
    ("T04_FRIENDS", "A2", "Hanging out", "あそぶ", [
        "Make plans to do something together",
        "Agree on a time and place",
    ]),
    ("T04_FRIENDS", "B1", "Sharing stories", "話を共有する", [
        "Tell a short story about something fun you did with a friend",
        "React to a story with sympathy or excitement",
    ]),
    # T05 Hobbies
    ("T05_HOBBIES", "A1", "What I like", "好きなこと", [
        "Say something you like to do",
        "Ask what someone else likes",
    ]),
    ("T05_HOBBIES", "A1", "I want to try it", "やってみたいです", [
        "Say you want to try a new hobby or activity",
        "Ask someone to show you how",
    ]),
    ("T05_HOBBIES", "A2", "Free-time plans", "自由時間の予定", [
        "Talk about what you usually do on weekends",
        "Suggest a hobby to a friend",
    ]),
    ("T05_HOBBIES", "B1", "Why I love it", "好きな理由", [
        "Explain why you enjoy a hobby",
        "Describe how you got started",
    ]),
    # T06 Food
    ("T06_FOOD", "A1", "Favorite food", "好きな食べ物", [
        "Name a food you like",
        "Order something simple at a restaurant or shop",
    ]),
    ("T06_FOOD", "A1", "At the dinner table", "ばんごはん", [
        "Say what food you like at a family meal",
        "Politely say you can't eat something",
    ]),
    ("T06_FOOD", "A2", "Cooking at home", "家で料理", [
        "Talk about a dish you can make",
        "Describe ingredients in simple terms",
    ]),
    ("T06_FOOD", "B1", "Food memories", "食べ物の思い出", [
        "Describe a memorable meal",
        "Compare flavors and textures",
    ]),
    # T07 Daily
    ("T07_DAILY", "A1", "My day", "わたしの一日", [
        "Tell what time you wake up and go to bed",
        "Describe one thing you do every morning",
    ]),
    ("T07_DAILY", "A1", "Life with my host family", "ホストファミリーとの毎日", [
        "Describe something you're doing right now",
        "Ask what time meals happen",
    ]),
    ("T07_DAILY", "A2", "Busy days", "いそがしい日", [
        "Talk through a busy weekday",
        "Say what you do after dinner",
    ]),
    ("T07_DAILY", "B1", "A productive routine", "じょうずな一日の使い方", [
        "Explain a routine that works well for you",
        "Talk about a habit you'd like to change",
    ]),
    # T08 Travel
    ("T08_TRAVEL", "A1", "Where I want to go", "行きたい場所", [
        "Name a place you'd like to visit",
        "Say one reason why",
    ]),
    ("T08_TRAVEL", "A1", "Where is it?", "どこですか", [
        "Ask where a place is nearby",
        "Understand a simple direction",
    ]),
    ("T08_TRAVEL", "A2", "A short trip", "ちょっとした旅", [
        "Describe a short trip you took",
        "Ask basic travel questions",
    ]),
    ("T08_TRAVEL", "B1", "Trip stories", "旅の話", [
        "Tell a story from a memorable trip",
        "Recommend a place to a friend",
    ]),
    # T09 Festivals
    ("T09_FESTIVALS", "A1", "Special days", "とくべつな日", [
        "Name a holiday you celebrate",
        "Say what you usually do on that day",
    ]),
    ("T09_FESTIVALS", "A1", "What are you wearing?", "なにを着ていますか", [
        "Describe simple festival clothing and colors",
        "Say what you're wearing to a summer festival",
    ]),
    ("T09_FESTIVALS", "A2", "Festival foods", "お祭りの食べ物", [
        "Describe foods or treats at a festival",
        "Talk about wearing special clothes",
    ]),
    ("T09_FESTIVALS", "B1", "A favorite memory", "お祭りの思い出", [
        "Tell a story from a favorite festival or birthday",
        "Compare two different celebrations",
    ]),
    # T10 Anime / Pop Culture
    ("T10_ANIME", "A1", "Favorite character", "好きなキャラ", [
        "Name a favorite anime, manga, or game character",
        "Say what they look like in simple terms",
    ]),
    ("T10_ANIME", "A1", "It's so cool!", "すごいですね", [
        "Give a simple positive reaction",
        "Say what makes a character or story exciting",
    ]),
    ("T10_ANIME", "A2", "What I'm watching", "今見ているもの", [
        "Describe an anime or show you're following",
        "Recommend something to a friend",
    ]),
    ("T10_ANIME", "B1", "Story discussion", "物語について話す", [
        "Summarize a recent episode or chapter",
        "Share what you think will happen next",
    ]),
    # T11 Sports
    ("T11_SPORTS", "A1", "Sports I like", "好きなスポーツ", [
        "Say a sport you enjoy doing or watching",
        "Ask what sport someone else likes",
    ]),
    ("T11_SPORTS", "A1", "I played it yesterday", "きのうしました", [
        "Say an activity you did recently",
        "Ask someone what they did",
    ]),
    ("T11_SPORTS", "A2", "Playing together", "いっしょにやる", [
        "Suggest playing a sport together",
        "Talk about the last game you played or watched",
    ]),
    ("T11_SPORTS", "B1", "A team I follow", "応援するチーム", [
        "Describe a team or athlete you like",
        "Explain why a recent game was exciting",
    ]),
    # T12 Pets
    ("T12_PETS", "A1", "My pet", "ペット", [
        "Say what kind of pet you have or want",
        "Describe its color and size",
    ]),
    ("T12_PETS", "A1", "Please don't...", "しないでください", [
        "Give a simple gentle request or rule",
        "Understand a simple house rule",
    ]),
    ("T12_PETS", "A2", "Daily life with a pet", "ペットとの毎日", [
        "Talk about how you take care of a pet",
        "Describe something funny your pet does",
    ]),
    ("T12_PETS", "B1", "Animals around us", "身近な動物", [
        "Describe an animal you saw recently",
        "Compare two animals you know",
    ]),
]


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def _existing_codes(engine: Engine, table, column: str = "code") -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(select(getattr(table.c, column))).all()
    return {r[0] for r in rows}


def seed_curriculum(engine: Engine) -> dict[str, int]:
    """Insert any missing topics/lessons. Returns a count of inserts performed."""
    inserted = {"topics": 0, "lessons": 0}

    existing_topic_codes = _existing_codes(engine, topics_table)
    new_topics: Iterable[dict] = (t for t in TOPICS if t["code"] not in existing_topic_codes)
    new_topic_rows = list(new_topics)
    if new_topic_rows:
        with engine.begin() as conn:
            conn.execute(insert(topics_table), new_topic_rows)
        inserted["topics"] = len(new_topic_rows)

    # Re-fetch topic codes -> id mapping after potential inserts.
    with engine.connect() as conn:
        topic_rows = conn.execute(
            select(topics_table.c.id, topics_table.c.code)
        ).mappings().all()
    code_to_id = {r["code"]: r["id"] for r in topic_rows}

    existing_lesson_codes = _existing_codes(engine, lessons_table)

    # A (topic_code, level) pair may now have more than one lesson (e.g. two
    # A1 lessons per topic). The first occurrence keeps the original
    # `{topic}_{level}` code so existing rows/plans aren't orphaned; later
    # occurrences get a lettered suffix.
    level_occurrence: Counter[tuple[str, str]] = Counter()

    lesson_rows: list[dict] = []
    for sort_idx, (topic_code, level, title_en, title_ja, can_dos) in enumerate(LESSONS):
        key = (topic_code, level)
        level_occurrence[key] += 1
        occurrence = level_occurrence[key]
        suffix = "" if occurrence == 1 else chr(ord("a") + occurrence - 2)
        lesson_code = f"{topic_code}_{level}{suffix}"
        if lesson_code in existing_lesson_codes:
            continue
        lesson_rows.append(
            {
                "topic_id": code_to_id[topic_code],
                "code": lesson_code,
                "title_en": title_en,
                "title_ja": title_ja,
                "level": level,
                "can_dos_json": json.dumps(can_dos),
                "sort_order": sort_idx,
            }
        )
    if lesson_rows:
        with engine.begin() as conn:
            conn.execute(insert(lessons_table), lesson_rows)
        inserted["lessons"] = len(lesson_rows)

    # Keep titles/sort_order in sync when seed data is updated by future tasks.
    # This is a safe upsert because users don't edit topics/lessons directly.
    with engine.begin() as conn:
        for t in TOPICS:
            conn.execute(
                update(topics_table)
                .where(topics_table.c.code == t["code"])
                .values(
                    title_en=t["title_en"],
                    title_ja=t["title_ja"],
                    sort_order=t["sort_order"],
                )
            )

    return inserted
