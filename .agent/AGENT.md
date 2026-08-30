# Gateway Control Center — Agent Contract

## 1. Project Identity

Project:
Gateway Control Center

Purpose:
Build a polished local web-based control center for a Windows-hosted AI API gateway.

The application must provide a stable local gateway interface while allowing the user to manage credentials, sessions, providers, models, usage, rotation, health, logs, and configuration from a unified UI.

The current repository contains a legacy Python implementation. The legacy implementation is the reference for existing behavior, but it is not the final architecture.

---

# 2. Primary Goal

Transform the existing Python/Tkinter gateway-management system into:

    React + TypeScript frontend
              ↓
          FastAPI backend
              ↓
       Gateway / key-management services

The first milestone is a fully functional LOCAL WEB APPLICATION.

Electron packaging is a later phase.

Do NOT begin Electron implementation until the web application is functionally complete and tested.

---

# 3. Critical Scope

The new system MUST provide:

1. Gateway lifecycle management
2. Gateway health/status
3. Session management
4. Manual credential entry
5. Automatic credential retrieval when supported
6. Automatic credential rotation
7. Manual rotation
8. Provider management
9. Model management
10. Usage tracking
11. Logs
12. Health checks
13. Stable local API endpoint
14. Configuration management
15. Safe secret handling

The low-latency gateway implementation is OUT OF SCOPE.

Do not port, depend on, optimize, or reproduce the low-latency gateway.

Use the normal gateway implementation as the legacy reference.

---

# 4. Legacy Code Rule

All existing Python scripts provided by the user are considered LEGACY SOURCE.

Preserve them.

Do not delete them.

Do not overwrite them during the migration.

The legacy implementation must first be studied and documented.

The legacy code should be copied under:

    legacy/

The following files are important reference components:

    OpusGateway.py
    KeyBinder.py
    rotate_now.py
    pull_latest_key.py
    key_poller.py

The legacy Tkinter controller is also reference material but must NOT become the foundation of the new UI.

---

# 5. Existing Architecture

The current legacy architecture roughly consists of:

    Tkinter Control Panel
             ↓
    process management
             ↓
    Gateway
    KeyBinder
    Rotation script
    Key polling script
             ↓
    active credential file
             ↓
    upstream provider

The legacy gateway reloads its active credential periodically and tracks token usage.

The legacy key manager watches usage and signals rotation.

The legacy rotation process creates a new credential and updates the active credential.

The legacy polling process attempts to retrieve the latest credential.

The new architecture should eventually consolidate these responsibilities into explicit backend services.

---

# 6. Required New Architecture

Backend:

    FastAPI
    Python
    service-oriented internal architecture

Frontend:

    React
    TypeScript
    Vite

State:

    SQLite for structured application state

Realtime:

    WebSocket or Server-Sent Events

Later:

    Electron

Recommended backend services:

    GatewayManager
    KeyManager
    SessionManager
    RotationManager
    ProviderManager
    ModelManager
    UsageManager
    HealthManager
    ProcessManager

---

# 7. Stable Local Gateway Principle

The local client-facing endpoint must remain stable.

Client applications should not need to know:

- which credential is active
- which session is active
- which upstream provider is active
- whether rotation occurred
- whether the provider endpoint changed
- how credentials were obtained

The local gateway acts as the stable abstraction layer.

---

# 8. Session Management

The UI MUST provide a manual session-management workflow.

Required capabilities:

- display session status
- manually enter/replace session credential
- validate session
- save session
- show last validation time
- show last successful credential fetch
- show last error
- disable automatic session handling when unsupported

Never assume that a session credential can automatically be refreshed.

First determine the authentication mechanism from the legacy implementation and current provider behavior.

---

# 9. Credential Management

The UI MUST support:

### Automatic mode

Discover and activate credentials automatically when possible.

### Manual mode

Allow the user to paste a credential directly into the UI.

### Hybrid mode

Allow automatic management with manual override.

The user must never need to manually edit:

    active_key.txt

for normal operation.

The backend may maintain compatibility with the legacy file during migration.

---

# 10. Rotation Rules

Rotation MUST eventually support:

1. manual rotation
2. usage-threshold rotation
3. provider quota/rate-limit-triggered rotation
4. invalid/expired credential-triggered rotation
5. automatic latest-credential discovery

Do not rely on a single local token counter.

A request-level failure from the upstream provider is an important rotation signal when it clearly indicates credential exhaustion or invalidity.

Avoid infinite retry loops.

Every automatic retry must have:

- a bounded retry count
- logging
- cooldown/backoff
- clear failure reporting

---

# 11. Provider System

Providers must be configurable.

Do not hard-code one upstream provider into the UI architecture.

A provider record should support:

    id
    name
    protocol
    base_url
    authentication configuration
    models
    enabled
    health
    metadata

Initial protocols:

    openai-completions
    anthropic-messages

The architecture should make additional adapters possible later.

---

# 12. Model System

Models belong to providers.

A model record should support:

    id
    provider_id
    display_name
    context_window
    capabilities
    enabled
    default
    metadata

Support:

- default model
- fallback model
- model health
- manual model selection

Do not assume that every provider exposes the same model capabilities.

---

# 13. Secrets

NEVER:

- commit API keys
- commit session credentials
- print full credentials to logs
- render full credentials in the UI
- place credentials in source code
- expose credentials through frontend state unnecessarily

Use masked representations.

Example:

    ************AB12

All secret-bearing operations must go through a backend-controlled abstraction.

---

# 14. UI Design Requirements

The UI must feel like a serious desktop-grade developer tool.

It must NOT look like a generic AI dashboard.

Use the installed design skills in:

    .agent/skills/

The project should explicitly use:

