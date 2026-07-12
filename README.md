# Japanese Conversation Practice App

A self-hosted AI-tutor web app to help our family practice Japanese
conversation. Voice-based, curriculum-driven, with a learning profile that
grows smarter over time.

See [`PLAN.md`](./PLAN.md) for the full design and task plan.

## Status

All 15 planned tasks are complete. The app is fully functional for
voice-based Japanese conversation practice with curriculum-driven sessions,
textbook image uploads, and personalized learning profiles.

- **Task 1 — done.** Project skeleton, dev Docker Compose, SQLite/WAL, `/api/healthz`.
- **Task 2 — done.** Profiles (no auth, kana-flash style), `/api/users` CRUD,
  `seed_users.py` CLI, `X-User-Id` header, profile picker, dashboard placeholder,
  settings page. Admin-guarded `/api/admin/whoami` proves the gate works.
- **Task 3 — done.** Pluggable LLM provider abstraction with the Anthropic Claude
  adapter as the default. `POST /api/chat` accepts a message history, builds a
  Misa/Hiro tutor system prompt, and returns the assistant's reply. New
  `/chat` route in the UI for a minimal text-chat practice loop. To talk to the
  real Claude API, copy `.env.example` → `.env` (or export `ANTHROPIC_API_KEY`)
  before starting the backend; otherwise the chat endpoint returns
  `503 Set ANTHROPIC_API_KEY`.
- **Task 4 — done.** Gemini / OpenAI / Bedrock LLM adapters added. Settings page
  has a "Your preferences" section to switch tutor voice (Misa/Hiro), LLM
  provider, and proficiency level per profile. Each provider raises a clear
  503/502 if its key isn't configured.
- **Task 5 — done.** Pluggable speech provider abstraction with Google Cloud
  STT + Neural2 TTS as the default. New `POST /api/voice/turn` endpoint runs
  STT → LLM → TTS and returns transcript, reply text, and an `audio_url` to
  stream the synthesized response. The Practice page gains a mic button that
  records via `MediaRecorder`, uploads, and auto-plays the tutor's reply.
  Misa → `ja-JP-Neural2-B` (female), Hiro → `ja-JP-Neural2-C` (male). Without
  Google credentials the endpoint returns `503` with a setup hint.
- **Task 6 — done.** OpenAI Whisper (STT) + OpenAI TTS adapter. Settings page
  has a Speech provider dropdown (Google Cloud / OpenAI). Misa→shimmer,
  Hiro→echo for OpenAI voices.
- **Task 7 — done.** Curriculum taxonomy seeded on startup: 12 kid-friendly
  topics × 3 lessons (A1/A2/B1) with original can-do statements. New
  `/api/curriculum/*` endpoints. Admin-only lesson plan editor with markdown
  textarea + live preview, save-as-draft / approve / revert workflow.
  Non-admins see read-only approved plans. New **Curriculum** page in the UI.
- **Task 8 — done.** Session orchestrator with persisted sessions and turns.
  Picks the next approved lesson, generates an opening greeting (with TTS),
  persists all turns server-side. Sessions survive page navigation.
- **Task 9 — done.** Session modes (freeform / 3-phase) and correction-style
  preferences (end-of-turn / end-of-session). End-of-session generates a
  wrap-up summary of corrections.
- **Task 10 — done.** Image upload for textbook-seeded sessions. All four LLM
  adapters support multimodal vision. The tutor quotes specific words/phrases
  from the uploaded page and builds practice around them.
- **Task 11 — done.** Learning profile capture: post-session LLM extraction of
  vocab, grammar, mistakes, and topics. Mastery tracking with dedup.
- **Task 12 — done.** Profile snapshot injected into the session prompt so the
  tutor naturally re-uses earlier vocab and revisits weak spots.
- **Task 13 — done.** Profile dashboard UI: vocab grid with mastery bars,
  grammar points, recent mistakes, topic interest chips.
