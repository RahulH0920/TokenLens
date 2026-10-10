# TokenLens account roles and team grants

TokenLens uses local accounts for the Streamlit dashboard and the FastAPI login.
The account database is `data/tokenlens_access.sqlite3`; it stores salted scrypt
password hashes, scoped team grants, hashed API session credentials, and an
audit trail. Passwords and bearer credentials are never stored in plain text.

## First-time setup

1. Copy `.env.example` to `.env` if `.env` does not already exist.
2. Generate an 11-character one-time setup token with `python -c "import secrets; print(secrets.token_urlsafe(8))"`.
3. Put the generated value in `.env` as `TOKENLENS_BOOTSTRAP_TOKEN=...` and start Streamlit.
4. On the first page, enter that token and create the organization-head account
   with a password of at least 12 characters. The setup token cannot create
   another account after this initial setup.
5. Sign in as the organization head and open **Settings → Organization Access
   Grants** to create accounts and assign team scopes.

Do not commit `.env` or share the bootstrap token. The app refuses first-time
setup when the token is missing or shorter than 11 characters. API setup
attempts are rate-limited. There are no default credentials or public sign-up
after the first account is created.

## Role permissions

| Role | Usage data | Delegated workloads | Grants and settings |
|---|---|---|---|
| Organization Head | All teams | Create, view, update, and revoke any task | Create accounts, grant/revoke scopes, reset passwords, access settings and audit events |
| Manager | Only granted teams | Allocate work only in granted teams; assign an active team leader granted to that same team; update/revoke tasks in scope | Cannot create accounts or expand grants |
| Team Leader | Only granted teams | View tasks assigned to their username and update their status | Cannot allocate, revoke, or grant access |

Access defaults to deny. A manager or team leader without a team grant cannot
see team usage. An account must be active to sign in. Changing its role, teams,
password, or active state invalidates its existing Streamlit session on the next
request. Account and task changes are recorded in the access audit log.

## API login

After creating accounts in Streamlit, a client can exchange username and
password for a one-hour API bearer credential:

```http
POST /auth/login
Content-Type: application/json

{"username":"manager.eng","password":"..."}
```

The response contains `access_token`, `token_type`, `expires_at`, `role`, and
`teams`. Send the credential as `Authorization: Bearer <access_token>` on API
requests. `/auth/logout` revokes the current credential. Account disablement,
password reset, or grant/role changes invalidate prior credentials. The
credential is random and opaque; only its SHA-256 digest is stored. Its lifetime
is configurable with `FINOPS_API_SESSION_TTL_SECONDS` (60 to 86400 seconds,
default 3600).

The API applies the same team scopes as the dashboard. Team leaders cannot add
usage records or run manager guardrail checks; organization-wide reconciliation
is restricted to the organization head. The legacy `FINOPS_API_TOKEN` remains
a service credential with organization-wide access, so keep it server-side and
do not put it in browser code. For remote login, the existing service token is
also required at credential exchange; without it the API rejects non-loopback
clients.

## Deployment boundary

This account store belongs to one local TokenLens instance and is not synced to
central PostgreSQL. Streamlit currently binds to `127.0.0.1`. The FastAPI service
uses the same local account store for short-lived user sessions and retains a
separate organization-wide `FINOPS_API_TOKEN` for machine clients. Keep both
services on loopback for local use. A shared, multi-host organization deployment
needs centralized identity/session storage, secret management, and HTTPS.
