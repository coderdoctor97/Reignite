# Gateway Control Center — Architecture

## Overview

The Gateway Control Center is a local web application that manages an AI API
gateway. It is a **Gateway + Multi-Provider Router + Credential Monitor +
Management Center**. It is NOT an automatic credential-rotation system.

The application provides a stable local endpoint for AI clients (OpenAI,
Anthropic, and Responses API formats) while monitoring credential and
session health, tracking usage, and helping the user manage credentials,
sessions, providers, and models. A built-in Apply-Config feature writes the
gateway endpoint configuration into installed CLI agents and IDE
extensions (Claude Code, Claude Desktop, Codex, Grok Build, Cline, Roo
Code) on explicit user action only.

## Core Policy

### Monitoring is universal, credential generation is provider-specific

All providers support monitoring (usage, health, errors). Credential generation,
discovery, and revocation are provider-specific capabilities that must be
explicitly declared and user-initiated.

### Manual replacement is universal

The user must always be able to paste a credential directly into the UI. This
works for every provider, always.

### Sessions are not credentials

Session state, API credential state, and provider configuration are separate
concepts. Never conflate them.

### The application does not act silently

The application must NOT silently generate, delete, revoke, replace, or rotate
credentials unless a specific provider adapter explicitly supports such a workflow
AND the user explicitly enables and initiates it.

## Technology Stack

| Layer      | Technology              |
|------------|-------------------------|
| Frontend   | React + TypeScript      |
| Build      | Vite                    |
| Backend    | FastAPI (Python 3.11+)  |
| Database   | SQLite (via SQLAlchemy + aiosqlite) |
| Migrations | Alembic                 |
| Realtime   | WebSocket (planned)     |
| Packaging  | Electron (later phase)  |

## Directory Structure

```
Reignite/
├── frontend/           # React + TypeScript + Vite
│   ├── src/
│   │   ├── components/ # Shared UI components (AppShell, etc.)
│   │   ├── pages/      # Route-level page components
│   │   ├── lib/        # Utilities (api client, realtime stub)
│   │   ├── hooks/      # Custom React hooks
│   │   └── styles/     # Design tokens, global styles
│   └── public/
│
├── backend/            # FastAPI application
│   ├── app/
│   │   ├── api/        # Route handlers (thin — delegate to services)
│   │   ├── core/       # Configuration, logging, secrets
│   │   ├── services/   # Business logic (the real work)
│   │   ├── models/     # Pydantic schemas / data models
│   │   ├── storage/    # Database, SQLAlchemy models, repositories
│   │   └── adapters/   # External system integrations
│   ├── alembic/        # Database migrations
│   └── tests/
│
├── legacy/             # Original Python implementation (preserved)
├── docs/               # Documentation
├── scripts/            # Development and build scripts
├── desktop/            # Future Electron packaging
├── data/               # SQLite database (created at runtime)
└── .agent/skills/      # Design and functional skills
```

## Architectural Layers

### 1. Frontend (React)

**Responsibility:** Presentation and user interaction only.

React components render the UI and capture user input. They call the backend
API via the centralized `api` client (`src/lib/api.ts`). They do NOT:

- Read credential files directly
- Manipulate gateway processes
- Call provider dashboard APIs
- Perform credential operations
- Manage secrets

All business logic is delegated to the backend via API calls.

### 2. API Layer (FastAPI routes)

**Responsibility:** HTTP boundary — validate requests, call services, format
responses.

Route handlers in `app/api/` are thin. They parse the request, call the
appropriate service, and return the response. They do NOT contain business
logic, database queries, or external API calls directly.

### 3. Service Layer

**Responsibility:** Business logic — the core of the application.

Services in `app/services/` contain all the real work:

- `GatewayService` — the first-party data plane (routing, translation, usage accounting, runtime toggle)
- `CredentialManager` — manual entry, validation, activation, health monitoring
- `SessionManager` — session manual entry, replacement, validation, activation, health
- `ProviderManager` — provider CRUD, capability declarations, health probes, model discovery, OpenRouter quick-add
- `ModelManager` — model configuration, defaults, fallbacks
- `UsageManager` — token usage tracking, snapshots, threshold alerts
- `ConfigApplier` — apply/revert gateway config into external agents (Claude Code, Codex, Cline, Roo Code, ...)
- `ProviderWorkflowService` — user-initiated provider key workflows (list/create/revoke/import)
- `CredentialMonitor` — background monitor (credentials + sessions + usage snapshots)

