# DashScribe Accounts Design (v2)

## Overview

Add optional user accounts to DashScribe using Supabase. The app remains fully functional offline without an account. Accounts enable cloud backup/sync, telemetry, feature gating infrastructure, and personalization. Transcription history remains local-only.

## Use Cases

**UC1: Multi-device settings sync**
WHO: A power user with an iMac and a MacBook
WANTS: Their 47 snippets, custom dictionary, and formatting preferences on both machines
SO THAT: They don't manually recreate config when switching devices
WHEN: They sign in on a second Mac

**UC2: Data backup after device loss**
WHO: A user whose Mac was stolen or had a drive failure
WANTS: Their snippets, dictionary, and app styles restored on a new Mac
SO THAT: They don't lose months of customization
WHEN: They sign in on their replacement machine

**UC3: Product improvement via telemetry**
WHO: The developer (you)
WANTS: Per-session metrics (latency by chipset, feature usage, error rates)
SO THAT: Performance can be optimized for real hardware and popular features prioritized
WHEN: Users opt in by creating an account

**UC4: Future monetization**
WHO: The developer
WANTS: Infrastructure to gate premium features behind paid tiers
SO THAT: The app can sustain development costs if it gains users
WHEN: Pricing decisions are made (TBD)

**UC5: Account deletion**
WHO: Any signed-in user
WANTS: To delete their account and all associated cloud data
SO THAT: Their right to erasure is respected (GDPR compliance)
WHEN: They choose "Delete account" in settings

## Success Metrics

| Metric | Target | Timeline |
|--------|--------|----------|
| Account creation rate | >30% of active users within 60 days of release | 60 days post-launch |
| Sync round-trip | <2s for settings push/pull | At launch |
| Auth flow completion | >90% of users who start sign-in complete it | 30 days post-launch |
| Telemetry flush success | >95% of events delivered within 24h | At launch |
| Offline dictation availability | 100% — auth never blocks local dictation | At launch |

**Failure criteria:** If <10% of users create accounts after 90 days, re-evaluate whether accounts add value.

## Auth

### Provider
Supabase Auth — magic link + Google OAuth. Passwordless only, no password storage. No Apple Sign In (requires Developer ID signing / Apple Developer Program).

### Account is Optional
The app works fully without an account, identical to today. On first launch, the app shows a dismissible banner: "Sign in to sync your settings across devices." The banner reappears once per week until dismissed 3 times, then stops. All dictation, history, snippets, dictionary, and settings work locally without auth.

### Flow (when user chooses to sign in)
1. User clicks "Sign in" in settings panel
2. Auth modal appears (inside main window, not a separate window)
3. User picks: magic link (enter email → check inbox → click link) or Sign in with Google
4. OAuth uses PKCE flow with state parameter for CSRF protection
5. Magic link / OAuth redirects to `http://localhost:8765/auth/callback`
6. FastAPI callback handler validates state param, exchanges code for session
7. Auth modal closes, settings panel shows profile + sync status
8. If user clicks magic link while app is closed → browser shows "Open DashScribe to complete sign-in" page with a retry link

### Token Storage
Tokens stored in **macOS Keychain** via Security framework (SecItemAdd/SecItemCopyMatching through PyObjC). Service name: `com.dashscribe.auth`. Stores: access token, refresh token, user ID.

Fallback for environments where Keychain is unavailable (e.g., CI/testing): encrypted file at `~/.dashscribe/auth.enc` using a machine-derived key.

### Session Management
- JWT: 15-minute expiry (short window if leaked), refresh token: 30-day expiry
- Supabase Python SDK handles automatic silent refresh
- Refresh failure: show "Session expired" banner with sign-in button (never blocks dictation)
- Sign-out: per-device only — clears local Keychain entry, does not revoke other devices

### Offline Behavior
- No account: app works exactly as today, forever
- Signed in + online: sync and telemetry active
- Signed in + offline: app works normally, sync/telemetry queues locally, flushes on reconnect
- Signed in + offline >30 days: app still works, but sync is paused until token refreshes. User sees "Sign in again to resume sync" — dictation never blocked

## Data Model (Supabase Postgres)

### profiles
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid (FK users.id) | Primary key |
| display_name | text | |
| avatar_url | text | |
| chipset | text | e.g., "Apple M4 Pro" |
| ram_gb | integer | |
| os_version | text | |
| app_version | text | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### user_settings
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid (FK users.id) | Primary key |
| settings | jsonb | Entire config blob (minus hotkey) |
| updated_at | timestamptz | Server-set on upsert |

