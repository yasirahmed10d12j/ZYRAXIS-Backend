# ZYRAXIS-Backend — zero-cost AI backend for the ZYRAXIS iOS client

FastAPI service implementing the **exact** wire contract in
`D:\Projects\ZYRAXIS-iOS` (`ZYRAXIS/Networking/BackendProtocol.swift`,
`ZYRAXIS/AI/BackendAIService.swift`). The iOS project is **not modified** by
this backend; all keys live here or in host secrets — never in the app or Git.

> **Budget: ₹0.** No paid hosting, subscriptions, billing upgrades, or paid
> API usage. Nothing here deploys or creates cloud resources without your
> explicit approval (see §5). No payment method is added anywhere, ever.

---

## 0. Zero-cost feasibility report (researched 2026-10-13)

| Layer | Option | Free eligibility | Quota (verify live — vendors rotate IDs/limits) | Verdict |
|---|---|---|---|---|
| LLM primary | Gemini API, AI Studio key, no card | Free tier, no billing | Flash-class models rate-limited per model (recent: Flash-Lite ~15 RPM / ~1000 RPD; Flash ~10 RPM / ~250 RPD; Pro models paid-only). Spend cap N/A — cannot charge without billing. Limits in AI Studio dashboard. | ✅ Eligible |
| LLM fallback | Groq free tier, no card | Free tier, no billing | Per-model limits, org-level (recent typical: ~30 RPM / ~6K TPM / ~14.4K RPD). Live values on console rate-limits page. No charge possible without upgrade. | ✅ Eligible |
| Hosting | Render free web service | Hobby workspace $0, **no card required** | 750 instance-hours/workspace/month; sleeps after 15 min without inbound traffic (HTTP requests **and** WS messages count, since Feb 2026); ~60 s cold start; 512 MB / 0.1 CPU; 5 GB bandwidth/mo included. | ✅ Eligible with caveats |

**WebSocket support:** Render web services accept inbound WS on the single public port (`$PORT`, default 10000) — our app binds `$PORT` and serves HTTP+WS from one process. No fixed WS timeout; connections drop on sleep/redeploy, and the iOS client auto-reconnects (`WebSocketManager`).

**Sleep/cold-start vs the iOS client (honest):**
- Idle 15 min → service sleeps. Next request/connect wakes in ~60 s.
- The iOS REST path times out at 30 s (`BackendConfig`), so the **first chat after sleep surfaces a friendly transport error — retry once warm**. WS streaming has no fixed client timeout, but the user waits ~60 s for the first delta.
- An idle-but-open socket may still trigger spindown (only *messages* reset the timer; protocol pings may not count). The client reconnects automatically.
- A keep-alive pinger would burn the 750 h budget (one always-awake service ≈ the whole allowance; a second awake service suspends everything till month-end). **Recommendation: let it sleep.**
- No database is used (stateless design), dodging the 30-day free-Postgres expiry entirely.

**Accidental-charge risks — all neutralized by design:**
1. Render bandwidth overage ($0.15/GB past 5 GB): **without a payment method on file, Render suspends instead of billing.** Never add a card (forbidden by §6 anyway). Personal-assistant traffic is MB-scale.
2. Gemini/Groq free tiers: spend cap N/A without billing; over-quota returns 429, which the server maps to retryable `assistant_error` + Groq fallback. Never enable billing.
3. No paid models configured (defaults are Flash-Lite + `llama-3.1-8b-instant`); model IDs are env-configurable but the code never selects paid tiers.
4. Render Postgres/Key Value: not used, not declared in `render.yaml`.

**Privacy note:** Gemini free-tier prompts may be used to improve Google products (outside EU/UK/EEA). Don't send secrets; the server never logs message bodies or keys.

**Abuse hardening:** REST bodies capped at 1 MiB (413 beyond); WS frames capped
at the server's 1 MiB message limit; per-IP REST/WS rate limits (in-memory);
no CORS middleware (browsers blocked by default; the native app is unaffected).

## 1. Exact iOS contract implemented (source → behavior)

