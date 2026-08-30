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
│  capabilities│       │  is_fallback │       │    Event     │
│  metadata    │       │  created_at  │       │  (log)       │
│  created_at  │       │  updated_at  │       │              │
│  updated_at  │       └──────────────┘       │  event_type  │
└──────┬───────┘                              │  severity    │
       │                                      │  message     │
       │                                      │  details     │
       ├────1:N────┌──────────────┐           │  created_at  │
       │           │  Credential  │           └──────────────┘
       │           │              │
       │           │  id          │           ┌──────────────┐
       │           │  provider_id │           │ HealthCheck  │
       │           │  key_masked  │           │              │
       │           │  secret_ref  │           │  target_type │
       │           │  source      │           │  target_id   │
       │           │  state       │           │  status      │
       │           │  validation  │           │  latency_ms  │
       │           │  usage_*     │           │  details     │
       │           │  activated_at│           │  checked_at  │
       │           │  created_at  │           └──────────────┘
       │           └──────┬───────┘
       │                  │
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
       │                  └─1:N──┌──────────────────────┐
       │                         │  CredentialEvent     │
       │                         │                      │
       │                         │  event_type          │
       │                         │  credential_id       │
       │                         │  provider_id         │
       │                         │  status              │
       │                         │  failure_reason      │
       │                         │  created_at          │
       │                         └──────────────────────┘
       │
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
| capabilities_json | Text | JSON declaring provider capabilities (see below) |
| created_at | Text | ISO timestamp |
| updated_at | Text | ISO timestamp |

**Provider capabilities** (`capabilities_json`):
```json
{
  "credential_validation": true,
  "credential_discovery": false,
  "credential_generation": false,
  "credential_revocation": false
}
```

These capabilities are provider-specific. The application must NOT assume
they exist for all providers.

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
| source | String(32) | `manual`, `provider-assisted` |
| state | String(32) | `active`, `inactive`, `expired`, `invalid`, `revoked` |
| validation_status | String(32) | `valid`, `invalid`, `expired`, `unknown` |
| last_validated | Text | ISO timestamp of last validation |
| last_validation_error | Text | Last validation error message |
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

Sessions are NOT the same as API credentials. The application treats session
state, API credential state, and provider configuration as separate concepts.

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

### CredentialEvent

A record of a credential lifecycle event. This replaces the previous
"rotation_events" table with a broader set of event types that reflect
the monitor-first, user-controlled credential lifecycle.

| Field | Type | Description |
|-------|------|-------------|
| id | String(12) | Primary key |
| provider_id | String(12) | FK → providers.id (SET NULL on delete) |
| credential_id | String(12) | FK → credentials.id (SET NULL on delete) |
| event_type | String(64) | Event type (indexed, see below) |
| status | String(32) | `success`, `failed`, `timeout`, `skipped` |
| failure_reason | Text | Why the event failed (if applicable) |
| duration_ms | Integer | How long the operation took |
| details_json | Text | Additional context (JSON) |
| created_at | Text | ISO timestamp |

**Event types:**
- `created` — credential record created
- `imported_manually` — user pasted a credential
- `validated` — credential validated against provider
- `activated` — credential set as active
- `deactivated` — credential deactivated
- `expired` — credential expired (detected by monitoring)
- `invalid` — credential rejected by provider
- `replacement_requested` — user requested replacement
- `replacement_completed` — new credential activated after replacement
- `warning_triggered` — monitoring detected a condition requiring attention
- `provider_assisted_rotation` — provider-specific rotation (where supported)

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
| event_type | String(128) | Event type (indexed): `gateway.started`, `credential.warning`, etc. |
| severity | String(16) | `debug`, `info`, `warn`, `error`, `critical` |
| message | Text | Human-readable message |
| details_json | Text | Additional context (JSON) |
| created_at | Text | ISO timestamp |

### HealthCheck

A health check result for a gateway, provider, session, or credential.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Auto-increment primary key |
| target_type | String(32) | `gateway`, `provider`, `session`, `credential` (indexed) |
| target_id | String(12) | FK to provider/session/credential, or `local` for gateway |
| status | String(32) | `healthy`, `degraded`, `unhealthy` |
| latency_ms | Float | Response latency in milliseconds |
| details_json | Text | Additional context (JSON) |
| checked_at | Text | ISO timestamp |

## Design Decisions

### Monitor-first credential lifecycle

The default credential lifecycle is: monitor → detect → warn → user action →
validate → activate → continue monitoring. The application does NOT silently
generate, delete, revoke, replace, or rotate credentials.

### Credential events replace rotation events

The `credential_events` table replaces the previous `rotation_events` table.
The broader event types reflect the monitor-first policy: most events are
about credential health and user actions, not automatic rotation.

### Provider capabilities are declared, not assumed

The `capabilities_json` field on providers declares what credential-management
workflows the provider supports. The application must NOT assume these
capabilities exist for all providers.

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
Credential events and usage snapshots use SET NULL to preserve history.

### Short IDs

Primary keys are 12-character hex strings (UUID prefixes) rather than
auto-incrementing integers. This avoids sequential ID enumeration and
works well with the frontend's display needs.

## Credential Lifecycle State vs Validation State

These are two independent dimensions of credential health:

**Lifecycle state** (`state` field) tracks whether the credential is in use:
- `inactive` — stored but not in use (default for new credentials)
- `active` — currently in use by the gateway
- `expired` — past its validity period
- `invalid` — rejected by the provider
- `revoked` — manually revoked

**Validation state** (`validation_status` field) tracks the result of the
last validation attempt:
- `unknown` — not yet validated (default for new credentials)
- `valid` — confirmed working with the provider
- `invalid` — rejected by the provider
- `expired` — provider reports the credential has expired

A credential can be `active` with `unknown` validation status (we're using
it but haven't checked if it's still valid). These states are updated
independently by different operations.
