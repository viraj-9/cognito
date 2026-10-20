# Cognito

Cognito is a multilingual language-learning companion. Learners can search a curated lesson library, practice short questions, save their progress, rate recommendations, and chat with an optional AI tutor. The existing premium visual style is retained; internal curriculum alignment is kept out of the interface and public API.

## Run on Windows

Python 3.10 or newer is required. From PowerShell in the project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
if (-not (Test-Path .env)) { Copy-Item .env.example .env } # create only when .env does not already exist
python -m pip install -r requirements.txt
python -m app.init_db
python -m app.train
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The database is created at `app/data/linguaai.sqlite3`; the trained language model is saved at `app/model_artifacts/language_model.json`. The server also initializes the database at startup. API documentation is available at <http://127.0.0.1:8000/api/docs> in development.

If PowerShell blocks virtual-environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell window, then activate again. Stop the server with **Ctrl+C**.

## Optional live AI tutoring

Lesson search and practice work without external credentials, but natural open-ended chat requires a configured AI provider. This project defaults to NVIDIA Nemotron 3.5 Lightning. Generate a key from [NVIDIA's model page](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b), place it in `.env`, and fully restart Uvicorn:

```dotenv
LINGUAAI_AI_BASE_URL=https://integrate.api.nvidia.com/v1
LINGUAAI_AI_API_KEY=your-new-nvidia-api-key
LINGUAAI_AI_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

The NVIDIA adapter explicitly requests non-streaming JSON, disables the model's internal thinking for short conversational replies, and polls NVIDIA's status endpoint when an inference request returns HTTP 202. The key is used only by the backend and is never returned to the browser. Provider timeouts, authentication errors, rate limits, malformed responses, and outages are reported clearly. Temporary network and provider errors (including HTTP 429 and common 5xx responses) are retried with a short backoff, and upstream diagnostic text is sanitized before display. A configured provider failure does not pretend that a local reply came from the provider. Password reset and email verification are not enabled because no email delivery service is configured.

## Conversation practice

Sign in, choose a mode (casual conversation, English practice, interview, education-based mock interview, or role-play), choose a level, and press **Start practice**. Interview mode accepts a target role; role-play accepts a scenario. For **Mock interview (by education)**, select your education level first, then your field of study, and choose one of the matching roles. The interviewer then asks one role-appropriate question at a time and uses your answers for contextual follow-ups. **Stop & Generate Report** completes the session and creates a performance report. Feedback is transcript-based: it does not measure pronunciation, accent, vocal confidence, pauses, or speaking speed.

Saved conversation transcripts are private to the signed-in account. The app stores at most 40 messages per session, sends up to the latest 16 messages per turn to the provider, and lists the latest 30 sessions. Use **Clear history** to delete the account's saved sessions. The normal one-off assistant keeps recent conversation context in the open browser session and does not save message text to the account. Voice input uses browser speech recognition; during voice practice, each recognized answer is submitted automatically after the interviewer finishes. Browser speech playback reads the latest assistant response aloud. Use **Voice speed** to choose Slow, Medium, or Fast; this preference applies to assistant playback and is remembered in the browser. Cognito does not upload or store raw microphone recordings; the browser speech-recognition service may process audio separately according to its own behavior and settings. When voice practice is used and enough signal is available, the report can include approximate pace, pause balance, pitch-variation and volume-steadiness metrics; only those aggregated summaries and the transcript are sent by Cognito with the report. Confidence is a coaching estimate, not a psychological assessment. Browser voice APIs and available high-quality voices vary by browser and operating system; localhost is allowed for microphone access.

With no AI provider configured or when the provider is unavailable, saved conversation practice uses a clearly labelled offline question flow and transcript-based report. The offline mock-interview questions are selected by role, but they are preset rather than dynamically generated and the report is not an AI assessment. Standalone open-ended tutoring needs a working AI provider. Lesson search and short-answer practice continue to work offline.

## Configuration

Copy `.env.example` to `.env`. Environment variables override values in that file.

| Variable | Purpose |
| --- | --- |
| `LINGUAAI_ENV` | Set `production` to enable secure cookies and disable development API docs. |
| `LINGUAAI_SESSION_SECRET` | HMAC pepper for session-token hashes. Use at least 32 random characters in production. Changing it invalidates existing sessions. |
| `LINGUAAI_DATABASE` | SQLite file path. Defaults to `app/data/linguaai.sqlite3`. |
| `LINGUAAI_LANGUAGE_MODEL` | Trained model artifact path. Defaults to `app/model_artifacts/language_model.json`. |
| `LINGUAAI_AI_BASE_URL` | Optional compatible provider base URL. |
| `LINGUAAI_AI_API_KEY` | Optional server-side provider key. |
| `LINGUAAI_AI_MODEL` | Model name accepted by the configured provider. |

Before deploying, generate a private session secret and set it in the process environment or `.env`:

```powershell
[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

Do not commit `.env`, database files, or credentials. HTTPS is required in production because the session cookie is marked `Secure` there. The app does not enable cross-origin API access.

## Learning data and model workflow

- `app/data/resources.json` is a small, hand-authored English lesson catalogue with prerequisite relationships and answer keys held server-side.
- `app/data/language_samples.csv` contains synthetic development phrases for four languages. Its provenance is labeled in the dataset and its small held-out scores are not evidence of production language-detection accuracy.
- `python -m app.train` validates the sample labels and duplicates, creates reproducible stratified train/validation/test splits, fits a small one-hidden-layer language classifier and a Naive Bayes baseline, evaluates precision/recall/F1 and confusion matrices, and writes a versioned JSON artifact. Features are created from the training split only.
- The running app loads the trained artifact and uses it for language identification, with a Unicode-script shortcut for Devanagari. If the artifact is missing, the assistant reports an unknown language rather than claiming a prediction.
- Resource recommendations use prerequisite completion and explicit helpful/not-for-me feedback. Ratings are attached to the signed-in learner; the bounded contextual policy only changes suggestions and does not explore during lessons.
- We do not train on conversations or store chat messages. Assessment scores and recommendation ratings are stored only for signed-in users. Anonymous users can still use the library and practice, but their progress is not persisted.
- No regression model is shipped: the project has no credible labeled numeric learner dataset from which to predict proficiency or completion time. A small synthetic sample is not a responsible substitute.

## Data, sessions, and privacy

SQLite schema migrations `001_initial` and `002_conversations` are applied by `python -m app.init_db` or automatically at app startup. User records, password hashes, opaque sessions, progress, assessment scores, recommendation feedback, rate-limit buckets, and signed-in conversation transcripts are stored locally. Passwords are hashed with PBKDF2-HMAC-SHA256. Session cookies are HTTP-only and SameSite Strict; mutating requests require a session-bound CSRF header. Login, registration, chat, conversation, and assessment endpoints are throttled. The app does not store submitted lesson answers.

Academic alignment is maintained only in `app/internal/curriculum_map.py` for maintainers. It is not imported by the API routers, serialized in public responses, or served as a static asset.

## API overview

- `GET /api/health`, `GET /api/ready` — service and database health
- `GET /api/session`, `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout` — session and accounts
- `GET /api/profile`, `PUT /api/profile` — signed-in learner profile
- `POST /api/chat` — provider-backed natural chat with recent transcript context; requires AI provider settings
- `GET/POST /api/conversations`, `GET /api/conversations/{id}`, `POST /api/conversations/{id}/messages`, `POST /api/conversations/{id}/end`, `DELETE /api/conversations` — account-owned practice history and feedback
- `GET /api/library`, `GET /api/library/search`, `GET /api/library/{id}` — lessons and search
- `GET /api/paths` — a learner-facing sequence of prerequisite guides
- `GET /api/recommendations` — progress and rating-aware lesson suggestions
- `POST /api/library/{id}/assessment`, `POST /api/library/{id}/feedback`, `GET /api/progress` — practice and learner history

All state-changing requests require the CSRF token returned by `GET /api/session` in the `X-CSRF-Token` header. Public responses contain lesson and product data only; they do not expose the internal curriculum map or model diagnostics.

## Tests and checks

```powershell
python -m unittest discover -v
python -m app.train
```

With the server running in another PowerShell window, exercise the account, learning, and conversation configuration API flow:

```powershell
python tests/smoke_api.py
```

No JavaScript package manager or frontend build is needed; the browser interface is served directly by FastAPI. The small development dataset and static UI are not a production-ready translation model or a substitute for a larger, independently sourced evaluation corpus.

## Voice mock interview and fresh lesson practice

- Choose Casual conversation, English practice, Interview, HR interview, Technical interview, **Mock interview (by education)**, or Role-play, then click **Start practice**. For Mock interview, complete the education, field-of-study, and role selectors first.
- Keep **Voice practice** checked to have the interviewer speak each prompt aloud, listen for your spoken reply, send the transcript, and speak the next response automatically. Uncheck it to use the same session as text practice.
- Click **Stop & Generate Report** to finish and open a report with an overall estimate, strengths, improvement areas, answer examples, and a suggested next practice. The app does not store audio. Browser speech recognition transcribes the user's speech, so pronunciation, accent, tone, pauses, and speaking speed are not measured by the report.
- If the configured AI provider is unavailable or returns an access error, the conversation practice can fall back to preset interviewer follow-ups and a clearly labelled, basic transcript report. A working provider is still required for detailed AI-generated conversation and evaluation; the fallback report is not an AI assessment.
- Lesson exercises such as **Build a clear sentence**, **Everyday vocabulary**, and adjective/grammar practice rotate through distinct question banks. The expected answers stay on the server and each session avoids repeats until its bank is exhausted.

Voice input works best in a current Chrome or Edge browser on `localhost`/`127.0.0.1` or HTTPS. If microphone permission is unavailable, you can type answers and still generate a report.