Services depend on the storage layer and adapters, never on the API layer
or the frontend.

### 4. Repository Layer (Data Access)

**Responsibility:** Persistence — CRUD operations against SQLite.

Repositories in `app/storage/repositories.py` provide clean data access:

- `ProviderRepository` — provider CRUD
- `ModelRepository` — model CRUD with provider relationships
- `CredentialRepository` — credential metadata CRUD (secrets stored separately)
- `SessionRepository` — session metadata CRUD (secrets stored separately)
- `CredentialEventRepository` — credential lifecycle event persistence
- `SessionEventRepository` — session lifecycle event persistence
- `UsageRepository` — usage snapshot persistence
- `SettingsRepository` — application settings CRUD
- `EventRepository` — structured event persistence
- `HealthRepository` — health check result persistence

Repositories do NOT contain business logic. They only handle persistence.
Services call repositories; repositories never call services.

### 5. Secret Storage Boundary

**Responsibility:** Secure storage of actual secret values.

The database stores only:
- `secret_ref` — a reference ID into the SecretStore
- `key_masked` / `session_masked` — masked display values

Actual secrets (API keys, session cookies) are stored in the `SecretStore`
abstraction (`app/core/secrets.py`). The current implementation is a
`FileSecretStore` that writes secrets to individual files outside the database.

**Future:** During the Electron phase, swap in `keyring` for Windows-native
secret storage. The `SecretStore` interface makes this a drop-in replacement.

### 6. Storage Layer (Database)

**Responsibility:** SQLite database via SQLAlchemy.

- SQLAlchemy async engine with aiosqlite driver
- WAL journal mode for concurrent read performance
- Foreign keys enabled for relational integrity
- Alembic for deterministic schema migrations
- 10 tables: providers, models, credentials, sessions, credential_events,
  session_events, usage_snapshots, settings, events, health_checks

**Why SQLite?**
- Zero configuration — no database server to manage
- Single file — easy to backup, move, and version
- Sufficient for a local single-user application
- WAL mode handles concurrent reads from the web UI and background workers
- Can be migrated to PostgreSQL later if needed

### 7. Adapter Layer

**Responsibility:** External system integrations.

Adapters in `app/adapters/` encapsulate communication with external systems:

- `OpusDashboardAdapter` — communicate with the provider's dashboard API
- `OpusApiAdapter` — communicate with the provider's API endpoint
- Future adapters for additional providers

Adapters are called by services, never by the API layer or frontend directly.

### 8. GatewayService (first-party data plane)

**Responsibility:** The unified AI gateway, served in-process by the backend
under `/v1`.

`GatewayService` (`app/services/gateway_service.py`) replaces the legacy
subprocess gateway with a first-party data plane:

- **`GET /v1/models`** — OpenAI-style model list from the registry
- **`POST /v1/chat/completions`** — OpenAI Chat Completions (stream + non-stream)
- **`POST /v1/messages`** — Anthropic Messages API (stream + non-stream)
- **`POST /v1/responses`** — OpenAI Responses API (stream + non-stream)

Routing and protocol adaptation:
- The requested model is matched against the model registry and routed to
  its provider; `model: "auto"` resolves to the provider's default model.
- The provider's ACTIVE credential is fetched from the SecretStore and sent
  upstream (Bearer for OpenAI-compatible, `x-api-key` for Anthropic).
- Requests pass through unchanged when the client speaks the provider's
  native protocol, and are translated otherwise (OpenAI ↔ Anthropic,
  Responses → either). Streaming is preserved end-to-end via SSE.
- Token usage is counted per request and applied to the credential's usage
  counters; `UsageManager` snapshots those counters periodically.

Auth model: local single-user trust — any bearer token is accepted unless
`GCC_GATEWAY_API_KEY` is set. Start/stop is a runtime toggle; when stopped,
`/v1` returns 503.

**API routes (control):**
- `GET /api/gateway/status` — state, stats, endpoint contract
- `GET /api/gateway/health` — data plane liveness
- `GET /api/gateway/config` — configuration + stable endpoint URL
- `POST /api/gateway/start|stop|restart` — runtime toggle

## Migration Strategy

Database schema changes are managed by Alembic:

