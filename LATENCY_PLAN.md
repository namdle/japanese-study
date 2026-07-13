# Voice-Turn Latency Reduction — Implementation Handoff

> **STATUS (2026-07-12, implemented locally, NOT yet deployed to the NAS):**
> Tasks 1–4 are done. Measured on a real session (Sonnet 5 + Google STT/TTS,
> same 4s recording): old `/turn-audio` returned after **5.7s**; the new
> `/turn-audio/stream` shows the transcript at **1.1s** and starts audio at
> **4.0s** (gap grows in the new path's favor on longer replies, since old =
> sum of all stages). Implementation notes:
> - Task 1: system-prompt tightened; in the streaming path the aid backfill
>   runs AFTER all audio events, so it's off the listening-latency path.
> - Task 2: `claude.py` sends `system` as a cache_control block and puts a
>   cache breakpoint on the last message (history prefix reuse). Existing
>   `system: str` callers unchanged.
> - Task 3: VAD poll 100→50ms + hysteresis hangover in `useMic.ts` (soft
>   trailing speech no longer starts the silence clock), making 1–2s
>   auto-stop settings safe. DB default kept at 2s (tests pin it).
> - Task 4: `stream_chat` on ClaudeProvider (optional capability via
>   getattr; other providers fall back to one chat() call but still get
>   chunked TTS). New SSE endpoint `POST /turn-audio/stream` (events:
>   transcript → text/audio per sentence → aids → done), sentence splitter
>   in `app/session/streaming.py`, streaming client + gapless audio queue
>   in the frontend. Old endpoint kept; client falls back on 404/405.
> - Google `streaming_synthesize` was NOT used (voice/codec restrictions);
>   per-sentence `synthesize` + MP3 concat matches the existing pattern.
> - Tests: backend 188 passing (was 176), frontend 31 passing.

> **Audience:** a capable coding agent (e.g. Fable) implementing this end to end.
> **Written by:** the prior Claude Code session, after diagnosing the pipeline and a
> separate profile-extraction bug. This file is self-contained — you should not need
> the chat history. Read it top to bottom before touching code.

---

## 0. TL;DR of the task

The app is a Japanese conversation-practice tutor. A **voice turn** currently takes
**~2–5 seconds** between the student finishing speaking and the tutor's audio reply
playing. The cause is architectural: the pipeline is **fully serial and non-streaming**,
and it hops across two different vendors (speech provider + LLM provider).

Your job: cut that latency. There is a prioritized plan in **§6**. Do the cheap, safe
wins first (they keep every existing feature), then the structural streaming refactor.
**Measure before and after** (§8) — optimize against real numbers, not estimates.

**Hard constraint:** the app's beginner features — furigana (hiragana) + English reading
aids under each reply, and speech-to-text biasing toward the learner's name and lesson
vocab — are *text-pipeline* features. Do not delete them to gain speed. A pure
speech-to-speech realtime model (§5, option 6) would sacrifice them, so that path is an
*optional future toggle*, not the default.

---

## 1. Product context

- **What it is:** a family Japanese-practice web app. One parent admin ("Nam") plus two
  teenagers ("Khoi", "Mia") preparing for exchange homestays in Japan during Obon.
- **How a session works:** the student picks an approved lesson, then converses turn by
  turn with an AI tutor. Each turn the student either types or **speaks**; the tutor
  replies in Japanese with optional hiragana + English aids, and (for voice) speaks the
  reply aloud.
- **Deployment:** single Docker container on an Unraid NAS, fronted by a Cloudflare
  Tunnel at `https://japanese.miale13.com` (HTTPS is required for mic access). See §7.

---

## 2. Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy Core, SQLite (WAL, `foreign_keys=ON`),
  Pydantic. LLM SDK: **`anthropic==0.49.0`** (pinned in `backend/pyproject.toml` — this
  predates `thinking`/`output_config`; **it DOES support streaming and prompt caching**,
  see §5).
- **Frontend:** React + TypeScript + Vite, react-router-dom, react-markdown, Vitest +
  jsdom, ESLint.
- **LLM providers** (`backend/app/llm/`): `claude` (default, Anthropic), `bedrock`,
  `openai`, `gemini`. Selected per-user. Default model is **`claude-sonnet-5`**.
