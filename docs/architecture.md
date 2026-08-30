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
| Database   | SQLite (via aiosqlite)  |
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
│   │   ├── core/       # Configuration, logging
│   │   ├── services/   # Business logic (the real work)
│   │   ├── models/     # Pydantic schemas / data models
│   │   ├── storage/    # Database access layer
│   │   └── adapters/   # External system integrations
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

### 4. Storage Layer

**Responsibility:** Data persistence — SQLite database access.

The storage layer in `app/storage/` provides:

- Database connection management (`database.py`)
- Query helpers (future: repositories or DAOs)

Services call the storage layer to read and write data. The storage layer
does not contain business logic.

### 5. Adapter Layer

**Responsibility:** External system integrations.

Adapters in `app/adapters/` encapsulate communication with external systems:

- `OpusDashboardAdapter` — communicate with the provider's dashboard API
- `OpusApiAdapter` — communicate with the provider's API endpoint
- Future adapters for additional providers

Adapters are called by services, never by the API layer or frontend directly.

### 6. Realtime (Planned — Phase 6)

WebSocket or Server-Sent Events will push real-time updates to the frontend:

- Gateway status changes
- Usage threshold alerts
- Rotation events
- Health status changes
- Log entries

The frontend has a `realtime` client stub (`src/lib/realtime.ts`) that
establishes the interface now. The actual WebSocket implementation will
arrive in Phase 6.1.

### 7. Electron (Planned — Phase 7)

The web application will be wrapped in Electron for Windows packaging:

- System tray integration
- Auto-start on boot
- Native notifications
- Process lifecycle management

Electron should NOT duplicate backend business logic. It wraps the existing
web application and adds OS-level integration.

## Why Business Logic Must Stay Outside React

1. **Testability:** Services can be tested independently of the UI.
2. **Reusability:** The same service works for the web UI, Electron, CLI,
   and future API consumers.
3. **Separation of concerns:** UI code handles rendering and interaction;
   service code handles rules and state.
4. **Security:** Secrets and credentials are managed server-side, never
   exposed to the frontend bundle.
5. **Maintainability:** Changes to business logic don't require UI changes
   and vice versa.

## Configuration

All configuration flows through `app/core/config.py` using Pydantic Settings.
Environment variables are prefixed with `GCC_`. See `.env.example` for the
full list.

## What Is Intentionally NOT Implemented Yet

Phase 1.1 establishes the skeleton only. The following are NOT implemented:

- Gateway proxy logic (Phase 1.4)
- Credential management (Phase 2.1)
- Session management (Phase 2.2)
- Provider dashboard API client (Phase 2.3)
- Auto-discovery (Phase 2.4)
- Usage tracking (Phase 3.1)
- Rotation (Phase 3.2)
- Provider CRUD (Phase 4.1)
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
incrementally replaced during Phases 1.4 through 4.x.