- **Task 14 — done.** Admin family overview: read-only stats for all profiles.
- **Task 15 — done.** Production single-container Dockerfile, `deploy.sh` for
  Unraid NAS, Cloudflare Tunnel + Access documentation.

## Enabling voice (Google Cloud Speech)

1. In a Google Cloud project, enable **Cloud Speech-to-Text** and **Cloud
   Text-to-Speech**, then create a service account with the *Speech
   Administrator* (or scoped Speech-to-Text + Text-to-Speech User) role.
2. Download the service-account JSON key file. Save it somewhere safe like
   `~/secrets/japanese-study-gcloud.json`.
3. Export the path before starting the backend:

   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=$HOME/secrets/japanese-study-gcloud.json
   APP_DATA_DIR=../data uvicorn app.main:app --reload
   ```

   Or add `GOOGLE_APPLICATION_CREDENTIALS=...` to `.env` and `set -a; source ../.env; set +a` from the backend dir.

## Quick start (development)

You can run with Docker Compose **or** directly without Docker.

### Without Docker (recommended on a dev machine)

```bash
# one-time backend setup
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# one-time frontend setup
cd ../frontend
npm install
```

Run two terminals:

```bash
# terminal 1: backend
cd backend && source .venv/bin/activate
APP_DATA_DIR=../data uvicorn app.main:app --reload

