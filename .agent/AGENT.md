# Gateway Control Center — Agent Contract

## 1. Project Identity

**Project:** Gateway Control Center

**Purpose:** Build a polished local web-based control center for a Windows-hosted AI API gateway.

The application is primarily a **Gateway + Credential Monitor + Management Center**. It is NOT primarily an automatic credential-rotation system.

**Target platform:** Windows (primary). A local Windows developer/system utility.

**Current status:** Phase 1.3 — Architecture/policy alignment.

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
- **Services:** all business logic, credential handling, monitoring, health checks
- **Repositories:** data persistence — CRUD against SQLite, no business rules
- **Adapters:** external system integrations (provider APIs, dashboard APIs)
- **Storage:** SQLite database, secret store abstraction

React must never directly read credential files, manipulate gateway processes, call provider dashboard APIs, perform credential operations, or manage secrets. Those responsibilities belong in the backend.

---

## 3. Credential Policy

### 3.1 Default credential lifecycle

The default credential lifecycle is **monitor-first, user-controlled**:

```
MONITOR
   ↓
DETECT CONDITION
   ↓
WARN USER
   ↓
USER ACTION
   ↓
VALIDATE NEW CREDENTIAL
   ↓
ACTIVATE NEW CREDENTIAL
   ↓
CONTINUE MONITORING
```

The application must NOT silently generate, delete, revoke, replace, or rotate credentials unless a specific provider adapter explicitly supports such a workflow AND the user explicitly enables and initiates it.

Provider-specific credential-management capabilities must never become global assumptions.

### 3.2 Three credential modes

**A. MANUAL**

The user enters a credential directly into the UI. This is the universal fallback and must always work regardless of provider capabilities.

**B. MONITOR**

The application monitors:
- token usage
- provider health
- request errors (rate limits, quota exhaustion, invalid credential)
- session validity

and warns the user when action may be required. Monitoring is universal — it works for all providers.

**C. PROVIDER-SPECIFIC ASSISTANCE**

Some providers may support additional credential-management workflows (discovery, generation, validation, revocation). These MUST be implemented as provider-specific capabilities. They are optional. They MUST NOT be assumed to exist for all providers.

### 3.3 Credential terminology

The application should talk to the user in terms such as:
- Credential Health
- Credential Warning
- Replace Credential
- Validate Credential
- Activate Credential

rather than presenting "Auto Rotate" as the main feature.

"Rotation" is an EVENT TYPE in the data model, not the core business model.

### 3.4 What the application must NOT do silently

The application must NOT silently:
- generate credentials
- delete credentials
- revoke credentials
- replace credentials
- rotate credentials

unless a specific provider adapter explicitly supports such a workflow and the user explicitly enables and initiates it.

---

## 4. Session Policy

Sessions are NOT the same as API credentials.

The application must treat:
- session state
- API credential state
- provider configuration

as separate concepts.

The user must be able to manually replace an expired/changed session value.

The system must show:
- session status
- last validation time
- last failure
- failure reason

Automatic session renewal must NOT be assumed. The legacy system used a hardcoded session cookie that expired — the new system must handle this gracefully by detecting expiry and prompting the user.

---

## 5. Stable Gateway Policy

The local gateway is the stable abstraction layer.

Client applications should not need to know:
- which provider is active
- which credential is active
- how credentials were obtained
- whether a credential was replaced
- which provider endpoint changed

The client-facing endpoint should remain stable while backend provider configuration changes.

---

## 6. Scope

### In scope

1. Gateway lifecycle management (start/stop/restart)
2. Gateway health and status
3. Credential monitoring (usage, health, validity)
4. Credential warnings (threshold, rate-limit, quota, expiry, invalid)
5. Manual credential entry (universal)
6. Manual credential replacement
7. Credential validation
8. Session management (manual entry, validation, replacement)
9. Provider management (configurable, extensible)
10. Model management (per-provider, with defaults and fallbacks)
11. Usage tracking (current, historical, per-credential)
12. Structured logs and events
13. Health checks (gateway, provider, session, credential)
14. Stable local API endpoint
15. Configuration management
16. Safe secret handling
17. Provider-specific credential workflows (where supported, user-initiated)

### Out of scope as defaults

