# Legacy Session Flow — Analysis (Phase 4.1)

This document records how the legacy scripts use provider sessions. It is a
**read-only analysis**: the legacy implementation is not modified, and its
hard-coded session credential is **never copied** into the new application,
into Python/TypeScript source, `.env`, documentation, tests, or the database.

> ⚠️ The legacy scripts embed a live session cookie in source code. The actual
> value is intentionally NOT reproduced here — it is referenced only as
> `<REDACTED>`.

## Scripts analyzed

| File | Role |
|------|------|
| `legacy/KeyBinder.py` | Background watcher: reads gateway usage, writes a rotation flag when a threshold is hit |
| `legacy/rotate_now.py` | Manual key rotation: deletes all dashboard keys, creates one new key, updates `active_key.txt` |
| `legacy/pull_latest_key.py` | Fetches the newest dashboard key and writes it to `active_key.txt` |

## What session data they require

All three scripts hard-code a single session cookie:

```python
SESSION_COOKIE = "opus_session=<REDACTED>"
```

- **Cookie name:** `opus_session`
- **Cookie value:** a provider-issued opaque token (hard-coded string literal)
- **How it is sent:** as an HTTP cookie dict `{"opus_session": <value>}` on
  every request, together with a static browser-like `User-Agent` and an
  `Origin`/`Referer` header pair pointing at the dashboard.
- **Where it is stored:** directly in Python source. There is no config file,
  no secret store, and no expiry tracking.

## Where it is stored and how it is sent

- Sent via `requests.get/post/delete(..., cookies=get_cookies(), timeout=15,
  verify=False)`.
- TLS certificate verification is disabled (`verify=False`) in all three
  scripts.
- Requests target `https://opus.abhibots.com/dashboard/api/keys` and
  `.../keys/{key_id}`.

## What operations use it

All operations are **provider-side management** operations (dashboard API):

1. **List keys** — `GET /dashboard/api/keys`
2. **Create key** — `POST /dashboard/api/keys` with body
   `{"name": ..., "dailyTokenLimit": ...}`
3. **Delete key** — `DELETE /dashboard/api/keys/{key_id}`
4. **Quota handling** — when key creation fails with `400` and
   `"Not enough tokens"`, the scripts delete all existing keys and retry.

These are the operations a future provider-specific management adapter
(Phase 4.2+) would perform — but only when the user explicitly initiates
them. The new application never performs them autonomously.

## What HTTP status indicates failure

- List: anything other than `200` is treated as failure.
- Create: `200`/`201` = success; `400` with "Not enough tokens" triggers the
  delete-all-retry path; anything else = failure.
- Delete: `200`/`204` = success; anything else = failure.

## Is the session provider-specific?

Yes — entirely. The base URL (`https://opus.abhibots.com`), the cookie name
(`opus_session`), the dashboard endpoints, the request headers, and the
response parsing are all hard-coded for a single provider. Nothing in the
generic session system may assume any of these details.

## Assumptions about cookies

- Exactly one cookie (`opus_session`) is required.
- The cookie value is passed verbatim; no refresh, no re-login, no
  session-renewal flow exists.
- If the cookie stops working, every request starts failing with a non-200
  status and the scripts simply print an error. They do not detect a login
  redirect, a 401, or a 403 specifically — they only check the status code.

## How expiry currently appears

There is no expiry tracking at all. Expiry manifests only indirectly:
requests begin returning non-200 statuses (a dashboard would typically
redirect to a login page, yielding a 302 or a 401/403).

## How the legacy code reacts to an invalid session

- `list_keys()` → returns `[]` (silent failure from the caller's perspective).
- `create_key()` → returns `None`; `KeyBinder.main()` then exits with
  `[FATAL] Could not create initial key. Check session cookie.`
- `rotate_now.py` → prints `[FATAL] Could not create new key.` and stops.
- `pull_latest_key.py` → prints `[FATAL] Could not retrieve latest key.` and
  exits with code 1.

In other words: the legacy code fails loudly at the CLI, but it never warns
the user in a structured way, never distinguishes "expired" from "wrong", and
never offers a guided replacement flow.

## What Phase 4.1 changes

| Legacy behavior | New behavior |
|-----------------|--------------|
| Session cookie hard-coded in source | Secret stored via `SecretStore`; DB holds only a reference + masked value |
| No lifecycle/validation tracking | Explicit `lifecycle_state` and `validation_state` + derived health |
| No expiry signal | Validation results can be `expired`/`invalid`; health becomes `critical` |
| Silent CLI failure | UI shows "Session requires attention" with a guided Replace action |
| Provider baked into every script | Provider-agnostic `SessionManager` + `SessionValidator` abstraction |
| Automatic key deletion/rotation scripts | NOT reimplemented — no automatic session renewal, no autonomous key management |

The legacy scripts keep running untouched for now; a future
`SessionProviderAdapter`-backed management adapter can expose the stored
session secret to provider-specific integrations without exposing it to the
rest of the application.