# terminal 2: frontend
cd frontend && npm run dev
```

Then open http://localhost:5173. The Vite dev server proxies `/api/*` to
the backend on port 8000.

### With Docker

```bash
docker compose up --build
```

Same URLs as above.

## Seeding profiles

Profiles are stored in SQLite and managed via the `seed_users.py` CLI.

```bash
cd backend && source .venv/bin/activate

# from the repo root use APP_DATA_DIR=../data so the CLI writes to the
# same database the running app uses:
APP_DATA_DIR=../data python seed_users.py list
APP_DATA_DIR=../data python seed_users.py create "Mom" --admin
APP_DATA_DIR=../data python seed_users.py create "Kid1"
APP_DATA_DIR=../data python seed_users.py create "Kid2"
APP_DATA_DIR=../data python seed_users.py rename Kid1 "Sora"
APP_DATA_DIR=../data python seed_users.py set-admin Mom --on   # or --off
APP_DATA_DIR=../data python seed_users.py delete Kid2
```

Profiles can also be created/renamed/deleted from the **Settings** page
inside the app. The `is_admin` flag currently flips only via the CLI (it's
the bootstrap mechanism for designating the parent profile).

## Auth model

There is no password. The browser remembers the selected profile in
`localStorage` and sends it as `X-User-Id` on each API call. On a trusted
LAN this is fine. When the app is exposed remotely (Cloudflare Tunnel),
auth lives at the edge via Cloudflare Access.

## Running tests

```bash
# backend
cd backend && source .venv/bin/activate
pytest && ruff check app tests seed_users.py

# frontend
cd frontend
npm run test
npx tsc -b      # typecheck
```

## Production deployment (Unraid NAS)

### Prerequisites

- Unraid 7.2.2+ with Docker enabled
- SSH access to the NAS as root
- Git installed on the NAS (via NerdTools or similar)

### First-time setup on the NAS

```bash
# SSH into the NAS
ssh root@YOUR_NAS_IP

# Clone the repo
mkdir -p /mnt/user/appdata
cd /mnt/user/appdata
git clone https://github.com/namdle/japanese-study.git
cd japanese-study

# Create .env with your API keys
cp .env.example .env
nano .env   # fill in ANTHROPIC_API_KEY, GOOGLE_APPLICATION_CREDENTIALS, etc.

# If using Google Cloud Speech, copy the service account JSON:
mkdir -p /mnt/user/appdata/japanese-study/secrets
# (scp or copy your gcloud JSON key here)
# In .env, set: GOOGLE_APPLICATION_CREDENTIALS=/app/data/secrets/gcloud.json
# And mount it: add -v .../secrets:/app/data/secrets to the docker run command

# Build and run
docker build -t japanese-study .
docker run -d \
  --name japanese-study \
  --restart unless-stopped \
  -p 3001:8000 \
  -v /mnt/user/appdata/japanese-study/data:/app/data \
  --env-file /mnt/user/appdata/japanese-study/.env \
  japanese-study
```

The app is now at `http://YOUR_NAS_IP:3001` (host port 3001 matches the
Cloudflare tunnel route; 3000 is used by another app).

### Subsequent deploys (from your laptop)

```bash
# One-time: copy .deploy-config.example to .deploy-config and fill in values
cp .deploy-config.example .deploy-config

# Deploy (pushes to GitHub, pulls on NAS, rebuilds, restarts)
./deploy.sh
```

### Seed family profiles on the NAS

```bash
ssh root@YOUR_NAS_IP
docker exec -it japanese-study python seed_users.py create "Mom" --admin
docker exec -it japanese-study python seed_users.py create "Kid1"
docker exec -it japanese-study python seed_users.py create "Kid2"
docker exec -it japanese-study python seed_users.py list
```

## Enabling remote access (Cloudflare Tunnel + Access)

If you already run a locally-managed `cloudflared` tunnel on the NAS,
you can add this app as a new ingress rule — no new tunnel needed.

### Step 1 — Add an ingress rule on the NAS

SSH into the NAS and edit your cloudflared config (typically
`/root/.cloudflared/config.yml`). Add a new `hostname` block **before**
the catch-all `http_status:404` line:

```yaml
tunnel: <YOUR_TUNNEL_ID>
credentials-file: /etc/cloudflared/<YOUR_TUNNEL_ID>.json

ingress:
  # ... your existing rules ...
  - hostname: japanese.yourdomain.com    # ← add this
    service: http://localhost:3001
  - service: http_status:404
```

Then restart cloudflared to pick up the change:

```bash
docker restart cloudflared
```

### Step 2 — Add a DNS CNAME record in Cloudflare

In the Cloudflare dashboard → **your domain → DNS → Records**, add:

| Type  | Name       | Target                                    | Proxy |
|-------|------------|-------------------------------------------|-------|
| CNAME | `japanese` | `<YOUR_TUNNEL_ID>.cfargotunnel.com`       | ✅ On |

The app is now at `https://japanese.yourdomain.com`.

> **Why this matters for the mic:** Chrome only allows microphone access
> (`getUserMedia`) on secure contexts (HTTPS or localhost). Plain HTTP on
> the LAN will block the mic; the Cloudflare Tunnel provides HTTPS
> automatically.

### Step 3 — Enable Cloudflare Access (optional but recommended)

Gate the public URL so only family members can reach it:

- Cloudflare dashboard → **Zero Trust → Access → Applications → Add**
- Application URL: `https://japanese.yourdomain.com`
- Policy: **Allow**, rule type **Emails** — add each family member's address
- Family members get a one-time email code; the app itself stays password-free

This keeps the app simple while providing proper security for remote
access. The trust-based profile picker is fine because Cloudflare Access
already verified the person is a family member.

## Project layout

```
japanese-study/
├── PLAN.md             # full implementation plan (living doc)
├── docker-compose.yml  # dev: frontend + backend with hot reload
├── data/               # gitignored: sqlite db, audio, uploads
├── backend/
│   ├── app/
│   │   ├── api/        # FastAPI routers (users, admin, ...)
│   │   ├── schemas/    # Pydantic schemas
│   │   ├── db.py       # SQLAlchemy Core tables, init_db, WAL pragma
│   │   ├── deps.py     # current_user / require_admin
│   │   └── main.py
│   ├── seed_users.py   # profile management CLI
│   └── tests/
└── frontend/
    └── src/
        ├── api/        # client + typed API helpers
        ├── components/ # Header, ...
        ├── hooks/      # useProfile
        ├── routes/     # ProfilePicker, Dashboard, Settings
        └── App.tsx
```
