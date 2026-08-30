# Gateway Control Center — Agent Contract

## 1. Project Identity

**Project:** Gateway Control Center

**Purpose:** Build a polished local web-based control center for a Windows-hosted AI API gateway.

The application provides a stable local gateway interface while allowing the user to manage credentials, sessions, providers, models, usage, rotation, health, logs, and configuration from a unified UI.

**Target platform:** Windows (primary). The final product is a local Windows developer/system utility.

**Current status:** Phase 1.2 — Data foundation.

---

## 2. Architecture

```
React + TypeScript (Vite)
        ↓  HTTP + WebSocket
    FastAPI backend
        ↓
    Service layer (business logic)
        ↓
    Repository layer (persistence)
        ↓
    Adapter layer (external systems)
        ↓
    SQLite (application state)
```

**Key principle:** Business logic must NOT live in React components.

- **React:** presentation and user interaction only
- **FastAPI:** API boundary — validate requests, call services, format responses
- **Services:** all business logic, credential handling, rotation, health checks
- **Repositories:** data persistence — CRUD against SQLite, no business rules
- **Adapters:** external system integrations (provider APIs, dashboard APIs)
- **Storage:** SQLite database, secret store abstraction

React must never directly read credential files, manipulate gateway processes, call provider dashboard APIs, perform credential rotation, or manage secrets. Those responsibilities belong in the backend.

---

## 3. Scope

### In scope

1. Gateway lifecycle management (start/stop/restart)
2. Gateway health and status
3. Session management (first-class feature — manual entry, validation, replacement)
4. Manual credential entry (mandatory fallback)
5. Automatic credential retrieval (optional — must be recoverable when it fails)
6. Automatic credential rotation (must support multiple triggers, not just token thresholds)
7. Manual rotation
8. Provider management (configurable, extensible)
9. Model management (per-provider, with defaults and fallbacks)
10. Usage tracking (current, historical, per-credential)
11. Structured logs and events
12. Health checks (gateway, provider, session)
13. Stable local API endpoint
14. Configuration management
15. Safe secret handling

### Out of scope

- **Low-latency gateway** — explicitly excluded. Do not port, depend on, optimize, or reproduce it.
- Cloud deployment
- Remote user authentication
- Chat/prompt playground
- Unrelated AI features
- Electron until the web application is functionally complete and tested

---

## 4. Core Design Principles

### 4.1 Stable local gateway endpoint

The local client-facing endpoint must remain stable. Client applications should not need to know which credential is active, which session is active, which upstream provider is active, whether rotation occurred, or how credentials were obtained. The local gateway is the stable abstraction layer.

### 4.2 Session management is first-class

The UI must provide a manual session-management workflow: display status, manually enter/replace, validate, save, show last validation time, show last error. Never assume a session credential can be automatically refreshed. Automatic session handling must be disableable when unsupported.

### 4.3 Manual credential entry is mandatory

The user must always be able to paste a credential directly into the UI. Automatic discovery is optional and must degrade gracefully. The user must never need to manually edit `active_key.txt` for normal operation.

### 4.4 Rotation must support multiple triggers

Rotation must support: manual trigger, usage-threshold trigger, provider quota/rate-limit trigger, invalid/expired credential trigger, and scheduled trigger. Quota, rate-limit, and invalid-credential failures must be distinguishable from each other. Do not rely on a single local token counter. Every automatic retry must have a bounded retry count, logging, cooldown/backoff, and clear failure reporting.

### 4.5 Providers and models are configurable

Do not hard-code one upstream provider. A provider record supports: id, name, protocol, base_url, auth_type, enabled, health, metadata. Models belong to providers and support: display_name, context_window, capabilities, enabled, default/fallback role. The adapter pattern makes additional providers possible later.

### 4.6 No business logic in React

All sensitive operations (credential management, rotation, session auth, provider API calls) go through backend services. React components call the backend API via the centralized `api` client. This separation must remain intact.

### 4.7 Secrets are never exposed

Never commit API keys, commit session credentials, print full credentials to logs, render full credentials in the UI, place credentials in source code, or expose credentials through frontend state. Use masked representations (e.g., `************AB12`). All secret-bearing operations go through a backend-controlled abstraction.

### 4.8 Application state uses the database

Application state must not rely on the old file-based IPC architecture (shared files on disk for inter-process communication). Use SQLite for structured state. Legacy compatibility files may exist temporarily during migration but are not the long-term solution.

---

## 5. Legacy Code

All existing Python scripts under `legacy/` are **reference material**, not the final architecture.

Preserved files:
- `OpusGateway.py` — HTTP proxy with usage tracking (the normal gateway reference)
- `OpusControlPanel.py` — Tkinter GUI controller
- `KeyBinder.py` — Usage watcher and rotation signal
- `key_poller.py` — Automatic key polling wrapper
- `pull_latest_key.py` — Key discovery from provider dashboard
- `rotate_now.py` — Manual key rotation

Do not delete or overwrite legacy files. Study them, document findings, and incrementally replace their behavior with cleaner backend services.

