# Gateway Control Center — Architecture

## Overview

The Gateway Control Center is a local web application that manages an AI API
gateway. It is primarily a **Gateway + Credential Monitor + Management Center**.
It is NOT primarily an automatic credential-rotation system.

The application provides a stable local endpoint for AI clients while monitoring
credential health, tracking usage, and helping the user manage credentials,
sessions, providers, and models.

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

- `GatewayManager` — start/stop/restart the gateway process
- `CredentialManager` — manual entry, validation, activation, health monitoring
- `SessionManager` — manual replacement, validation, health monitoring
- `ProviderManager` — CRUD, health checks, capability declarations
- `ModelManager` — model configuration, defaults, fallbacks
- `UsageManager` — token usage tracking and threshold alerts
- `HealthManager` — health checks for gateway, providers, sessions, credentials
- `ProcessManager` — subprocess lifecycle management
- `LogManager` — structured event logging

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
- 9 tables: providers, models, credentials, sessions, credential_events,
  usage_snapshots, settings, events, health_checks

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

### 8. GatewayManager

**Responsibility:** Lifecycle management of the gateway subprocess.

`GatewayManager` (`app/services/gateway_manager.py`) owns the gateway process:

- **start()** — launch the gateway script as a subprocess, wait for port readiness
- **stop()** — graceful terminate, force-kill on timeout
- **restart()** — stop + start
- **status()** — return process state (PID, uptime, restart count, exit code)
- **health()** — test process liveness AND port reachability
- **get_output()** — return recent subprocess stdout/stderr from bounded buffer

**Lifecycle states:** `STOPPED → STARTING → RUNNING → STOPPING → STOPPED`
**Failure states:** `FAILED` (startup failure or unexpected exit)

**Process supervision:**
- stdout/stderr captured via async readers into a bounded 500-line buffer
- Unexpected exit detected by a background wait task
- No aggressive auto-restart (detection + manual restart only)
- Duplicate start/stop calls are safe (idempotent)

**Health checks:**
- Process alive (subprocess not exited)
- Port reachable (TCP connection to configured gateway port)
- HTTP responsive (GET / returns any HTTP response — confirms the server is
  actually serving, not just listening). The legacy gateway returns 404 with a
  text body at the root, which is a safe, non-invasive probe.
- Combined status: HEALTHY, STARTING, STOPPED, FAILED, UNKNOWN

**API routes:**
- `GET /api/gateway/status` — process state + endpoint info
- `GET /api/gateway/health` — health check result (process + port + HTTP)
- `GET /api/gateway/config` — gateway configuration and endpoint contract
- `POST /api/gateway/start` — start the gateway
- `POST /api/gateway/stop` — stop the gateway
- `POST /api/gateway/restart` — restart the gateway
- `GET /api/gateway/logs` — recent subprocess output

**Relationship to legacy gateway:**
GatewayManager wraps `legacy/OpusGateway.py` as a subprocess. It does NOT
import or modify the gateway's internals. The legacy gateway runs as-is;
GatewayManager only manages its lifecycle. Later phases may replace or
refactor the underlying gateway implementation.

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

The application has a deliberate separation between control plane and data plane.
This is intentional for the current migration stage.

### Control Plane (FastAPI backend)

```
React (frontend)
    ↓ HTTP
FastAPI (backend)
    ↓ subprocess management
GatewayManager
    ↓ launches
legacy/OpusGateway.py
```

The control plane handles:
- Gateway lifecycle (start/stop/restart)
- Health monitoring (process + port + HTTP probe)
- Configuration management
- Status reporting
- Event recording

### Data Plane (Legacy Gateway)

```
Client application
    ↓ HTTP (OpenAI-compatible)
legacy/OpusGateway.py (port 5800)
    ↓ HTTPS (with auth replacement)
Upstream provider
```

The data plane handles:
- Request forwarding to the upstream provider
- Authorization header replacement
- Streaming response handling
- Token usage tracking

### Why This Separation

The legacy gateway is the actual data-plane proxy. It handles real provider
requests. The FastAPI backend is the control plane — it manages the gateway
process but does NOT forward provider requests.

This separation means:
- The legacy gateway runs as-is without modification
- The control plane can be developed and tested independently
- Client applications connect to a stable local endpoint
- The data plane can be rewritten later without changing the control plane

**Do not collapse these into one service yet.** After the control plane and
provider model are stable, we may decide whether the data plane should be
rewritten. That decision belongs to a later phase.

## Stable Endpoint Contract

The gateway exposes a stable local endpoint:

    http://<host>:<port><base_path>

Default: `http://127.0.0.1:5800/v1`

This endpoint is a **product contract**. Client applications should not need
to change when:
- Provider changes
- Model changes
- Credential changes
- Session changes
- Backend management changes

The endpoint URL is constructed from configuration:
- `gateway_protocol` (default: `http`)
- `gateway_host` (default: `127.0.0.1`)
- `gateway_port` (default: `5800`)
- `gateway_base_path` (default: `/v1`)

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

Phase 3.1 implements CredentialManager and manual credential management. The following are NOT implemented:

- Credential management (Phase 3)
- Session management (Phase 4)
- Usage monitoring (Phase 5)
- Provider system (Phase 6)
- Model system (Phase 7)
- Provider-specific credential workflows (Phase 8)
- UI completion (Phase 9)
- Electron packaging (Phase 10)

## Legacy Reference

The `legacy/` directory contains the original Python implementation. These
files are preserved as reference material. See AGENT.md for the full
classification of legacy behaviors (useful, fragile, provider-specific,
no longer desired).
