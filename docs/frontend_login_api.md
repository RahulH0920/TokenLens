# TokenLens Frontend Login & Access Security API

## 1. Overview & Security Architecture

The TokenLens Frontend Login API provides a hardened, zero-trust authentication and session management layer for frontend applications (Single Page Apps, Streamlit, React/Next.js dashboards, and automated clients).

```
[ Frontend Client (React / Streamlit / CLI) ]
                 │
                 ├── 1. POST /api/v1/auth/login { username, password }
                 │      ├── Rate Limiting Check (5 attempts / 5m lockout)
                 │      ├── Scrypt Constant-Time Verification (dummy salt fallback)
                 │      └── Session Token Issuance (256-bit URL-safe token)
                 │
                 ├── 2. GET /api/v1/auth/me [ Bearer <token> ]
                 │      ├── SHA-256 Digest Lookup in SQLite access_sessions
                 │      ├── Account Active & Auth-Version Check
                 │      └── Scoped RBAC Profile Returned
                 │
                 └── 3. POST /api/v1/auth/logout [ Bearer <token> ]
                        └── Immediate Revocation in access_sessions
```

---

## 2. API Endpoints Reference

### 2.1 Authenticate / Sign In
- **Route**: `POST /api/v1/auth/login` (Alias: `POST /auth/login`)
- **Access**: Public (Subject to brute-force rate limiting)
- **Request Body**:
  ```json
  {
    "username": "manager.platform",
    "password": "StrongPassword-2026!"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "access_token": "uH6f_EXAMPLE_BEARER_TOKEN_bQ7jA91kM",
    "token_type": "bearer",
    "expires_in": 3600,
    "expires_at": "2026-10-10T05:30:00+00:00",
    "user": {
      "username": "manager.platform",
      "display_name": "Platform Engineering Manager",
      "role": "manager",
      "role_label": "Manager",
      "teams": ["platform", "infra"]
    },
    "role": "manager",
    "teams": ["platform", "infra"]
  }
  ```
- **Error Responses**:
  - `401 Unauthorized`: `{"detail": "Invalid username or password"}` + `WWW-Authenticate: Bearer`
  - `429 Too Many Requests`: `{"detail": "Too many failed login attempts for this account. Try again in 300 seconds."}` + `Retry-After: 300`

---

### 2.2 Current User Introspection
- **Route**: `GET /api/v1/auth/me` (Alias: `GET /auth/me`)
- **Access**: Requires `Authorization: Bearer <access_token>`
- **Response (200 OK)**:
  ```json
  {
    "username": "manager.platform",
    "display_name": "Platform Engineering Manager",
    "role": "manager",
    "role_label": "Manager",
    "teams": ["platform", "infra"],
    "active": true,
    "auth_version": "2026-10-10T04:00:00+00:00"
  }
  ```
- **Error Responses**:
  - `401 Unauthorized`: Token missing, expired, revoked, or invalidated.

---

### 2.3 Revoke Session / Sign Out
- **Route**: `POST /api/v1/auth/logout` (Alias: `POST /auth/logout`)
- **Access**: Requires `Authorization: Bearer <access_token>`
- **Response**: `204 No Content`
  - The token digest in `access_sessions` is marked with `revoked_at`. Subsequent calls immediately receive `401 Unauthorized`.

---

### 2.4 System Authentication Status
- **Route**: `GET /api/v1/auth/status` (Alias: `GET /auth/status`)
- **Access**: Public
- **Response (200 OK)**:
  ```json
  {
    "status": "ready",
    "is_bootstrapped": true,
    "user_count": 3,
    "auth_method": "scrypt_session_bearer",
    "session_ttl_seconds": 3600,
    "service": "tokenlens-auth"
  }
  ```

---

### 2.5 Initial Organization Head Bootstrap
- **Route**: `POST /api/v1/auth/bootstrap` (Alias: `POST /auth/bootstrap`)
- **Access**: Only allowed when `user_count == 0`
- **Request Body**:
  ```json
  {
    "username": "org.head",
    "display_name": "Executive Sponsor",
    "password": "InitialMasterPassword-2026!",
    "bootstrap_token": "<TOKENLENS_BOOTSTRAP_TOKEN from .env>"
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "message": "Organization head account successfully created",
    "user": {
      "username": "org.head",
      "display_name": "Executive Sponsor",
      "role": "org_head",
      "role_label": "Organization Head",
      "teams": []
    }
  }
  ```
- **Error Responses**:
  - `403 Forbidden`: Initial setup has already been completed.

---

## 3. Enterprise Security Safeguards

1. **Scrypt Salted Password Hashing**:
   - `N=16384`, `r=8`, `p=5`, `maxmem=128MB`.
   - Each account has a unique 128-bit cryptographically random salt.
2. **Timing Attack Resistance**:
   - When a requested username does not exist, the server computes a dummy scrypt verification with `_DUMMY_SALT`. This prevents response time variances from leaking whether an account exists.
3. **Brute Force & Credential Stuffing Defense**:
   - The API tracks failed attempts per client IP and per username (`LoginRateLimiter`).
   - After 5 failed attempts within 5 minutes, requests are rejected with `429 Too Many Requests` and a standard `Retry-After` HTTP header.
4. **Hashed Session Tokens**:
   - Session tokens are generated using `secrets.token_urlsafe(32)` (256 bits of entropy).
   - Only the SHA-256 digest is stored in `access_sessions`. The plaintext token is never stored in the database or written to server logs.
5. **Instant Invalidation on Account Updates**:
   - Each session records the account's `auth_version` timestamp.
   - If a user changes their password or an administrator updates permissions, all existing sessions for that user are instantly invalidated across all devices.
6. **Immutable Audit Trail**:
   - All authentication events (`api_login_succeeded`, `api_login_failed`, `logout`, `bootstrap`) are automatically recorded in the `access_audit` SQLite ledger with client IP and timestamps.
7. **Strict Security Headers**:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: no-referrer`
   - `Cache-Control: no-store`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`

---

## 4. Frontend Integration Examples

### JavaScript (Fetch / SPA)
```javascript
// 1. Sign In
async function login(username, password) {
  const res = await fetch("http://127.0.0.1:8000/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "Authentication failed");
  }
  const data = await res.json();
  sessionStorage.setItem("access_token", data.access_token);
  return data.user;
}

// 2. Fetch Current User Profile
async function getProfile() {
  const token = sessionStorage.getItem("access_token");
  const res = await fetch("http://127.0.0.1:8000/api/v1/auth/me", {
    headers: { "Authorization": `Bearer ${token}` }
  });
  return await res.json();
}

// 3. Sign Out
async function logout() {
  const token = sessionStorage.getItem("access_token");
  await fetch("http://127.0.0.1:8000/api/v1/auth/logout", {
    method: "POST",
    headers: { "Authorization": `Bearer ${token}` }
  });
  sessionStorage.removeItem("access_token");
}
```

### Python Client SDK (`core.auth_client.TokenLensAuthClient`)
```python
from core.auth_client import TokenLensAuthClient

client = TokenLensAuthClient(base_url="http://127.0.0.1:8000")

# 1. Login
session = client.login("manager.ai", "Manager-Secret-2026!")
print(f"Logged in as: {session.user.display_name} (Role: {session.user.role})")

# 2. Introspect Profile
profile = client.get_me()
print(f"Authorized Teams: {profile.teams}")

# 3. Logout
client.logout()
```
