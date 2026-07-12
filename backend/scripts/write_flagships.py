"""Write the three hand-tuned flagship A1 lesson plans as drafts.

These are authored by hand (not the LLM) as quality anchors, so the batch
generator must NOT regenerate them. Run this AFTER a regeneration pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_lesson_plans import admin_user_id, write_draft  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import get_engine, init_db, lessons_table  # noqa: E402

FLAGSHIPS: dict[str, str] = {
    "T01_GREETINGS_A1a": """\
## Scenario
You've just arrived at your host family's home during Obon and are standing at
the entrance (玄関), meeting everyone for the first time — the host parents and
their kids, who are about your age. This first moment is about greeting warmly,
introducing yourself simply, and thanking them for having you.

## Target vocabulary
- **はじめまして** (hajimemashite) — Nice to meet you (first meeting)
- **どうぞよろしくおねがいします** (douzo yoroshiku onegai shimasu) — Please treat me kindly
- **おせわになります** (osewa ni narimasu) — Thank you for having me / I'll be in your care
- **なまえ** (名前 / namae) — name
- **わたし** (私 / watashi) — I / me
- **〜さい** (〜歳 / sai) — ... years old
- **〜から来ました** (kara kimashita) — I came from ...
- **つかれました** (疲れました / tsukaremashita) — I'm tired (from the trip)
- **よろしく** (yoroshiku) — (casual) nice to meet you, to the host kids
- **ありがとうございます** (arigatou gozaimasu) — thank you (polite)

## Key sentence patterns
- **はじめまして。わたしは___です。** — "Nice to meet you. I'm ___."
- **___から来ました。** — "I came from ___."
- **___さいです。** — "I'm ___ years old."
- **おせわになります。** — the standard arrival phrase to the host parents.

## Register — who you're talking to
- To the **host parents**: stay polite — です/ます, おせわになります,
  どうぞよろしくおねがいします, and a small bow.
- To the **same-age host kids**: after the polite group hello, it's natural to
  loosen up — よろしく！, plain です is fine, smiles over formality.

## Example exchange
「Tutor (host parent):」 いらっしゃい！　長い旅、おつかれさま。
「Learner:」 はじめまして。わたしは___です。___から来ました。おせわになります。
「Tutor (host parent):」 ていねいにありがとう。こちらこそよろしくね。
「Tutor (host kid):」 やあ！わたしは___。よろしく！
「Learner:」 よろしく！

## Tutor notes
- Expect は pronounced "wa," and dropped です — model the full polite form back
  rather than explaining. If they manage はじめまして + name, that's a win for turn one.
- Elicit both goals by role-playing two people: first the host parent (draw out
  おせわになります + polite intro), then a same-age host kid (let them drop to
  casual よろしく). Naming that switch out loud once helps it stick.
- Cultural note: おせわになります is the natural arrival phrase — more fitting
  than a plain ありがとう — and a light bow with it is appreciated. Weave in the
  learner's real name and home country.
""",
    "T06_FOOD_A1a": """\
## Scenario
It's your first big family dinner during Obon. Everyone is sharing dishes around
the table and the host parents keep offering you more. You want to say which
foods you like — and, importantly, politely explain anything you can't eat.

## Target vocabulary
- **ハンバーガー** (hanbāgā) — burger
- **ピザ** (piza) — pizza
- **サンドイッチ** (sandoicchi) — sandwich
- **パスタ** (pasuta) — pasta
- **とんかつ** (tonkatsu) — breaded pork cutlet
- **からあげ** (karaage) — Japanese fried chicken
- **ラーメン** (rāmen) — ramen
- **ピーナッツ** (pīnattsu) — peanut
- **アレルギー** (arerugī) — allergy
- **すき** (suki) — like
- **おいしい** (oishii) — delicious
- **すみません** (sumimasen) — excuse me / sorry

## Key sentence patterns
- **〜が好きです** — "I like ~." (からあげが好きです。)
- **〜は食べられません** — "I can't eat ~." (ピーナッツは食べられません。)
- **〜アレルギーがあります** — "I have a ~ allergy." (ピーナッツアレルギーがあります。)
- **すみません、ちょっと…** — soft, polite lead-in for declining.