- Automatic credential generation (provider-specific only)
- Automatic credential replacement (provider-specific only, user-initiated)
- Silent credential deletion or revocation
- **Low-latency gateway** — explicitly excluded
- Cloud deployment
- Remote user authentication
- Chat/prompt playground
- Unrelated AI features
- Electron until the web application is functionally complete and tested

---

## 7. Core Design Principles

### 7.1 Stable local gateway endpoint

The local client-facing endpoint must remain stable. Client applications should not need to know which credential is active, which session is active, which upstream provider is active, whether a credential was replaced, or how credentials were obtained. The local gateway is the stable abstraction layer.

### 7.2 Monitoring is universal, credential generation is provider-specific

All providers support monitoring (usage, health, errors). Credential generation, discovery, and revocation are provider-specific capabilities that must be explicitly declared and user-initiated.

### 7.3 Manual replacement is universal

The user must always be able to paste a credential directly into the UI. This works for every provider, always. The user must never need to manually edit `active_key.txt` for normal operation.

### 7.4 Sessions are not credentials

Session state, API credential state, and provider configuration are separate concepts. Never conflate them.

### 7.5 Providers and models are configurable

Do not hard-code one upstream provider. A provider record supports: id, name, protocol, base_url, auth_type, enabled, health, capabilities, metadata. Models belong to providers and support: display_name, context_window, capabilities, enabled, default/fallback role. The adapter pattern makes additional providers possible later.

### 7.6 No business logic in React

All sensitive operations go through backend services. React components call the backend API via the centralized `api` client. This separation must remain intact.

### 7.7 Secrets are never exposed

Never commit API keys, commit session credentials, print full credentials to logs, render full credentials in the UI, place credentials in source code, or expose credentials through frontend state. Use masked representations (e.g., `************AB12`). All secret-bearing operations go through a backend-controlled abstraction.

### 7.8 Application state uses the database

Application state must not rely on the old file-based IPC architecture. Use SQLite for structured state. Legacy compatibility files may exist temporarily during migration but are not the long-term solution.

---

## 8. UI Product Direction

The UI should NOT be designed around "rotation".

The primary visual hierarchy should be:

```
Gateway Health
Credential Health
Session Health
Provider Health
Usage
Warnings
Actions
```

The main action should generally be "Replace Credential" rather than "Auto Rotate".

The UI must feel like a serious desktop-grade developer tool. It must NOT look like a generic AI dashboard.

Prefer: strong visual hierarchy, restrained color system, excellent typography, dense but readable information layout, purposeful motion, meaningful status indicators, clear error states, keyboard accessibility.

Avoid: excessive gradients, meaningless glassmorphism, random glowing borders, oversized rounded cards, fake dashboard metrics, unnecessary animations, emoji-heavy interfaces, generic "AI SaaS" styling.

Use design skills in `.agent/skills/` as guidance for producing a polished, deliberate developer-tool UI.

---

## 9. Legacy Code

All existing Python scripts under `legacy/` are **reference material**, not the final architecture.

Preserved files:
- `OpusGateway.py` — HTTP proxy with usage tracking
- `OpusControlPanel.py` — Tkinter GUI controller
- `KeyBinder.py` — Usage watcher and rotation signal
- `key_poller.py` — Automatic key polling wrapper
- `pull_latest_key.py` — Key discovery from provider dashboard
- `rotate_now.py` — Manual key rotation

### Legacy behavior classification

**A. Useful and should survive:**
- Gateway proxy behavior (request forwarding, header replacement, streaming)
- Usage tracking from responses (input_tokens, output_tokens extraction)
- Atomic file writes for state persistence
- Crash-loop protection (max restarts per minute)
- Config persistence (JSON with atomic writes)
- The concept of a stable local endpoint

**B. Fragile and should be replaced:**
- File-based IPC (shared files for inter-process communication)
- Hardcoded session cookie in 3 files (expired, no detection)
- Inconsistent thresholds across 4 files (270K, 300K, 1.35M, 1.5M)
- Duplicated API client code in 3 files
- No session expiry detection
- SSL verification disabled everywhere
- No error differentiation (401, 403, 404, 500 treated the same)

**C. Provider-specific (not universal):**
- Dashboard API key management (list/create/delete keys)
- Automatic key polling from dashboard
- Session cookie authentication for dashboard access
- Key creation with daily token limits