1. Modify SQLAlchemy models in `app/storage/models.py`
2. Generate a migration: `alembic revision --autogenerate -m "description"`
3. Review the generated migration in `alembic/versions/`
4. Apply: `alembic upgrade head`

For fresh databases, `init_database()` uses `create_all` which creates all
tables from the current models. Alembic is used for upgrading existing
databases.

## Control Plane vs Data Plane

The application keeps a clear separation between control plane and data
plane — both first-party now, running in one process.

### Control Plane (FastAPI `/api`)

```
React (frontend)
    ↓ HTTP
FastAPI /api/*  (management + monitoring)
    ↓
services: GatewayService, CredentialManager, SessionManager,
          ProviderManager, ModelManager, UsageManager, ConfigApplier,
          Monitor
    ↓
SQLite + SecretStore
```

The control plane handles:
- Gateway lifecycle (runtime enable/disable), stats
- Provider / model / credential / session management
- Health monitoring, usage tracking, structured events
- Apply-Config for external agents

### Data Plane (first-party gateway `/v1`)

```
Client application (OpenAI / Anthropic / Responses format)
    ↓ HTTP
GatewayService  (/v1, in-process)
    ↓ protocol routing + translation
Upstream providers (OpenAI-compatible / Anthropic)
```

The data plane handles:
- Model routing via the registry
- Protocol passthrough and OpenAI ↔ Anthropic translation
- Streaming (SSE) preservation
- Token usage accounting per credential

### Why This Shape

The legacy gateway (`legacy/OpusGateway.py`) remains in the repo as
reference only. The first-party data plane reads the active credential from
the SecretStore instead of a plaintext `active_key.txt` file, routes
multiple providers/models, and speaks both major wire protocols — so one
local endpoint serves every client (Claude Code, Codex, Cline, Roo Code,
OpenAI SDKs, ...) regardless of the backend protocol.

## Stable Endpoint Contract

The gateway exposes a stable local endpoint:

    http://<host>:<port><base_path>

Default: `http://localhost:8400/v1`

