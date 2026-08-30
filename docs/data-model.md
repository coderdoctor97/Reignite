# Gateway Control Center — Data Model

## Entity Relationship Diagram

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│   Provider   │──1:N──│    Model     │       │   Settings   │
│              │       │              │       │  (key-value) │
│  id          │       │  id          │       │              │
│  name        │       │  provider_id │       │  key         │
│  protocol    │       │  model_id    │       │  value       │
│  base_url    │       │  display_name│       │  updated_at  │
│  auth_type   │       │  context_win │       └──────────────┘
│  enabled     │       │  capabilities│
│  health_stat │       │  is_default  │       ┌──────────────┐
│  metadata    │       │  is_fallback │       │    Event     │
│  created_at  │       │  created_at  │       │  (log)       │
│  updated_at  │       │  updated_at  │       │              │
└──────┬───────┘       └──────────────┘       │  event_type  │
       │                                      │  severity    │
       │                                      │  message     │
       ├────1:N────┌──────────────┐           │  details     │
       │           │  Credential  │           │  created_at  │
       │           │              │           └──────────────┘
       │           │  id          │
       │           │  provider_id │           ┌──────────────┐
       │           │  key_masked  │           │ HealthCheck  │
       │           │  secret_ref  │           │              │
       │           │  source      │           │  target_type │
       │           │  state       │           │  target_id   │
       │           │  usage_*     │           │  status      │
       │           │  activated_at│           │  latency_ms  │
       │           │  created_at  │           │  details     │
       │           └──────┬───────┘           │  checked_at  │
       │                  │                   └──────────────┘
       │                  ├─1:N──┌──────────────────┐
       │                  │      │  UsageSnapshot   │
       │                  │      │                  │
       │                  │      │  credential_id   │
       │                  │      │  provider_id     │
       │                  │      │  input_tokens    │
       │                  │      │  output_tokens   │
       │                  │      │  total_tokens    │
       │                  │      │  remaining       │
       │                  │      │  snapshot_at     │
       │                  │      └──────────────────┘
       │                  │
       ├────1:N────┌──────────────┐
       │           │   Session    │
       │           │              │
       │           │  id          │
       │           │  provider_id │
       │           │  session_mask│
       │           │  secret_ref  │
       │           │  status      │
       │           │  last_valid  │
       │           │  created_at  │
       │           └──────────────┘
       │
       └────1:N────┌──────────────────┐
                   │  RotationEvent   │
                   │                  │
                   │  id              │
                   │  provider_id     │
                   │  trigger_type    │
                   │  old_credential  │
                   │  new_credential  │
                   │  status          │
                   │  failure_reason  │
                   │  duration_ms     │
                   │  created_at      │
                   └──────────────────┘