| iOS source | Server behavior |
|---|---|
| `BackendAIService.swift:98-103` `POST /api/{apiVersion}/chat` | `POST /api/{api_version}/chat` (any version served; mismatch logged). Body `{requestId?, text, context?}` → `200 {text, toolCalls: [], requestId}` |
| `BackendAIService.swift:267-274` Bearer auth | `Authorization: Bearer <key>` validated against `BACKEND_API_KEYS`; fail-closed (empty set denies all) → 401 |
| `BackendProtocol.swift:114-151` envelopes | Every frame `{version: 1, type, requestId, payload}`; `requestId` echoed verbatim (adapter requires UUID); unknown types ignored; version mismatch → `assistant_error VERSION_MISMATCH` |
| `BackendProtocol.swift:194-243` payloads | `session_start` recorded, no reply (client self-emits `.connected`); `user_message` → `assistant_delta` ×N (`seq` 1-based, strictly increasing — engine drops stale/dupes) → `assistant_complete{fullText, toolCalls}`; `ping` → `pong`; `session_end`/disconnect/new message cancels in-flight turn; `tool_result` tolerated+logged (v0.1 never sends `tool_call`) |
| `ConversationManager.swift:234,273` engine rules | Deltas strictly increasing from 1; `complete.fullText` always populated (never relies on the empty-fallback) |
| Provider fallback (verified design) | Gemini → Groq in `PROVIDER_ORDER`; **all** failures incl. bad keys fall through (a bad key on one provider never sinks a healthy other); final error keeps its type (429/502 mapping honest). History is merged to strict user/model alternation for Gemini. |
| `WebSocketManager.swift:80-92` plain socket open | WS accepts connections without auth headers by design (documented); optional `?key=` validated if present; per-IP rate limits apply |
| `APIClient.swift:71-74`, `BackendConfig` 30 s timeout | Error mapping: 401 bad key, 429 rate-limited (retryable), 502 provider failure, 400 bad body |

## 2. Layout

```
ZYRAXIS-Backend/
├── app/
│   ├── main.py        # routes: /, /healthz, POST /api/{v}/chat, WS /live
│   ├── protocol.py    # envelope builders (version=1)
│   ├── providers.py   # Gemini → Groq fallback, stream + non-stream
│   ├── auth.py        # Bearer gate (fail-closed) + optional WS key
│   ├── ratelimit.py   # in-memory per-IP guard (no DB)
│   └── config.py      # env-driven settings, no secrets in code
├── tests/             # mocked suite — zero provider credits by construction
│   ├── conftest.py    # fake keys before app import
│   ├── test_protocol.py, test_auth.py, test_rest.py, test_websocket.py
├── requirements.txt   # fastapi, uvicorn[standard], httpx, pytest
├── .env.example       # shape of local secrets (never commit .env)
├── render.yaml        # Blueprint declaration only — NOT applied
└── README.md          # this file
```

## 3. Local run (needs Python 3.11+ — NOT installed on this machine)

```powershell
cd D:\Projects\ZYRAXIS-Backend
copy .env.example .env   # then edit: BACKEND_API_KEYS + PROVIDER_ORDER
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q                  # mocked suite, no network, no credits
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 3a. Local LLM (Ollama + qwen2.5:3b, private + free)

1. Install Ollama for Windows, then: `ollama pull qwen2.5:3b`
2. Verify: `ollama run qwen2.5:3b "say ok"` (Ctrl+D to exit; leaves server on `localhost:11434`).
3. `.env`: `PROVIDER_ORDER=local,gemini,groq`, `LOCAL_MODEL=qwen2.5:3b`,
   `LOCAL_BASE=http://localhost:11434/v1/chat/completions`,
   `MAX_OUTPUT_TOKENS=512` (speed on 8GB CPU).
4. Cloud keys optional now: local answers with no keys; Gemini/Groq only
   catch what local fails. Empty cloud keys = those providers skipped.
5. Render note: free instances (512 MB) cannot run Ollama — keep Render on
   `PROVIDER_ORDER=gemini,groq`; laptop runs `local,gemini,groq`.

### 3b. Admin dashboard (devices + control, same process)
Open `http://<laptop-ip>:8000/admin?key=<one BACKEND_API_KEYS value>` (LAN only):
status cards (uptime, WS connections, REST chats, LLM ok/fail, Ollama UP/DOWN),
devices table (session, IP, client model), runtime config form (provider order,
max tokens, temperature — applied live, no restart), requests per IP, recent
events, **live transcript mirror** (every device request + reply), **remote
command** sender (device + command → `remote_command` frame; offline devices
queue until next `session_start`), device ping (pong latency in events), and
clear. No key is ever shown or editable there. New endpoints are covered by
`tests/test_admin.py` (auth gate, device tracking, runtime config, mirror,
queue, clear).