- **Speech providers** (`backend/app/speech/`): `gcloud` (Google STT/TTS, default) and
  `openai` (Whisper + TTS). Selected per-user.

---

## 3. Repo map (the files that matter here)

```
backend/
  app/
    api/sessions.py         ← THE voice turn lives here (voice_turn ~L946). Also text_turn,
                              session start/openers, end_session, _tutor_reply,
                              _ensure_reading_aids, _session_phrase_hints, _append_turn.
    llm/
      base.py               ← LLMProvider Protocol; Message, ChatResponse dataclasses;
                              build_tutor_system_prompt(); parse_tutor_reply()/ParsedReply.
      claude.py             ← Anthropic adapter. chat() calls messages.create (NON-streaming).
      bedrock.py openai_provider.py gemini.py  ← other adapters, same chat() signature.
      router.py             ← get_provider_for_user() factory.
    speech/
      base.py               ← SpeechProvider Protocol; SynthesizedAudio; TutorVoice.
      gcloud.py             ← Google STT (speech adaptation / phrase hints) + TTS.
      openai_speech.py      ← Whisper + OpenAI TTS.
      router.py hints.py
    profile/extract.py      ← post-session vocab/grammar/mistakes extraction (NOT latency-
                              critical; runs on end_session). Recently fixed, see §9.
  tests/                    ← pytest. Fake LLM/speech stubs must match provider signatures.
frontend/
  src/
    api/sessions.ts         ← postVoiceTurn() uploads a Blob to /api/sessions/{id}/turn-audio,
                              awaits the FULL SessionDetail JSON, returns it.
    routes/Chat.tsx         ← records via useMic, calls postVoiceTurn, then plays the last
                              turn's audio_url through a single <audio> element (audioRef).
    hooks/useMic.ts         ← MediaRecorder + Web-Audio VAD "Auto-stop after silence".
```

---

## 4. The current voice-turn pipeline (this is the problem)

**Endpoint:** `POST /api/sessions/{id}/turn-audio` → `voice_turn()` in
`backend/app/api/sessions.py` (~L946). It is a **synchronous `def`** (not `async`), and
every stage blocks on the *complete* output of the previous one:

```
student stops speaking
 └─(client) useMic VAD waits `auto_stop_seconds` of silence   ← 1–10s SETTING, perceived latency
 └─(client) upload full webm blob  → POST /turn-audio (multipart)
 └─(server) speech.transcribe(audio, phrase_hints, strong_hints)   ~0.5–1.5s  (STT round trip)
 └─(server) _append_turn(role="user", transcript)
 └─(server) _tutor_reply(llm, history, system_prompt)              ~1–3s      (llm.chat, waits for LAST token)
 └─(server) _ensure_reading_aids(...)  ← SOMETIMES a 2ND full llm.chat  +1–2s when it fires
 └─(server) speech.synthesize(reply_text, voice)                   ~0.5–1.5s  (produces WHOLE mp3 before returning)
 └─(server) write mp3 to data/audio/<uuid>.mp3, _append_turn(assistant, audio_path)
 └─(server) return SessionDetailOut (single JSON)
 └─(client) find last turn with audio_url, set audioRef.src, .play()   (downloads then plays)
```

The wall-clock cost is the **sum** of every hop. Three specific aggravators:

1. **Non-streaming LLM.** `ClaudeProvider.chat()` (`app/llm/claude.py`) calls
   `messages.create(...)` and blocks until the final token. TTS can't begin until the
   entire reply exists.
2. **Non-streaming TTS.** `speech.synthesize()` returns the *entire* `SynthesizedAudio`
   (full mp3 bytes) before the client gets anything. Playback can't start on the first
   syllable.
3. **A hidden second LLM call.** `_ensure_reading_aids()` (`app/api/sessions.py` ~L397)
   fires a *whole extra* `llm.chat()` round trip whenever the tutor drops the
   `[HIRAGANA]`/`[EN]` markers — on the critical path, on every turn it triggers.

---

## 5. How snappy voice apps avoid this (the target design)

Two techniques; good products use both:

