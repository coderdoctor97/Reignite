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
- Combined status: HEALTHY, STARTING, STOPPED, FAILED, UNKNOWN

**API routes:**
- `GET /api/gateway/status` — process state snapshot
- `GET /api/gateway/health` — health check result
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

## What Is Intentionally NOT Implemented Yet

Phase 2.1 implements GatewayManager only. The following are NOT implemented:

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