### 3c. Ecosystem sync API (shared memory + tasks + device identity)

One hub for every client (iOS first, Android/Desktop next — same contract):

- Identity: `session_start` accepts optional `deviceId` (stable per device)
  + `deviceName` ("Yasir's iPhone"). Without them the server falls back to
  the per-turn session id (old behavior). `GET /api/{v}/devices` (Bearer)
  lists claimed devices with online status.
- Memory: `GET /api/{v}/memory?query=&limit=`, `POST /api/{v}/memory`
  `{content, tags?, importance?}`, `DELETE /api/{v}/memory/{id}`.
  Substring search + importance ordering mirror the iOS local service.
- Tasks: `GET /api/{v}/tasks?filter=active|all|completed|cancelled`,
  `POST /api/{v}/tasks` `{title, notes?, dueDate?, priority?}`,
  `POST /api/{v}/tasks/{id}/complete|cancel`,
  `POST /api/{v}/tasks/{id}/snooze` `{until}`.
- Storage: JSON files (`MEMORY_FILE`/`TASKS_FILE`, default
  `A:\ZYRAXIS-Server\hub\`), atomic replace, survive restarts. Honest
  limit: notifications still fire per-device only (OS design); the hub
  holds the shared list.
- Covered by `tests/test_sync.py` (auth, CRUD, filters, persistence
  round-trip, claim + list, live `remote_command` push to a device id).

Local iOS pointing (same Wi-Fi/hotspot; app needs `http://` ATS exception or
`https` via a tunnel — iOS change only if you approve it):
base URL `http://<laptop-ip>:8000`, WS `ws://<laptop-ip>:8000/live`.

## 4. Tests

`pytest -q` — protocol shapes, auth gate (incl. fail-closed),
REST 200/400/401/413/429/502 + requestId echo + auth fall-through,
Gemini role alternation, WS happy path (seq 1..N, UUID echo, fullText),
ping→pong, unknown-type tolerance, version mismatch, missing-requestId
tolerance, rate-limit mapping, WS key rejection, limiter units.
Provider HTTP is monkeypatched at the app boundary; **no test can spend
credits by construction** (no real keys exist in the suite).

> Status 2026-10-13: written but **not executed here** — this machine has no
> Python runtime (Store stubs only). Run `pytest -q` on any Python 3.11+
> machine before deploying.

## 5. Deployment (⚠️ STOP — requires your explicit approval; creates nothing until then)

1. Approve this plan (nothing below runs without it).
2. Create free accounts (no cards): Google AI Studio → `GEMINI_API_KEY`;
   console.groq.com → `GROQ_API_KEY`; render.com + GitHub (Render deploys
   from Git — push this folder to a repo first).
3. In Render Dashboard → New → Web Service (or Blueprint with `render.yaml`,
   plan **free**): set secrets `BACKEND_API_KEYS` (generate 1–2 random
   strings), `GEMINI_API_KEY`, `GROQ_API_KEY`. Confirm plan shows **Free**,
   confirm **no payment method** is attached, then Create.
4. Note the URL `https://<name>.onrender.com`. First hit takes ~60 s (cold).
5. On iPhone (any network — Render has managed TLS, so no ATS change needed):
   base URL `https://<name>.onrender.com`, WS `wss://<name>.onrender.com/live`,
   paste one `BACKEND_API_KEYS` value into Settings → Backend API key,
   provider → ZYRAXIS Backend. `NO BACKEND` badge clears; first message may
   take ~60 s if the service slept — retry once.

## 6. Hard rules (standing)

- ₹0 total: no cards, no billing, no paid models, no paid resources/upgrades.
- Keys in env secrets only — never in the iOS app, never in Git (`.env` ignored).
- `D:\Projects\ZYRAXIS-iOS` untouched (contract already sufficient — verified).
- `codemagic.yaml`, certificates, profiles, signing secrets: out of scope here.
- Ask before ANY action that could cost money. None is planned.