**A. Pipelining — overlap stages instead of stacking them.**
Stream the LLM token-by-token; the moment the *first sentence* is complete, start TTS on
that sentence while the LLM keeps writing later sentences; play sentence 1's audio while
sentence 2 synthesizes. Stream STT while the user talks (endpointing), so the transcript
is ready the instant they stop. Wall-clock collapses toward *the slowest single stage*
instead of the *sum*.

**B. Realtime speech-to-speech models** (OpenAI Realtime API, Gemini Live).
One model over a persistent WebSocket: audio in → audio out, no intermediate text, no
cross-vendor handoff. Sub-second, but it doesn't natively produce our furigana/English
aids or do STT phrase biasing — so it's an *optional mode*, not a drop-in.

**What `anthropic==0.49.0` already gives you (no upgrade needed):**
- **Streaming:** `client.messages.stream(...)` (context manager yielding text deltas).
- **Prompt caching:** pass `system` as a list of blocks and mark the large, stable prefix
  with `"cache_control": {"type": "ephemeral"}`; likewise cacheable message blocks. This
  cuts *time-to-first-token*, which matters most for perceived snappiness.
- Verify Google TTS streaming (`streaming_synthesize`) availability in the installed
  `google-cloud-texttospeech` before relying on it; otherwise chunk TTS at the sentence
  level with ordinary `synthesize` calls.

---

## 6. Prioritized implementation plan

Do these in order. **1–3 are cheap, safe, and keep every feature.** 4 is the real
structural fix. 5–6 are optional/future.

### ✅ Task 1 — Remove the second LLM call from the hot path (`_ensure_reading_aids`)
- **Where:** `backend/app/api/sessions.py` — `voice_turn` (~L990), `text_turn` (~L928),
  and openers (~L678, ~L843) all call `_ensure_reading_aids()` after `parse_tutor_reply`.
- **Problem:** when the tutor omits the `[HIRAGANA]`/`[EN]` markers, this makes a full
  extra `llm.chat()` synchronously — a 1–2s spike.
- **Approach (pick one, in preference order):**
  1. **Make the primary reply reliable enough that the backfill rarely fires** — tighten
     the instruction in `build_tutor_system_prompt` (`app/llm/base.py`) and/or add the
     markers via prompt-cached few-shot so the model stops dropping them. Then the
     backfill stays only as a rare safety net.
  2. **Move the backfill OFF the critical path:** return/stream the reply immediately,
     and compute the aids asynchronously, delivering them in a follow-up (a second SSE
     event, or a tiny `PATCH`/poll that fills `hiragana`/`english` on the turn). The
     student hears audio without waiting on furigana, which they read afterward.
  3. **Generate hiragana locally** (no LLM) with a kana-conversion library
     (e.g. a kakasi/pykakasi-style dependency) for the `[HIRAGANA]` case; keep the LLM
     only for English. Weigh the dependency cost.
- **Acceptance:** no turn makes more than one LLM call on the critical path; furigana +
  English still appear under replies when the user has them enabled.

### ✅ Task 2 — Prompt caching on the Claude call
- **Where:** `backend/app/llm/claude.py::chat()`. The system prompt is large (built by
  `build_tutor_system_prompt` — includes lesson plan markdown + the learner's profile
  snapshot) and stable across a session.
- **Approach:** send `system` as a list of one or more blocks, marking the large stable
  prefix with `"cache_control": {"type": "ephemeral"}`. Optionally cache the long history
  prefix too. Keep the current string-`system` behavior working for callers that pass a
  plain string (accept both).
- **Acceptance:** measurable drop in time-to-first-token on the 2nd+ turn of a session;
  all existing tests still pass; no behavior change in reply content.

### ✅ Task 3 — Tune the client-side silence wait (perceived latency)
- **Where:** `frontend/src/hooks/useMic.ts` (VAD "Auto-stop after silence"), the
  `auto_stop_seconds` user setting (`frontend/src/routes/Settings.tsx`, backend
  `users.auto_stop_seconds`).
- **Problem:** part of the 2–5s is literally the app waiting N seconds of silence after
  the student finishes before it even sends anything.
- **Approach:** lower the sensible default and/or make endpointing snappier (shorter
  trailing-silence threshold once speech has clearly ended). Keep it user-tunable; don't
  make it so aggressive it clips the student mid-sentence. Consider a shorter default
  (e.g. 1–1.5s) with the existing 1–10s options retained.
