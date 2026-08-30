# Gateway Control Center — Architecture

## Overview

The Gateway Control Center is a local web application that manages an AI API
gateway. It provides a stable local endpoint for AI clients while handling
credential management, rotation, provider configuration, and usage tracking
behind the scenes.

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
- Perform credential rotation
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
- `KeyManager` — load, save, validate, and mask credentials
- `SessionManager` — manage session credentials for provider dashboards
- `RotationManager` — manual and automatic credential rotation
- `ProviderManager` — CRUD and health checks for providers
- `ModelManager` — model configuration, defaults, fallbacks
- `UsageManager` — token usage tracking and threshold alerts
- `HealthManager` — health checks for gateway and providers
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
- `RotationRepository` — rotation event persistence
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

**Why not encrypt in SQLite?**
Adding sqlcipher introduces a native dependency that complicates the build.
The file-based store keeps the boundary clean and makes the future swap to
Windows Credential Manager (via `keyring`) trivial.

**Future:** During the Electron phase, swap in `keyring` for Windows-native
secret storage. The `SecretStore` interface makes this a drop-in replacement.

### 6. Storage Layer (Database)

**Responsibility:** SQLite database via SQLAlchemy.

- SQLAlchemy async engine with aiosqlite driver
- WAL journal mode for concurrent read performance
- Foreign keys enabled for relational integrity
- Alembic for deterministic schema migrations
- 9 tables: providers, models, credentials, sessions, rotation_events,
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

### 8. Realtime (Planned — Phase 6)

WebSocket or Server-Sent Events will push real-time updates to the frontend:

- Gateway status changes
- Usage threshold alerts
- Rotation events
- Health status changes
- Log entries

The frontend has a `realtime` client stub (`src/lib/realtime.ts`) that
establishes the interface now. The actual WebSocket implementation will
arrive in Phase 6.1.

### 9. Electron (Planned — Phase 7)

The web application will be wrapped in Electron for Windows packaging:

- System tray integration
- Auto-start on boot
- Native notifications
- Process lifecycle management

Electron should NOT duplicate backend business logic. It wraps the existing
web application and adds OS-level integration.

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

Phase 1.2 establishes the data foundation only. The following are NOT implemented:

- Gateway proxy logic (Phase 3.1)
- Credential management (Phase 2.1)
- Session management (Phase 2.2)
- Provider dashboard API client (Phase 2.3)
- Auto-discovery (Phase 2.4)
- Usage tracking (Phase 3.2)
- Rotation (Phase 3.3)
- Provider CRUD UI (Phase 4.1)
- Model management (Phase 4.2)
- Realtime WebSocket (Phase 6.1)
- Electron packaging (Phase 7)

## Legacy Reference

The `legacy/` directory contains the original Python implementation:

- `OpusGateway.py` — HTTP proxy with usage tracking
- `OpusControlPanel.py` — Tkinter GUI controller
- `KeyBinder.py` — Usage watcher and rotation signal
- `key_poller.py` — Automatic key polling
- `pull_latest_key.py` — Key discovery from provider dashboard
- `rotate_now.py` — Manual key rotation

These files are preserved as reference material. They will be studied and
incrementally replaced during Phases 2 and 3.