## Register — who you're talking to
- To the **host parents** offering food: polite — すみません, 〜は食べられません,
  and いただきます before eating.
- With the **host kids**: casual is fine — おいしい！, これすき！, ちょっとむり〜.

## Example exchange
「Tutor:」 どうぞ、たくさん食べてね。からあげは好き？
「Learner:」 はい、からあげが好きです。おいしいです。
「Tutor:」 よかった！じゃあ、このクッキーもどうぞ。
「Learner:」 すみません、ピーナッツは食べられません。ピーナッツアレルギーがあります。
「Tutor:」 そうか、教えてくれてありがとう。じゃあ、ラーメンはどう？

## Tutor notes
- Expect は/が mixups or dropped particles — accept the meaning first, then model
  once. If 食べられません is too hard, ちょっと… + a head-shake is a valid polite fallback.
- Elicit both goals by playing host: offer several dishes (mix Western and Japanese)
  so they must express a like AND decline one politely. **A peanut allergy is serious —
  drill ピーナッツアレルギーがあります firmly and clearly.** If the learner has a
  different restriction use their real one; if none, have them decline a food they dislike.
- Cultural note: say いただきます before eating, and give a *reason* (an allergy) rather
  than just refusing — hosts appreciate knowing so they can adjust the next meal.
""",
    "T09_FESTIVALS_A1": """\
## Scenario
It's your first evening with the host family during Obon, and everyone is talking
about the holiday over dinner. Your host siblings ask what special days you
celebrate back home, and you all compare how you spend them.

## Target vocabulary
- **お盆** (obon) — Obon (summer holiday honoring ancestors)
- **お祭り** (omatsuri) — festival
- **盆踊り** (bon-odori) — Obon dance
- **花火** (hanabi) — fireworks
- **お墓参り** (ohaka-mairi) — visiting family graves
- **浴衣** (yukata) — light summer kimono
- **夏** (natsu) — summer
- **家族** (kazoku) — family
- **お祝い** (oiwai) — celebration
- **休み** (yasumi) — holiday, day off
- **する / します** (suru / shimasu) — to do
- **いつも** (itsumo) — usually, always

## Key sentence patterns
- **〜をします。** — "I do ~." (盆踊りをします。)
- **〜に、〜をします。** — "On ~, I do ~." (お盆に、お墓参りをします。)
- **いつも〜をします。** — "I usually do ~."
- **〜は、なにをしますか。** — "What do you do on ~?"

## Register — who you're talking to
- With the **host kids** comparing holidays: casual is natural — 〜するよ、なにするの？
- If a **host parent** joins in or explains a custom: answer politely with です/ます.

## Example exchange
「Tutor (host kid):」 もうすぐお盆だね。お盆に、なにをするの？
「Learner:」 えっと…盆踊りをします。
「Tutor:」 いいね！花火も見るよ。___の国では、なつに、お祝いをする？
「Learner:」 はい。なつに、パーティーをします。いつも家族とします。
「Tutor:」 すてきだね。家族といっしょ、いいね。

## Tutor notes
- Expect dropped を or に (「お盆、盆踊りします」) — repeat the full pattern back
  rather than correcting head-on; praise any successful を/に.
- Elicit both goals: ask なにをしますか first (what), then いつしますか (when).
  Let them name their own home holiday if they don't celebrate Obon.
- Cultural aside: during Obon many families do お墓参り to welcome ancestors home,
  so it's a quieter, family-centered holiday as much as a festive one.
""",
}


def main() -> None:
    engine = get_engine()
    init_db(engine)
    admin_id = admin_user_id(engine)
    with engine.connect() as conn:
        code_to_id = {
            r.code: r.id
            for r in conn.execute(
                select(lessons_table.c.id, lessons_table.c.code)
            )
        }
    for code, markdown in FLAGSHIPS.items():
        lesson_id = code_to_id.get(code)
        if lesson_id is None:
            print(f"WARNING: lesson {code} not found, skipping")
            continue
        write_draft(engine, lesson_id, markdown.strip(), admin_id)
        print(f"wrote flagship {code}")
    print("done.")


if __name__ == "__main__":
    main()