- **Acceptance:** the gap between "student done" and "request sent" shrinks without
  truncating normal speech.

### ⭐ Task 4 — Stream LLM → chunked TTS (the structural fix)
This is where the big win is. Pipeline the two slowest stages.
- **Backend:**
  - Add a **streaming** path to the LLM adapter (at least Claude): use
    `client.messages.stream(...)` to yield text deltas. Extend the `LLMProvider`
    Protocol with a streaming method (e.g. `stream_chat(...) -> Iterator[str]`) and
    implement for Claude first; other providers can fall back to non-streaming.
  - As tokens arrive, **segment on sentence boundaries** (Japanese: 。！？ plus newline),
    and for each completed sentence call `speech.synthesize()` (or a streaming TTS if
    available — see §5) so audio for sentence 1 is ready while sentence 2 generates.
  - Change the endpoint (or add a new one, e.g. `POST /api/sessions/{id}/turn-audio/stream`)
    to **stream** to the client rather than returning one JSON blob. Recommended:
    **SSE / chunked HTTP** emitting events like `text` (incremental reply for display),
    `audio` (base64 or a URL per synthesized chunk, in order), and a final `done` event
    carrying the persisted `SessionDetail` (so the client's turn list stays correct).
  - Persist the full assistant turn (concatenated text + a combined or first audio path)
    exactly as today at the end, so history/replay is unaffected.
  - **Make the handler `async def`** so streaming doesn't block the worker.
- **Frontend:**
  - Update `frontend/src/api/sessions.ts::postVoiceTurn` (or add `postVoiceTurnStream`)
    to consume the stream.
  - Play audio chunks **gaplessly in order** — either a `MediaSource`/SourceBuffer queue,
    or a small queue of sequential `Audio` elements that start the next on `ended`.
    Start playback as soon as the first chunk arrives.
  - Render the incremental `text` as it streams; reconcile with `SessionDetail` on `done`.
- **Acceptance:** first audio starts playing well before the full reply is generated;
  perceived latency for multi-sentence replies is roughly halved; furigana/English and
  the persisted transcript are unchanged; manual and auto-stop recording both still work.

### Task 5 (optional) — Streaming STT with endpointing
- Stream mic audio to the STT continuously with server-side endpointing so the transcript
  is ready the instant the student stops, removing the discrete upload+transcribe leg.
  Google/OpenAI both offer streaming recognition. Higher effort; do after Task 4.

### Task 6 (optional/future) — Realtime speech-to-speech "fast mode"
- A separate toggle using OpenAI Realtime or Gemini Live (audio-in/audio-out over a
  WebSocket). Sub-second, but **loses furigana/English aids and STT phrase-biasing** unless
  re-engineered. Ship it as an opt-in mode alongside the current pedagogical pipeline, not
  as a replacement.

---

## 7. Deploy process (how prod gets updated)

Established this session — **git-pull based**, deployed to the Unraid NAS:

1. Commit locally. **Push over SSH** (the HTTPS remote hangs on credentials in this env):
   `git push git@github.com:namdle/japanese-study.git main`
2. On the NAS: pull, rebuild, zero-downtime container swap. Effective command:
   ```
   ssh root@lenas.local 'cd /mnt/user/appdata/japanese-study && git pull && \
     docker build -t japanese-study . && \
     docker stop japanese-study; docker rm japanese-study; \
     docker run -d --name japanese-study --restart unless-stopped \
       -p 3001:8000 -v /mnt/user/appdata/japanese-study/data:/app/data \
       --env-file /mnt/user/appdata/japanese-study/.env japanese-study'
   ```
   There is also a `deploy.sh` at the repo root (reads `.deploy-config`).
- **Ports:** host **3001** → container 8000. **Do not use 3000** — that belongs to a
  different app (kana-flash) on the same NAS. The Cloudflare tunnel routes
  `japanese.miale13.com → localhost:3001`.
- **Deploying to production is a user-gated action** — build & confirm, but get explicit
  approval from the user before swapping the live container.

---

## 8. Measuring latency (do this first and after each task)

Before optimizing, instrument the real per-stage cost so you target the right stage:
- Add temporary timing around each stage in `voice_turn` (STT, LLM, reading-aid backfill,
  TTS) and log the deltas, OR run a one-off script that replays a real session's audio +
  history against the live providers and prints each stage's wall-clock time.
- Capture a baseline on a representative turn (a full multi-sentence tutor reply), then
  re-measure after Tasks 1–2 and again after Task 4. Report the numbers.
- Perceived latency = server pipeline **+** client silence-wait (Task 3) **+** audio
  download/first-play. Measure the end-to-end (mic-stop → first audio) too, not just
  server time.

---

## 9. Critical constraints & gotchas (discovered this session — respect these)

**Claude Sonnet 5 (`claude-sonnet-5`) quirks — these already bit us:**
- It **rejects a non-default `temperature`** (HTTP 400). `ClaudeProvider.chat()` does NOT
  send `temperature` to the Anthropic API for this reason, even though the shared
  interface accepts one. Keep it that way when you refactor.
- It **rejects assistant-message prefill** (400: "does not support assistant message
  prefill. The conversation must end with a user message"). `_tutor_reply()` guards this
  by dropping any trailing assistant turns. Preserve that guard in the streaming path.
- It **occasionally returns an empty message.** `_tutor_reply()` retries once, then falls
  back to a gentle Japanese prompt (`_FALLBACK_REPLY`) rather than saving a "…" turn that
  looks stuck. Preserve this in streaming (empty stream → retry/fallback).

**Token budget / truncation (a bug we just fixed — don't reintroduce):**
- `chat()` now accepts an optional per-call `max_tokens` (default falls back to the
  provider default of 1024). Post-session extraction was silently truncating its
  Japanese-heavy JSON at 1024 tokens and failing to parse; it now requests 4096.
  For the tutor reply, keep enough budget that a full spoken reply isn't clipped.

**Reading-aid marker format (don't break the parser):**
- The tutor appends `[HIRAGANA] ...` and `[EN] ...` marker lines; `parse_tutor_reply()`
  (`app/llm/base.py`) splits the Japanese body from these aids into a `ParsedReply`
  (`text`, `hiragana`, `english`). Only `text` is sent to TTS (`ja_for_tts`). Any
  streaming/segmentation must not feed the marker lines into TTS.

**STT biasing (a feature, not incidental):**
- `_session_phrase_hints()` returns `(strong_hints, phrase_hints)`: the learner's name
  (`users.name_ja`) at max boost, and lesson vocab at a lower boost. `gcloud.transcribe`
  puts the name in its own high-boost `SpeechContext`. This fixed the tutor mishearing a
  child's name. Any STT streaming rework must carry these hints through.
- Also: the tutor is instructed to **never correct the learner's own name pronunciation**
  (in `build_tutor_system_prompt`) — keep that instruction.

**Testing:**
- Backend: `cd backend && .venv/bin/python -m pytest -q` (currently **176 passing**).
  Fake LLM/speech stubs in `backend/tests/*` must match the provider signatures — when you
  extend `LLMProvider.chat`/add a streaming method, update every fake `chat`/stream stub
  (grep `def chat` and `class .*Provider`/`FakeLLM` under `tests/`) or tests fail with
  "unexpected keyword argument".
- Frontend: `cd frontend && npm run build` (typecheck) and `npm test`. Note: on **Node 25**
  there's an in-memory `localStorage` polyfill in `src/test/setup.ts` (Node 25 ships a
  stub global `localStorage` without `setItem` that shadows jsdom's) — keep it.

**SQLite:** WAL mode, `foreign_keys=ON`. Additive schema changes go through the additive
migration path in `app/db.py`. The prod DB lives at the mounted `data/` volume — never
wipe it on deploy.

---

## 10. Suggested sequencing for the implementer

1. Instrument & capture a baseline (§8).
2. Task 1 (kill the hot-path second LLM call) + Task 2 (prompt caching) + Task 3 (silence
   tuning). Re-measure. These are low-risk and keep all features — ship them together.
3. Task 4 (streaming LLM → chunked TTS + async endpoint + streaming client playback).
   Re-measure. This is the structural win.
4. Consider Tasks 5–6 only if still not snappy enough, mindful of the feature trade-off.

Report actual before/after numbers with each step.