(host/port = the backend's own; the data plane is served in-process).
`GCC_GATEWAY_PUBLIC_BASE_URL` customizes the URL written into external agent
configs by Apply-Config.

This endpoint is a **product contract**. Client applications should not need
to change when:
- Provider changes
- Model changes
- Credential changes
- Session changes
- Backend management changes

The `GET /api/gateway/config` endpoint returns the full configuration including
the constructed endpoint URL. The frontend uses this to display the stable
endpoint and provide a copy-to-clipboard action.

## Credential Management

### CredentialManager

`CredentialManager` (`app/services/credential_manager.py`) is the business-logic
owner of credential state. It implements the monitor-first, user-controlled
credential lifecycle:

```
MONITOR → DETECT → WARN → USER ACTION → VALIDATE → ACTIVATE → CONTINUE MONITORING
```

**Operations:**
- `list_credentials()` — list all credentials (safe metadata only)
- `get_credential(id)` — get a single credential by ID
- `get_active_credential()` — get the currently active credential
- `add_credential(value, provider_id)` — manually add a credential
- `validate_credential(id)` — validate via adapter abstraction
- `activate_credential(id)` — activate (deactivates previous)
- `deactivate_credential(id)` — deactivate
- `replace_credential(value, provider_id)` — explicit replacement workflow

### SecretStore Boundary

The database stores only:
- `secret_ref` — a reference ID into the SecretStore
- `key_masked` — masked display value (e.g., `************AB12`)

Actual credential values are stored in the `SecretStore` abstraction
(`app/core/secrets.py`). The current implementation is a `FileSecretStore`
that writes secrets to individual files outside the database.

**Credential values never appear in:**
- API responses
- Database plaintext fields
- Logs or events
- Frontend state after save
- Subprocess arguments

### Credential Lifecycle State

A credential has a **lifecycle state** that tracks its position in the
management workflow:

| State | Description |
|-------|-------------|
| `inactive` | Stored but not in use (default for new credentials) |
| `active` | Currently in use by the gateway |
| `expired` | Past its validity period |
| `invalid` | Rejected by the provider |
| `revoked` | Manually revoked |

### Validation State

A credential also has a **validation state** that tracks the result of
the last validation attempt:

| State | Description |
|-------|-------------|
| `unknown` | Not yet validated (default for new credentials) |
| `valid` | Confirmed working with the provider |
| `invalid` | Rejected by the provider |
| `expired` | Provider reports the credential has expired |

**Important distinction:** Lifecycle state tracks whether the credential
is in use. Validation state tracks whether it works. A credential can be
`active` with `unknown` validation status (we're using it but haven't
checked if it's still valid).

### Validation Architecture

Validation uses an adapter pattern. `CredentialManager._perform_validation()`
delegates to a provider-specific adapter. Currently, without a provider
registry, validation returns `unknown` status (the credential exists in
the store but we can't verify it against the upstream provider).

Future phases will implement provider-specific validation adapters:
- API key validation: lightweight API call to verify the key
- Session cookie validation: check if the session is still valid
- OAuth token validation: check expiration

### Manual Replacement Workflow

Replacing a credential is an explicit user-initiated workflow:

1. User clicks "Replace Credential" in the UI
2. User enters the new credential value
3. System adds the new credential
4. System deactivates the current active credential
5. System activates the new credential
6. System records replacement events
7. Legacy adapter writes new credential to `active_key.txt`
8. Legacy gateway discovers the change on its next reload cycle

The previous credential is **deactivated, not deleted**. It remains in
the database for audit purposes.

### Legacy Credential Compatibility Adapter

`LegacyCredentialAdapter` (`app/adapters/legacy_credential_store.py`) bridges
the new credential system with the legacy gateway:

```
CredentialManager
       ↓
LegacyCredentialAdapter
       ↓
active_key.txt (atomic write)
       ↓
legacy OpusGateway.py (reads every 3 seconds)
```

The adapter:
- Writes the active credential to `active_key.txt` using atomic replacement
- Never logs the credential value
- Reports failures clearly
- The legacy gateway discovers changes on its own reload cycle (no restart)

## Session Management (Phase 4.1)

### Sessions Are Not Credentials

A **session** and an **API credential** are completely separate concepts:

```
Provider
   ↓
Session   — provider-side management access (dashboard cookie/token)
   +
Credential — API access (API key / bearer token)
   +
Model
```

- A credential is used for API access (the gateway data plane).
- A session may be required by a provider for provider-side **management**
  operations (e.g. listing/creating/deleting API keys on a dashboard).
- Not every provider requires a session. The application never assumes
  one-provider = one-session, nor that any provider has a session at all.
  Multiple sessions per provider are supported; at most one session per
  provider is `active` at a time (enforced by SessionManager).

### SessionManager

`SessionManager` (`app/services/session_manager.py`) is the business-logic
owner of session state. Route handlers never contain session logic.

**Operations:**
- `list_sessions(provider_id?)` — list sessions (safe metadata only)
- `get_session(id)` — get a single session
- `get_active_session(provider_id?)` — active session lookup
- `add_session(secret_value, provider_id, label?, source='manual')` — manual entry
- `replace_session(secret_value, provider_id, label?, session_id?)` — manual replacement
- `activate_session(id)` — activate (deactivates the provider's previous active session)
- `deactivate_session(id)` — deactivate (preserves the record; there is no DELETE)
- `validate_session(id)` — validate through the SessionValidator abstraction

### Session Lifecycle State

Tracks whether the session is in service:

| State | Description |
|-------|-------------|
| `inactive` | Stored but not in use (default for new sessions) |
| `active` | Currently the provider's active management session |
| `expired` | Validation determined the session has expired |
| `invalid` | Validation rejected the session |

### Session Validation State

Tracks what the last validation attempt determined:

| State | Description |
|-------|-------------|
| `unknown` | Never validated, or provider validation unsupported (default) |
| `valid` | Confirmed working |
| `invalid` | Rejected |
| `expired` | Reported expired |
| `unavailable` | Validation could not run (e.g. endpoint down) |
| `error` | The validator itself failed |

Lifecycle and validation state are deliberately separate fields; health is
derived from both and never persisted.

### Session Health

| Health | Meaning |
|--------|---------|
| `healthy` | validated and valid |
| `warning` | validation unavailable/errored, or a scheduled validation is overdue |
| `critical` | session invalid or expired |
| `unknown` | never validated, or provider validation unsupported |

The system never pretends to know something the adapter cannot determine:
without a provider-specific validator, validation honestly reports
`unknown` — a session is **never** reported valid merely because its secret
exists in the SecretStore.

### Session Validation Architecture

Validation uses an adapter abstraction:

```
SessionManager
     ↓
SessionValidator (protocol)
     ↓
DefaultSessionValidator — returns 'unknown' (no provider adapter yet)
Future provider-specific validators (Phase 4.2+)
```

`SessionValidationResult` supports `valid`, `invalid`, `expired`,
`unavailable`, `unknown`, and `error`. Validation timing:
- `last_validated` — when the last attempt ran
- `next_validation_at` — computed from `GCC_SESSION_VALIDATION_INTERVAL`
- manual "Validate Now" action in the UI

There is **no aggressive polling** and **no automatic session renewal**. A
later phase may let the background monitor call session validation, but
renewal/refresh is never silent.

### Session Manual Replacement Workflow

Explicit user-initiated workflow:

1. User clicks "Replace" on an existing session
2. User enters the new session secret
3. System stores the new session and validates it
4. If validation passes (or honestly reports `unknown`), the new session
   is activated and the previous session becomes `inactive`
5. If validation reports `invalid`/`expired`, the new session is NOT
   activated and the previous session is left untouched
6. Audit events `replacement_requested` → `replacement_completed` are recorded

The previous session is **deactivated, not deleted**, and replacement is
duplicate-safe: repeated replacements each produce a new record, deactivate
their predecessor, and always leave exactly one active session per provider.

### SessionProviderAdapter (Legacy Compatibility Seam)

`SessionProviderAdapter` (`app/adapters/session_provider_adapter.py`) is the
only path by which a raw session secret may leave the SecretStore:

```
SessionManager → SecretStore
                    ↑
SessionProviderAdapter.get_active_session_secret(provider_id)
                    ↓
future provider-specific management adapter (Phase 4.2+)
```

A future provider-management adapter uses the value for one request and
discards it. The legacy KeyBinder/pull scripts are NOT modified in this
phase; they keep their own (hard-coded) session handling. See
`docs/legacy-session-flow.md` for the full legacy analysis.

### Session Secrets

Session secrets follow the same SecretStore boundary as credentials:

- Stored via `SecretStore`; SQLite holds only `secret_ref` + `session_masked`
- Never in API responses, logs, events (`session_events` and `events`), or
  frontend state after submission
- Never placed in command-line arguments or subprocess environments
- Validator error text is redacted defensively before it is persisted

### Session Events

Two complementary records exist:

- `session_events` table — per-session audit trail (mirrors
  `credential_events`): `created`, `imported_manually`, `validated`,
  `activated`, `deactivated`, `invalid`, `expired`,
  `replacement_requested`, `replacement_completed`, `warning`
- `events` table — app-wide log entries with `session.*` types
  (`session.created`, `session.activated`, `session.invalid`, ...)

Event payloads contain only IDs and masked values — never secrets.

### Session API

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/sessions` | List sessions (optional `provider_id` filter) |
| GET | `/api/sessions/health` | Health summaries + counts |
| GET | `/api/sessions/active` | Active session (optional `provider_id`) |
| GET | `/api/sessions/{id}` | Single session |
| POST | `/api/sessions` | Add a session manually (201) |
| POST | `/api/sessions/replace` | Replace an existing session |
| POST | `/api/sessions/{id}/validate` | Validate now |
| POST | `/api/sessions/{id}/activate` | Activate (409 if expired/invalid) |
| POST | `/api/sessions/{id}/deactivate` | Deactivate |

There is deliberately **no DELETE endpoint** — deactivation is preferred.

### Why Automatic Session Renewal Is Not Implemented

- Session renewal requires provider-specific login/refresh flows — exactly
  the kind of provider-specific behavior that belongs in adapters added
  later, not in the generic session system.
- Silent renewal would violate the monitor-first, user-controlled policy.
- The legacy scripts had no renewal either — they simply failed when the
  cookie died. Phase 4.1 replaces that silent failure with clear detection
  (validation states, health, "Session requires attention" UX) and a guided
  manual replacement workflow.

## Providers, Models, and Provider Workflows

### ProviderManager

`ProviderManager` (`app/services/provider_manager.py`) owns provider
configuration. Providers declare capabilities explicitly — nothing is
guessed:

| Capability | Meaning |
|------------|---------|
| `credential_validation` | an adapter can validate credentials |
| `credential_discovery` | keys can be listed (masked) |
| `credential_creation` | keys can be created (user-initiated) |
| `credential_revocation` | keys can be revoked (user-initiated) |
| `session_required` | management needs a provider session |
| `session_validation` | session-validator adapter name (registry) |
| `dashboard_adapter` | dashboard management adapter name (registry) |

Also: generic reachability health probes, OpenAI-compatible model
discovery (`GET {base_url}/models`, imports as disabled models), and the
OpenRouter quick-add template.

### Adapter Registry

`app/adapters/registry.py` maps declared adapter names to
implementations. The built-in `opus-dashboard` adapter
(`app/adapters/opus_dashboard.py`) implements session validation and
key management against the legacy-style dashboard API
(`/dashboard/api/keys` with a `opus_session` cookie, per
`docs/legacy-session-flow.md`). Providers opt in via capabilities;
unknown names fall back to safe defaults. The dashboard base URL can
differ from the inference base URL via `metadata.dashboard_base_url`.

### ProviderWorkflowService (user-initiated only)

`app/services/provider_workflows.py` implements list / create / revoke /
import-latest key workflows. Every action is:
- capability-gated (403 when not declared)
- session-gated (active session required)
- explicit (no polling, no rotation, no quota auto-recovery)

Created keys are stored as `source=provider-assisted` credentials that
start **inactive** — the user validates and activates them. Import-latest
compares with the active credential and only creates a new one when the
key actually changed.

### ModelManager

`ModelManager` (`app/services/model_manager.py`) owns model configuration:
per-provider models with at most one default and one fallback each, manual
enable/disable, and the routing registry the gateway consults.

## Usage Monitoring

`UsageManager` (`app/services/usage_manager.py`):
- per-credential counters (updated by the gateway on every completion)
- periodic snapshots into `usage_snapshots` (monitor + manual capture)
- runtime thresholds (`usage_limit`, `usage_warning_threshold`) with a
  duplicate-suppressed `usage.warning` event
- best-effort legacy `token_usage.json` import

## Apply Config (Agents & Apps)

`ConfigApplier` (`app/services/config_applier.py`) writes the gateway
endpoint into installed apps and CLI agents on explicit Apply:

| Target | What is written |
|--------|-----------------|
| Claude Code | `~/.claude/settings.json` env: `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` |
| Claude Desktop | `claude_desktop_config.json` (platform paths), same env keys |
| Codex CLI | `~/.codex/config.toml` (`[model_providers.gcc]`, `wire_api="responses"`) |
| Codex (app/IDE) | shares `~/.codex/config.toml` |
| Grok Build | `~/.grok/user-settings.json` (best-effort) |
| Cline | VS Code `settings.json` (`cline.openAiCompatible.*`) |
| Roo Code | VS Code `settings.json` (`roo-cline.*`, Anthropic mode through the gateway) |

Safety: master toggle + per-target toggles, writes only on Apply, every
write backed up (`*.gcc-backup`), Revert restores. JSON files are merged
(never clobbered); Codex TOML is parsed and re-serialized (reported as
error, file untouched, if unparsable).

Because the gateway speaks the Anthropic Messages API at `/v1/messages`
regardless of backend protocol, Claude-side agents work against any
backend; OpenAI-side agents use `/v1/chat/completions` and Codex uses
`/v1/responses`.

### CredentialHealthManager

`CredentialHealthManager` (`app/services/credential_health_manager.py`) monitors
credential health by running validations and tracking health states.

**Operations:**
- `check_credential(id)` — run validation on a single credential
- `check_all_due_credentials()` — check all credentials whose validation is due
- `get_health(id)` — get health summary without running validation
- `get_all_health()` — get health summaries for all credentials

**Validation flow:**
1. Set `validation_status` to `pending`
2. Invoke the validation adapter
3. Update `validation_status` with the result
4. Calculate `next_validation_at` based on configured interval
5. Record events (with duplicate suppression)

**Health states** (derived from validation status):
- `healthy` — `validation_status == 'valid'`
- `warning` — `validation_status` in (`unknown`, `unavailable`, `pending`) or validation overdue
- `critical` — `validation_status` in (`invalid`, `expired`)
- `unknown` — never validated, no validation possible

**Validation adapter pattern:**
The health manager uses a `CredentialValidator` protocol. Implementations
validate a credential against a specific provider. The default validator
(`DefaultCredentialValidator`) only checks if the secret exists in the
store — it does NOT make external API calls.

**Duplicate warning suppression:**
When validation detects an issue (`invalid`, `expired`, `unavailable`),
the health manager checks if an identical event was created recently
(within 5 minutes). If so, the duplicate event is suppressed. This
prevents warning spam from repeated health checks.

**Scheduling:**
Each credential has a `next_validation_at` timestamp. The
`check_all_due_credentials()` method only checks credentials whose
`next_validation_at <= now`. The validation interval is configurable
via `GCC_CREDENTIAL_VALIDATION_INTERVAL` (default: 1 hour).

**Important:** Monitoring never implies automatic replacement. The health
manager detects issues and records events. The user must take action.

### CredentialMonitor

`CredentialMonitor` (`app/services/credential_monitor.py`) is the background
monitoring service that periodically checks credentials due for validation.

```
CredentialMonitor (asyncio background task)
           ↓
    CredentialHealthManager
           ↓
    CredentialValidator
           ↓
    CredentialRepository
```

**Lifecycle:**
- `start()` — launches an asyncio background task
- `stop()` — cancels the task and waits for clean shutdown
- `run_once()` — triggers a single monitoring cycle
- `status()` — returns current monitor state and statistics

**Scheduling:**
- The monitor wakes every `credential_monitor_interval` seconds (default: 60s)
- Each cycle calls `CredentialHealthManager.check_all_due_credentials()`
- Only credentials with `next_validation_at <= now` are checked
- The monitor interval is NOT the same as the validation interval

**Non-overlapping cycles:**
- If a cycle is still running when the next interval arrives, the new cycle is skipped
- An asyncio lock prevents concurrent cycles
- `run_once()` returns a clear status if a cycle is already in progress

**Error resilience:**
- Individual credential check failures don't stop the monitor
- The monitor logs errors and continues to the next credential
- Monitor-level errors are caught and the loop continues after a brief pause

**Health change detection:**
- The monitor tracks previous health states for each credential
- When a health state changes between cycles, it emits a structured event
- Event types: `credential.health_changed`, `credential.warning`, `credential.critical`
- Duplicate events are suppressed (no repeated notifications for unchanged conditions)

**FastAPI integration:**
- Started during application lifespan when `credential_monitor_enabled == true`
- Stopped cleanly during application shutdown
- Monitor failures don't prevent the application from starting

**API routes:**
- `GET /api/monitor/status` — monitor status and statistics
- `POST /api/monitor/run` — trigger a single monitoring cycle

**Events emitted:**
- `monitor.cycle_completed` — after each successful cycle
- `monitor.error` — when a cycle fails
- `credential.health_changed` — when a credential's health state changes
- `credential.warning` — when a credential enters warning state
- `credential.critical` — when a credential enters critical state

**Important:** The monitor never automatically replaces credentials. It detects
issues and records events. The user remains responsible for replacing credentials.

### Why Automatic Rotation Is Not Part of the Default System

The legacy project included automatic credential rotation via `KeyBinder.py`
and `rotate_now.py`. These scripts:
- Delete all existing keys on the provider dashboard
- Create a new key
- Write it to `active_key.txt`

This approach is fragile, provider-specific, and destructive. The new system
follows a monitor-first policy: detect issues, warn the user, and let the
user decide what to do. Automatic rotation is a provider-specific capability
that may be offered as an opt-in feature in future phases, but it is NOT
the default behavior.

## What Is Intentionally NOT Implemented Yet

The system is complete through the provider/router/agent integration scope.
The following are intentionally NOT implemented:

- Automatic credential rotation / session renewal (provider-specific,
  opt-in workflows only — the monitor-first policy forbids silent action)
- Automatic quota recovery (legacy "delete all keys and retry" is replaced
  by a surfaced error + user decision)
- Electron desktop packaging (the web UI + local backend covers the same
  scope; Electron remains an option for a later release)
- Full fidelity of every client-side protocol edge case (e.g. exact
  reasoning-token accounting across translation) — the gateway covers the
  standard OpenAI / Anthropic / Responses surfaces

## Legacy Reference

The `legacy/` directory contains the original Python implementation. These
files are preserved as reference material. See AGENT.md for the full
classification of legacy behaviors (useful, fragile, provider-specific,
no longer desired).