**D. No longer desired:**
- Silent automatic credential generation without user control
- Deleting all credentials during rotation as a default strategy
- Token threshold as the sole rotation trigger
- Auto-rotation as the primary feature

---

## 10. Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | React + TypeScript | Vite build |
| Backend | FastAPI (Python 3.11+) | Async, service-oriented |
| Database | SQLite | Via SQLAlchemy + aiosqlite, WAL mode |
| Migrations | Alembic | Deterministic upgrades |
| Realtime | WebSocket or SSE | Phase 9 |
| Packaging | Electron | Phase 10 (later) |

---

## 11. Phased Roadmap

### Phase 1 — Foundation
- **1.1** Project scaffolding — **DONE**
- **1.2** Data model + migrations — **DONE**
- **1.3** Architecture/policy alignment — **CURRENT**

### Phase 2 — Gateway Foundation
- **2.1** Gateway lifecycle and health
- **2.2** Stable local endpoint
- **2.3** Gateway configuration

### Phase 3 — Credential Management
- **3.1** Manual credential management
- **3.2** Credential validation
- **3.3** Credential health state

### Phase 4 — Session Management
- **4.1** Session storage
- **4.2** Manual session replacement
- **4.3** Session validation

### Phase 5 — Monitoring and Warnings
- **5.1** Usage monitoring
- **5.2** Provider health monitoring
- **5.3** Credential warnings
- **5.4** Notification system

### Phase 6 — Provider System
- **6.1** Provider configuration
- **6.2** OpenAI-compatible providers
- **6.3** Anthropic-compatible providers
- **6.4** Provider capabilities

### Phase 7 — Model System
- **7.1** Model catalog
- **7.2** Default/fallback models
- **7.3** Model health

### Phase 8 — Provider-Specific Credential Workflows
- **8.1** Only where explicitly supported
- **8.2** User-controlled actions
- **8.3** Safe validation and rollback

### Phase 9 — UI Completion and Polish
- **9.1** Functional dashboard
- **9.2** All management views
- **9.3** Realtime updates
- **9.4** Visual polish using design skills

### Phase 10 — Electron Packaging
- **10.1** Electron wrapper
- **10.2** Tray integration
- **10.3** Startup management
- **10.4** Native notifications
- **10.5** Windows packaging

---

## 12. Development Workflow

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

## 13. Testing Rule

Every backend service must have automated tests. Tests must use isolated temporary databases. Do not test real external API calls. Do not use real secrets or production credentials.

Do not claim a feature works without testing it.

---

## 14. Code Quality

Prefer: typed Python, typed TypeScript, small modules, explicit interfaces, dependency injection where useful, centralized configuration, clear error boundaries, testable services, minimal global state.

Avoid: giant files, duplicated business logic, UI directly manipulating files, UI directly spawning arbitrary subprocesses, hidden background threads, undocumented magic constants.

---

## 15. Error Handling

Every failure must be observable. Errors should contain: operation, timestamp, category, human-readable message, technical details where safe, retryability, suggested action. Never silently swallow failures.

---

## 16. Logging

Use structured logging internally. Log events such as: `gateway.started`, `gateway.stopped`, `gateway.failed`, `session.validated`, `session.failed`, `credential.validated`, `credential.activated`, `credential.deactivated`, `credential.warning`, `provider.tested`, `provider.failed`. Never log secrets.

---

## 17. Backward Compatibility

During migration: preserve legacy behavior where reasonable, preserve existing file formats temporarily, allow migration from legacy state, avoid destructive changes, document incompatibilities. Legacy compatibility files may exist temporarily but are not the long-term solution.

---

## 18. Definition of Success

The finished system should allow the user to: start/stop/restart the gateway, see gateway health, monitor credential health, receive warnings when action is needed, replace credentials manually, validate credentials, manage sessions, configure providers and models, observe usage, inspect logs, and use the same stable local gateway from different AI clients — without manually editing internal files.

---

## 19. Most Important Instruction

Do not treat the legacy implementation as correct simply because it previously worked. Treat it as historical implementation plus behavioral reference. Verify every important behavior. Where the old implementation is fragile, replace it with a cleaner design. The objective is not to reproduce the old code — it is to produce a reliable system that preserves the useful behavior while removing the fragile architecture.