- anthropic frontend design skill for overall product quality
- hallmark for avoiding generic AI-slop patterns
- impeccable for final visual polish
- animation-related skills where appropriate

Before implementing major UI sections, inspect the relevant skill instructions.

Do not blindly combine every design technique.

Use design skills as guidance, not as decorative additions.

---

# 15. UI Principles

Prefer:

- strong visual hierarchy
- restrained color system
- excellent typography
- dense but readable information layout
- purposeful motion
- meaningful status indicators
- clear error states
- excellent loading states
- keyboard accessibility
- responsive layout
- consistent spacing
- clear destructive-action confirmation

Avoid:

- excessive gradients
- meaningless glassmorphism
- random glowing borders
- oversized rounded cards
- fake dashboard metrics
- unnecessary animations
- emoji-heavy interfaces
- generic "AI SaaS" styling

---

# 16. Dashboard Requirements

The dashboard should eventually communicate:

Gateway:
- running/stopped
- endpoint
- health

Credential:
- active/inactive
- source
- last change

Session:
- valid/invalid
- last validation

Usage:
- used
- remaining
- percentage
- threshold

Rotation:
- automatic/manual
- next trigger
- last rotation
- last error

Provider:
- health
- latency
- active model

Logs:
- recent activity
- warnings
- failures

---

# 17. Error Handling

Every failure must be observable.

Errors should contain:

- operation
- timestamp
- category
- human-readable message
- technical details where safe
- retryability
- suggested action where appropriate

Never silently swallow failures.

---

# 18. Logging

Use structured logging internally.

Log events such as:

    gateway.started
    gateway.stopped
    gateway.failed
    session.validated
    session.failed
    credential.fetched
    credential.changed
    rotation.started
    rotation.completed
    rotation.failed
    provider.tested
    provider.failed
    model.tested

Never log secrets.

---

# 19. Backward Compatibility

During migration:

- preserve legacy behavior where reasonable
- preserve existing file formats temporarily
- allow migration from legacy state
- avoid destructive changes
- document incompatibilities

The migration should be incremental.

---

# 20. Testing Rule

Every backend service must eventually have automated tests.

At minimum test:

- provider configuration
- model configuration
- session replacement
- credential activation
- credential polling
- rotation
- usage accounting
- gateway restart
- gateway failure recovery
- invalid credential
- rate-limit response
- network failure

Do not claim a feature works without testing it.

---

# 21. Development Workflow

The project is built in PHASES.

Each phase has smaller SUBPHASES.

Each subphase has ONE concrete task.

Do not jump ahead.

Do not implement future phases unless explicitly instructed.

After completing a subphase:

1. explain what changed
2. list files changed
3. report tests performed
4. report failures
5. report unresolved questions
6. provide a short phase summary
7. STOP

Do not automatically continue to the next phase.

The user will provide the summary for review before requesting the next task.

---

# 22. Initial Development Rule

Before modifying code:

1. inspect the complete repository
2. inspect all legacy scripts
3. inspect .agent/skills
4. map dependencies
5. document the existing behavior
6. identify assumptions
7. identify broken functionality
8. identify authentication/session dependencies

Do not begin the migration based on filename assumptions.

---

# 23. Repository Exploration Rule

The user may provide a Git repository containing related functionality.

If instructed to clone a repository:

- clone it into the designated workspace
- inspect it
- do not blindly copy its code
- document relevant functionality
- preserve licensing information
- distinguish reused code from newly written code

The agent must never silently replace the current project with the cloned repository.

---

# 24. Code Quality

Prefer:

- typed Python
- typed TypeScript
- small modules
- explicit interfaces
- dependency injection where useful
- centralized configuration
- clear error boundaries
- testable services
- minimal global state

Avoid:

- giant files
- duplicated business logic
- UI directly manipulating files
- UI directly spawning arbitrary subprocesses
- hidden background threads
- undocumented magic constants

---

# 25. Architecture Rule

Business logic must NOT live in React components.

React:
    presentation + interaction

FastAPI:
    API boundary

Services:
    business logic

Adapters:
    external integrations

Storage:
    persistence

This separation must remain intact.

---

# 26. Database Rule

Do not introduce SQLite merely for decoration.

Use it when structured state needs persistence.

Suitable candidates include:

- providers
- models
- session metadata
- rotation history
- provider health history
- application settings
- event history

Do not store raw secrets in plaintext unless explicitly justified.

---

# 27. Electron Rule

Electron is a LATER phase.

Do not introduce Electron during the initial functional-web phase.

Once the web application is stable:

    React + FastAPI
          ↓
      Electron

Electron should provide:

- tray integration
- startup
- native notifications
- lifecycle management
- packaging
- native Windows integration

Do not duplicate backend business logic inside Electron.

---

# 28. Scope Discipline

Do not:

- redesign unrelated functionality
- add unnecessary AI features
- add chat functionality
- add a prompt playground
- add cloud deployment
- add authentication for remote users
- add unnecessary analytics
- add unrelated automation

The project is a gateway control center.

---

# 29. Definition of Success

The finished system should allow the user to:

1. start the gateway
2. stop the gateway
3. see gateway health
4. configure providers
5. configure models
6. configure a session credential
7. replace an expired session manually
8. enter a credential manually
9. fetch a credential automatically when supported
10. rotate credentials manually
11. rotate credentials automatically
12. observe usage
13. inspect logs
14. test endpoints
15. use the same stable local gateway from different AI clients

without manually editing internal files.

---

# 30. Most Important Instruction

Do not treat the legacy implementation as correct simply because it previously worked.

Treat it as:

    historical implementation + behavioral reference

Verify every important behavior.

Where the old implementation is fragile, replace it with a cleaner design.

The objective is not to reproduce the old code.

The objective is to produce a reliable system that preserves the useful behavior while removing the fragile architecture.