```

## Entities

### Provider

Represents an upstream API endpoint/integration (e.g., opus.abhibots.com).

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key (UUID prefix) |
| name | String(255) | Human-readable name |
| protocol | String(64) | Integration protocol: `openai-completions`, `anthropic-messages` |
| base_url | Text | Upstream API base URL |
| auth_type | String(32) | Authentication type: `api-key`, `session-cookie` |
| enabled | Boolean | Whether this provider is active |
| health_status | String(32) | `healthy`, `degraded`, `unhealthy`, `unknown` |
| last_health_check | Text | ISO timestamp of last health check |
| metadata_json | Text | Arbitrary JSON metadata |
| created_at | Text | ISO timestamp |
| updated_at | Text | ISO timestamp |

**Owns:** credentials, sessions, models

### Model

A model offered by a provider. Supports default and fallback routing.

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key |
| provider_id | String(12) | FK → providers.id (CASCADE delete) |
| display_name | String(255) | Human-readable name |
| model_id | String(255) | Upstream model identifier |
| context_window | Integer | Context window size in tokens |
| capabilities | Text | JSON array: `["chat","completion","vision"]` |
| enabled | Boolean | Whether this model is available |
| is_default | Boolean | Default model for the provider |
| is_fallback | Boolean | Fallback model when default fails |
| metadata_json | Text | Arbitrary JSON metadata |
| created_at | Text | ISO timestamp |
| updated_at | Text | ISO timestamp |

### Credential

An API key or bearer token for a provider. The actual secret is NOT stored
here — only a reference into the SecretStore and a masked display value.

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key |
| provider_id | String(12) | FK → providers.id (CASCADE delete) |
| key_masked | String(64) | Masked display value (e.g., `************AB12`) |
| secret_ref | String(255) | Reference ID into the SecretStore |
| source | String(32) | `manual`, `auto-discovered`, `rotated` |
| state | String(32) | `active`, `expired`, `revoked`, `rotating` |
| usage_input | Integer | Input tokens consumed by this credential |
| usage_output | Integer | Output tokens consumed by this credential |
| usage_total | Integer | Total tokens consumed |
| activated_at | Text | When this credential became active |
| deactivated_at | Text | When this credential was deactivated |
| created_at | Text | ISO timestamp |
| updated_at | Text | ISO timestamp |

**Secret storage:** The actual API key is stored in the `SecretStore`
(`app/core/secrets.py`), not in the database. The `secret_ref` field
points to the secret's location in the store.

### Session

A session credential for provider dashboard access (e.g., web session cookie).
Used for operations like key management that require dashboard authentication.

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key |
| provider_id | String(12) | FK → providers.id (CASCADE delete) |
| session_masked | String(128) | Masked display value |
| secret_ref | String(255) | Reference ID into the SecretStore |
| status | String(32) | `valid`, `invalid`, `expired`, `unknown` |
| last_validated | Text | ISO timestamp of last validation |
| last_validation_error | Text | Last validation error message |
| last_successful_fetch | Text | ISO timestamp of last successful credential fetch |
| metadata_json | Text | Arbitrary JSON metadata |
| created_at | Text | ISO timestamp |
| updated_at | Text | ISO timestamp |

### RotationEvent

A record of a credential rotation attempt.

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key |
| provider_id | String(12) | FK → providers.id (SET NULL on delete) |
| trigger_type | String(32) | What triggered the rotation (see below) |
| old_credential_id | String(12) | ID of the credential that was replaced |
| new_credential_id | String(12) | ID of the replacement credential |
| status | String(32) | `success`, `failed`, `timeout`, `skipped` |
| failure_reason | Text | Why the rotation failed (if applicable) |
| duration_ms | Integer | How long the rotation took |
| details_json | Text | Additional context (JSON) |
| created_at | Text | ISO timestamp |

**Trigger types:**
- `manual` — user-initiated from the UI
- `threshold` — usage threshold exceeded
- `rate-limit` — upstream rate limit hit
- `invalid-credential` — upstream rejected the credential
- `quota-exhausted` — provider quota used up
- `scheduled` — timer-based rotation
- `recovery` — automatic recovery after failure

### UsageSnapshot

A point-in-time snapshot of token usage. Captured periodically or on each
request for historical tracking.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Auto-increment primary key |
| credential_id | String(12) | FK → credentials.id (SET NULL on delete) |
| provider_id | String(12) | FK → providers.id (SET NULL on delete) |
| input_tokens | Integer | Input tokens in this snapshot |
| output_tokens | Integer | Output tokens in this snapshot |
| total_tokens | Integer | Total tokens |
| remaining | Integer | Tokens remaining |
| limit | Integer | Token limit for this credential |
| snapshot_at | Text | ISO timestamp |

### Setting

Application settings (key-value store). Not used for secrets.

| Field | Type | Description |
|-------|------|-------------|
| key | String(255) | Primary key |
| value | Text | Setting value |
| updated_at | Text | ISO timestamp |

### Event

Structured application event log.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Auto-increment primary key |
| event_type | String(128) | Event type (indexed): `gateway.started`, `rotation.completed`, etc. |
| severity | String(16) | `debug`, `info`, `warn`, `error`, `critical` |
| message | Text | Human-readable message |
| details_json | Text | Additional context (JSON) |
| created_at | Text | ISO timestamp |

### HealthCheck

A health check result for a gateway, provider, or session.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Auto-increment primary key |
| target_type | String(32) | `gateway`, `provider`, `session` (indexed) |
| target_id | String(12) | FK to provider/session, or `local` for gateway |
| status | String(32) | `healthy`, `degraded`, `unhealthy` |
| latency_ms | Float | Response latency in milliseconds |
| details_json | Text | Additional context (JSON) |
| checked_at | Text | ISO timestamp |

## Design Decisions

### Timestamps as ISO strings

All timestamps are stored as ISO 8601 strings (UTC) rather than SQLite's
native TIMESTAMP type. This avoids SQLite's type affinity quirks and makes
the data portable and human-readable.

### Secrets separated from metadata

Actual secret values (API keys, session cookies) are never stored in the
database. Only references (`secret_ref`) and masked display values are
stored. This keeps the database safe to inspect, backup, and share without
exposing credentials.

### JSON columns for flexible metadata

Fields ending in `_json` store serialized JSON strings. This provides
schema flexibility for provider-specific data, model capabilities, and
event details without requiring schema migrations for every new field.

### Cascade deletes

Deleting a provider cascades to its credentials, sessions, and models.
Rotation events and usage snapshots use SET NULL to preserve history.

### Short IDs

Primary keys are 12-character hex strings (UUID prefixes) rather than
auto-incrementing integers. This avoids sequential ID enumeration and
works well with the frontend's display needs.