Using a single JSONB column instead of individual columns avoids schema migrations when new settings are added and matches SettingsManager's flat-dict pattern.

### user_snippets
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid (FK users.id) | Primary key |
| snippets | jsonb | {trigger: expansion} map |
| updated_at | timestamptz | Server-set on upsert |

### user_dictionary
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid (FK users.id) | Primary key |
| terms | text[] | Array of custom words |
| updated_at | timestamptz | Server-set on upsert |

### feature_entitlements
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid (FK users.id) | Primary key |
| tier | text | free/pro/enterprise |
| features_enabled | jsonb | Flexible feature flags |
| valid_until | timestamptz | Nullable, for trials |
| updated_at | timestamptz | |

Absence of a row = free tier with all features enabled (no row needed for free users initially).

### telemetry_events
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | Primary key |
| user_id | uuid (FK users.id) | |
| event_type | text | session/feature_use/error |
| payload | jsonb | Schema-validated (see Telemetry section) |
| created_at | timestamptz | Partitioned by month |

### Row-Level Security
- All tables: `user_id = auth.uid()` — users can only access their own data
- `telemetry_events`: insert-only for users (no read/update/delete — only queried from dashboard)
- RLS policy tests required in test suite (verify user A cannot access user B's rows)

## Sync Strategy

- **Direction:** Push on local save, pull on sign-in (new device or re-auth)
- **Conflict resolution:** Server-timestamp wins. `updated_at` is set by Supabase `now()` on upsert, not client clock. On pull, if server `updated_at` > local last-sync timestamp, overwrite local.
- **Scope:** Settings (minus hotkey), snippets, dictionary, app styles, profile. NOT history, NOT hotkey (device-specific), NOT auto_insert (device-specific, depends on Accessibility permission)
- **Debounce:** Settings changes debounced 2 seconds before push (user may toggle multiple settings rapidly)
- **Failure handling:** If push fails, mark dirty. Retry on next save or next 5-min flush cycle.
- **Data size limits:** Max 500 snippets, max 10,000 dictionary terms, max 100KB settings JSON. Enforced client-side and via Supabase check constraints.

### Existing User Migration (on first sign-in)
When a user with existing local data (`~/.dashscribe/config.json`, `snippets.json`, `dictionary.txt`) signs in for the first time:
1. Check if cloud has data for this user (from another device)
2. If cloud is empty: upload local data to cloud (local becomes source of truth)
3. If cloud has data: show prompt "We found settings from another device. Use cloud settings or keep this device's settings?" — chosen set becomes the new cloud state

## API Endpoints

All new endpoints require valid JWT in Authorization header (except `/auth/callback`).
Existing endpoints (`/api/history`, `/api/settings/*`, `/ws`, `/ws/bar`) remain unauthenticated — they are localhost-only and gating them would break offline usage for users without accounts.

### Auth Endpoints

**POST /api/auth/magic-link**
Request: `{ "email": "user@example.com" }`
Response: `{ "ok": true, "message": "Check your email" }`
Rate limit: 3 per email per 10 min, 10 total per hour.

**GET /api/auth/callback?code=...&state=...**
Handles OAuth/magic link redirect. Validates state param, exchanges code via PKCE.
Response: Redirects to a local HTML page that sends a WebSocket message to close the auth modal.

**POST /api/auth/sign-out**
Clears local Keychain tokens. Response: `{ "ok": true }`

**GET /api/auth/session**
Returns current session state.
Response: `{ "signed_in": true, "user": { "id": "...", "email": "...", "display_name": "..." } }` or `{ "signed_in": false }`

**DELETE /api/auth/account**
Deletes user account and all cloud data (profiles, settings, snippets, dictionary, telemetry_events). Clears local tokens.
Response: `{ "ok": true, "message": "Account deleted" }`
Implementation: Supabase Edge Function with service role key to cascade delete across all tables.

### Sync Endpoints

**POST /api/sync/push**
Request: `{ "settings": {...}, "snippets": {...}, "dictionary": [...] }`
Response: `{ "ok": true, "updated_at": "..." }`

**GET /api/sync/pull**
Response: `{ "settings": {...}, "snippets": {...}, "dictionary": [...], "updated_at": "..." }`

### Profile Endpoint

**GET /api/profile**
Response: `{ "display_name": "...", "email": "...", "avatar_url": "...", "tier": "free" }`

**POST /api/profile**
Request: `{ "display_name": "..." }`
Response: `{ "ok": true }`

## Architecture Changes

### New Files
- `auth.py` — AuthManager: Supabase client init, sign-in/out, Keychain token storage, PKCE flow, session state
- `sync.py` — SyncManager: push/pull with debounce, conflict resolution, data size validation, offline dirty tracking
- `telemetry.py` — TelemetryManager: event collection, schema validation, batch upload, offline SQLite queue (`~/.dashscribe/telemetry.db`)
- `static/auth.js` — Auth modal logic (sign-in form, OAuth buttons, callback listener)

### Modified Files
- `main.py` — Initialize AuthManager, start telemetry flush timer if signed in
- `app.py` — New endpoints (auth, sync, profile). Inject AuthManager as dependency. Auth middleware on new endpoints only.
- `config.py` — SettingsManager gains `on_change` callback with 2-second debounce triggering SyncManager push (only if signed in)
- `state.py` — New `AuthState` enum (signed_out, signing_in, signed_in, offline_cached) as a SEPARATE state machine from AppState. New `AuthStateManager` class following AppStateManager pattern.
- `static/index.html` / `app.js` — Sign-in banner, profile section in settings, sign-out button, sync status indicator, delete account button

### App Start Flow
```
main.py
├── Initialize AuthManager
│   ├── Check macOS Keychain for cached tokens
│   ├── Valid token? → refresh silently → AuthState.signed_in
│   ├── Expired/missing? → AuthState.signed_out
│   └── Valid but offline? → AuthState.offline_cached
├── Start main window (always — regardless of auth state)
├── If signed_in → start SyncManager, start telemetry flush (every 5 min)
└── If signed_out → show dismissible "Sign in" banner
```

### Offline Telemetry Queue
- Separate SQLite database: `~/.dashscribe/telemetry.db` (avoids locking contention with history.db)
- WAL mode, same pattern as TranscriptionHistory
- Events written to `queue` table with `uploaded` boolean flag
- Flush: batch INSERT to Supabase, mark `uploaded = true`, periodically purge uploaded events older than 7 days
- Queue cap: 10,000 events max. Drop oldest when cap reached.
- Deduplication: UUID per event, Supabase upsert on conflict

### Device Info Collection
- Chipset: `subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'])` (primary — `platform.processor()` returns empty on Apple Silicon)
- RAM: `os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')`
- Collected on first sign-in, updated when app_version changes

## Telemetry Schema

Telemetry payloads are schema-validated before insert. Only these fields are allowed:

**Session events** (`event_type: "session"`):
```json
{ "duration_seconds": 12.5, "latency_seconds": 0.8, "word_count": 42, "source": "dictation" }
```

**Feature usage events** (`event_type: "feature_use"`):
```json
{ "feature": "smart_cleanup", "action": "enabled" }
```

**Error events** (`event_type: "error"`):
```json
{ "error_type": "transcription_failed", "error_code": "model_load" }
```

Error payloads are sanitized — no transcription text, no file paths, no user content. Only error type and code.

## Auth UI

- Auth modal inside main window (not a separate pywebview window) — avoids pywebview window lifecycle issues
- Clean centered card, dark theme matching existing style
- DashScribe logo + tagline
- Email input + "Send magic link" button
- Divider "or"
- "Sign in with Google" button (no Apple Sign In)
- Post-send state: "Check your email" with resend option (rate-limited)
- Profile in settings: avatar + name + email, sign-out button, sync status (synced / syncing / offline), "Delete account" danger button
- Privacy note: "DashScribe collects anonymous usage metrics to improve performance. Your transcriptions are never uploaded."

## Security

### Token Storage
macOS Keychain via Security framework. Fallback to encrypted file for CI/test environments.

### OAuth Security
PKCE flow with S256 code challenge. State parameter validated on callback. Authorization code exchanged server-side only.

### Rate Limiting
- Magic link: 3 per email per 10 min, 10 total per hour
- Auth callback: 10 per minute
- Sync push: 30 per minute
- Telemetry flush: 6 per minute (once per 5-min cycle, with headroom)

### Input Validation
- All sync payloads validated against size limits (500 snippets, 10K dictionary terms, 100KB settings)
- Telemetry payloads validated against allowed schema — reject unknown fields
- Profile display_name: max 100 chars, sanitized

### Supabase Client
- Uses anon key (public, embedded in app) — RLS is the sole security boundary
- Anon key only allows operations permitted by RLS policies
- No service role key in client — only used in Supabase Edge Functions for admin operations (account deletion)

### CORS
Not needed — all requests from localhost to localhost. Supabase SDK handles its own CORS to supabase.co.

## Supabase SDK Packaging

**Package:** `supabase` (PyPI) — wraps gotrue, postgrest, storage3, realtime, httpx
**Version:** Pin to latest stable (currently 2.x)
**py2app assessment needed:** supabase-py is pure Python + httpx (also pure Python). No native extensions. Should bundle cleanly without the namespace package issues seen with mlx/PyObjCTools. Add to `packages` list in setup.py. **Must be validated in build_app.sh before merging Phase 1.**

## Implementation Phases

### Phase 1: Auth (auth.py + auth UI + token storage)
- AuthManager with Supabase client init, PKCE OAuth, magic link, Keychain storage
- AuthState enum + AuthStateManager (separate from AppState)
- Auth modal in main window (auth.js)
- /api/auth/* endpoints with rate limiting
- Sign-in banner (dismissible)
- **Tests:** test_auth.py — mock Supabase SDK responses, test PKCE flow, test Keychain read/write (mock Security framework), test rate limiting, test offline state transitions, test expired token handling
- **Acceptance:** User can sign in via magic link and Google, tokens persist across restarts, dictation works without account

### Phase 2: Sync (sync.py + settings sync)
- SyncManager with push/pull, debounce, conflict resolution
- on_change hook in SettingsManager
- Existing user migration (local → cloud on first sign-in)
- /api/sync/* endpoints
- Sync status indicator in settings
- **Tests:** test_sync.py — mock Supabase responses, test debounce behavior, test conflict resolution (server wins), test offline dirty tracking, test data size validation, test migration prompt logic
- **Acceptance:** Settings/snippets/dictionary sync across two devices, conflict resolution works, offline changes flush on reconnect

### Phase 3: Telemetry (telemetry.py + event collection)
- TelemetryManager with schema validation, local SQLite queue, batch upload
- Collect session/feature/error events
- Device info collection on first sign-in
- Flush timer (5 min)
- **Tests:** test_telemetry.py — test event schema validation (reject bad payloads), test queue cap, test flush batch logic, test deduplication, test error sanitization (no transcription text leaks)
- **Acceptance:** Events collected and flushed to Supabase, queue handles offline gracefully, payloads validated

### Phase 4: Feature gating + account management
- Feature entitlements check (cached locally)
- Account deletion (DELETE /api/auth/account + Supabase Edge Function)
- Profile management (display name, avatar)
- Privacy note in settings
- **Tests:** test_entitlements.py — test tier checking, test cache, test fallback when offline. Account deletion: test cascade delete, test local cleanup
- **Acceptance:** Free tier works with all features, account deletion purges all cloud data, profile editable

## Testing Strategy

### Mock Infrastructure
- **Supabase SDK:** Mock `supabase.create_client()` to return a mock client. Mock `client.auth`, `client.table()`, `client.rpc()` responses with fixture JSON.
- **macOS Keychain:** Mock `Security.SecItemAdd`, `SecItemCopyMatching`, `SecItemDelete` via `unittest.mock.patch`
- **Network:** Use `httpx` mock transport for any direct HTTP calls
- **SQLite:** Use in-memory SQLite for telemetry queue tests

### Edge Cases Per Module
**auth.py:** Expired JWT, expired refresh token, Keychain permission denied, Supabase unreachable, PKCE state mismatch, rate limit exceeded, concurrent sign-in attempts, corrupt Keychain entry
**sync.py:** Server newer than local, local newer than server, both modified while offline, empty cloud (first device), empty local (new install), oversized payload rejected, network failure mid-push, debounce cancellation
**telemetry.py:** Queue at capacity (10K), flush during app quit, Supabase unreachable for days, duplicate event IDs, invalid event schema rejected, error payload sanitization

### RLS Policy Tests
Verify in test suite that user A cannot read/write user B's data across all tables.

## Future Work

- Year-end wrapped: voluntary opt-in to temporarily sync history stats for annual summary
- Custom Whisper model tuning from cloud-stored vocabulary
- Usage-adapted behavior (learned patterns, preferred formatting)
- Cross-device sign-out (token revocation via Supabase webhook)
- Telemetry dashboard (Supabase + Grafana or custom)
