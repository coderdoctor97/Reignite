# Gateway Control Center (Reignite)

A local-first **Gateway Control Center**: a unified AI endpoint that routes
multiple providers and models through one stable OpenAI / Anthropic /
Responses API, with a web UI for managing providers, credentials, sessions,
models, usage, and applying the endpoint to installed CLI agents and IDE
extensions.

**Monitor-first, user-controlled.** Nothing happens automatically:
credentials and sessions are never rotated, replaced, generated, or revoked
without an explicit user action.

## Architecture

```
React frontend (Vite)
      ↓ REST
FastAPI control plane (:8400)
      ├── /api/*        management + monitoring APIs
      ├── /v1/*         first-party gateway data plane (OpenAI / Anthropic / Responses)
      └── services      SessionManager, CredentialManager, ProviderManager,
                        ModelManager, UsageManager, ConfigApplier, Monitor
      ↓
adapters  (provider-specific, capability-gated, user-initiated only)
      ↓
SQLite (metadata) + SecretStore (secrets, never in the DB)
```

## Features

- **Unified gateway** under `/v1`: `GET /v1/models`, `POST /v1/chat/completions`
  (OpenAI), `POST /v1/messages` (Anthropic), `POST /v1/responses` (Responses
  API) — streaming and non-streaming, with automatic protocol translation
  between OpenAI and Anthropic backends.
- **Multi-provider routing**: providers declare a protocol and base URL;
  models route to their provider; `model: "auto"` uses the default model.
  OpenRouter quick-add and OpenAI-compatible model discovery included.
- **Credentials**: manual entry, validation, activation, deactivation,
  replacement — monitor-first, never automatic.
- **Sessions**: provider dashboard sessions (separate from credentials),
  validated through provider adapters, with health and guided replacement.
- **Provider workflows** (user-initiated only, capability-gated): list /
  create / revoke dashboard keys, import the latest key.
- **Usage tracking**: per-credential counters, snapshots, thresholds,
  warning events.
- **Apply Config**: one toggle writes the gateway endpoint into Claude Code,
  Claude Desktop, Codex (CLI/app), Grok Build (best-effort), Cline, and
  Roo Code — with backups and Revert.
- **Demo provider**: a local mock (OpenAI API + dashboard) seeded
  automatically so the whole system is testable without real credentials.

## Running

```bash
# 1. Backend dependencies
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt

# 2. Demo provider (optional, for testing without real credentials)
./scripts/dev-demo-provider.sh        # port 5900

# 3. Backend (control plane + gateway data plane)
./scripts/dev-backend.sh              # port 8400, /api + /v1

# 4. Frontend
cd frontend && npm install && npm run dev   # port 5173
```

Open http://localhost:5173. The gateway endpoint is `http://localhost:8400/v1`.

The demo provider ("Demo Provider") is seeded with an active demo
credential, session, and models. Try the Live test on the Gateway page, or:

```bash
curl -X POST http://localhost:8400/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello!"}]}'
```

## Testing

```bash
cd backend && ../.venv/bin/python -m pytest   # 233 tests
cd frontend && npm run build && npm run lint
```

## Documentation

- `docs/architecture.md` — full architecture
- `docs/data-model.md` — database schema
- `docs/legacy-session-flow.md` — analysis of the legacy scripts
- `legacy/` — the original implementation (reference only, not modified)