**Known legacy issues (from Phase 0):**
- Hardcoded session cookie in 3 files — almost certainly expired
- Inconsistent thresholds across 4 files (270K, 300K, 1.35M, 1.5M)
- Duplicated API client code in 3 files
- No session expiry detection
- File-based IPC is slow and fragile
- SSL verification disabled everywhere

---

## 6. Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | React + TypeScript | Vite build |
| Backend | FastAPI (Python 3.11+) | Async, service-oriented |
| Database | SQLite | Via aiosqlite, WAL mode |
| Migrations | Alembic | Deterministic upgrades |
| Realtime | WebSocket or SSE | Phase 6 |
| Packaging | Electron | Phase 7 (later) |

---

## 7. Phased Roadmap

### Phase 1 — Foundation
- **1.1** Project scaffolding — **DONE**
- **1.2** Data foundation + AGENT.md update — **CURRENT**
- **1.3** Configuration foundation hardening
- **1.4** Gateway service architecture

### Phase 2 — Credential & Session Management
- **2.1** Manual credential management
- **2.2** Session management
- **2.3** Provider dashboard client
- **2.4** Automatic credential discovery

### Phase 3 — Gateway & Rotation
- **3.1** Gateway proxy migration
- **3.2** Usage tracking
- **3.3** Rotation engine
- **3.4** Failure-triggered rotation
- **3.5** Recovery and rollback

### Phase 4 — Providers & Models
- **4.1** Provider management
- **4.2** Model management
- **4.3** Routing and fallbacks

### Phase 5 — Frontend
- **5.1** Functional dashboard
- **5.2** Credentials/session UI
- **5.3** Provider/model UI
- **5.4** Logs and history
- **5.5** Settings

### Phase 6 — Realtime & Reliability
- **6.1** WebSocket/SSE
- **6.2** Health monitoring
- **6.3** Structured events
- **6.4** Resilience testing

### Phase 7 — Electron
- **7.1** Electron wrapper
- **7.2** Tray integration
- **7.3** Startup management
- **7.4** Native notifications
- **7.5** Windows packaging

---

## 8. Development Workflow

The project is built in **phases**. Each phase has **subphases**. Each subphase has **one concrete task**.

Do not jump ahead. Do not implement future phases unless explicitly instructed.

After completing a subphase:
1. Explain what changed
2. List files changed
3. Report tests performed and results
4. Report failures
5. Report unresolved questions
6. Provide a short phase summary
7. **STOP**

Do not automatically continue to the next phase. The user will review the summary before requesting the next task.

---

## 9. Testing Rule

Every backend service must have automated tests. Tests must use isolated temporary databases. Do not test real external API calls. Do not use real secrets or production credentials.

At minimum test: provider CRUD, model CRUD, session management, credential management, rotation events, usage tracking, health checks, settings, gateway lifecycle, failure recovery.

Do not claim a feature works without testing it.

---

## 10. Code Quality

Prefer: typed Python, typed TypeScript, small modules, explicit interfaces, dependency injection where useful, centralized configuration, clear error boundaries, testable services, minimal global state.

Avoid: giant files, duplicated business logic, UI directly manipulating files, UI directly spawning arbitrary subprocesses, hidden background threads, undocumented magic constants.

---

## 11. UI Design

The UI must feel like a serious desktop-grade developer tool. It must NOT look like a generic AI dashboard.

Prefer: strong visual hierarchy, restrained color system, excellent typography, dense but readable information layout, purposeful motion, meaningful status indicators, clear error states, keyboard accessibility.

Avoid: excessive gradients, meaningless glassmorphism, random glowing borders, oversized rounded cards, fake dashboard metrics, unnecessary animations, emoji-heavy interfaces, generic "AI SaaS" styling.

Use design skills in `.agent/skills/` as guidance, not as decorative additions.

---

## 12. Error Handling

Every failure must be observable. Errors should contain: operation, timestamp, category, human-readable message, technical details where safe, retryability, suggested action. Never silently swallow failures.

---

## 13. Logging

Use structured logging internally. Log events such as: `gateway.started`, `gateway.stopped`, `gateway.failed`, `session.validated`, `session.failed`, `credential.fetched`, `credential.changed`, `rotation.started`, `rotation.completed`, `rotation.failed`, `provider.tested`, `provider.failed`. Never log secrets.

---

## 14. Backward Compatibility

During migration: preserve legacy behavior where reasonable, preserve existing file formats temporarily, allow migration from legacy state, avoid destructive changes, document incompatibilities. Legacy compatibility files may exist temporarily but are not the long-term solution.

---

## 15. Definition of Success

The finished system should allow the user to: start/stop/restart the gateway, see gateway health, configure providers, configure models, configure a session credential, replace an expired session manually, enter a credential manually, fetch a credential automatically when supported, rotate credentials manually and automatically, observe usage, inspect logs, test endpoints, and use the same stable local gateway from different AI clients — without manually editing internal files.

---

## 16. Most Important Instruction

Do not treat the legacy implementation as correct simply because it previously worked. Treat it as historical implementation plus behavioral reference. Verify every important behavior. Where the old implementation is fragile, replace it with a cleaner design. The objective is not to reproduce the old code — it is to produce a reliable system that preserves the useful behavior while removing the fragile architecture.
