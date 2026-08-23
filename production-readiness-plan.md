# EcoGuardAI — Production-Readiness Implementation Plan

## Design Decisions (confirmed)

- **Task 1 — Alembic migration file:** Written manually by introspecting
  the ORM models in `core/database.py`. Does NOT require a live database
  connection. The generated file must be reviewed against the models before
  any `alembic upgrade head` is run.
- **Task 6 — CI pip cache:** A `actions/cache` step is included to cache
  the pip download cache between runs.
- **Out of scope:** Redis-backed rate limiting and DB-based API-key
  provisioning are explicitly excluded from this plan.

---

## Overview

The EcoGuardAI application code is feature-complete and all 97 tests pass.
This plan addresses the remaining production-readiness gaps identified in the
completion audit. No new features are added; no existing architecture or
behaviour changes.

Tasks are ordered strictly by deployment impact:
1. Items that must be fixed before any public deployment (critical)
2. Items that harden the deployment once it is live (high)
3. Minor items that are worth completing but do not block launch (medium)

Each task is labelled **AGENT** (implementable by the AI agent in code) or
**MANUAL** (requires developer action, credentials, or external services).

---

## Task 1 — Database Migrations with Alembic [AGENT]

**Status:** [x] done

### Intent
Replace the development-only `Base.metadata.create_all()` call in
`core/database.py::init_db()` with an Alembic-managed migration workflow.
This gives the project a versioned, rollback-capable schema history required
for any production database.

### Relevant Context
- `core/database.py` — contains `init_db()` and all four ORM models
  (IndustrialUnit, SensorReading, Violation, Alert)
- `requirements.txt` — `alembic>=1.13.1` is already listed
- `Dockerfile` — `alembic` is already installed via pip
- `config/settings.py` — `async_database_url` property returns the
  asyncpg-compatible URL used by the engine

### Expected Outcomes
- `alembic.ini` exists at repo root, pointing at `alembic/` directory
- `alembic/env.py` imports `Base` from `core.database` and uses
  `settings.async_database_url` as the connection string
- `alembic/versions/` contains exactly one initial migration file that
  captures the full current schema (IndustrialUnit, SensorReading,
  Violation, Alert tables with all columns, indexes, and foreign keys)
- `core/database.py::init_db()` is updated to run
  `alembic upgrade head` programmatically (or is replaced with a comment
  directing operators to run migrations manually before first start)
- `README.md` quick-start step 4 is updated to read:
  `alembic upgrade head` instead of `python scripts/seed_database.py`
  (seed script remains; it is run after migration, not instead of it)
- Running `alembic upgrade head` on a clean database creates all four tables
  with no errors

### Todo List
1. Run `alembic init alembic` scaffold (creates `alembic.ini` and
   `alembic/` directory)
2. Edit `alembic/env.py`:
   - Import `Base` from `core.database`
   - Set `target_metadata = Base.metadata`
   - Load `settings.database_url` (sync URL — Alembic uses synchronous
     connection for migrations; use `settings.database_url` not
     `settings.async_database_url`)
   - Configure the `run_migrations_offline` and
     `run_migrations_online` functions to use the above
3. Generate the initial migration:
   `alembic revision --autogenerate -m "initial schema"`
4. Review and commit the generated migration file (verify all four tables,
   all indexes, and JSONB column types are present)
5. Update `core/database.py::init_db()`: keep it for dev convenience but
   add a docstring noting that production deployments must run
   `alembic upgrade head` before starting the app
6. Update `README.md` to add `alembic upgrade head` as a step between
   infrastructure start and seeding

### Completion Condition
`alembic upgrade head` runs successfully against a fresh PostgreSQL
instance and creates all four tables. `alembic history` shows exactly one
revision. No existing test breaks.

---

## Task 2 — Wire APIKeyMiddleware in Production Mode [AGENT]

**Status:** [x] done

### Intent
`api/middleware.py::APIKeyMiddleware` exists and is fully implemented but is
never registered with the FastAPI app. In `ENVIRONMENT=production` mode every
protected endpoint is currently accessible without an API key. Wire the
middleware using a new `VALID_API_KEYS` settings field.

### Relevant Context
- `api/main.py` line 74 — `app.add_middleware(RateLimitMiddleware, ...)` is
  the only middleware registration; `APIKeyMiddleware` is absent
- `api/middleware.py::APIKeyMiddleware.__init__` — accepts
  `valid_keys: List[str] | None`
- `config/settings.py` — add `valid_api_keys_raw: str = ""` with a property
  that splits on commas, mirroring the existing `allowed_origins_raw` pattern
- `.env.example` — needs the new variable added with a comment

### Expected Outcomes
- `config/settings.py` has a `valid_api_keys_raw` field and a
  `valid_api_keys` property returning `List[str]`
- `api/main.py` registers `APIKeyMiddleware` with
  `valid_keys=settings.valid_api_keys`
- In `ENVIRONMENT=development` (default) all requests pass through
  (existing bypass logic in the middleware is unchanged)
- In `ENVIRONMENT=production` with `VALID_API_KEYS` unset (empty), all
  protected requests are rejected with 401
- In `ENVIRONMENT=production` with a key set, requests with
  `X-API-Key: <key>` are accepted; requests without are rejected
- `.env.example` shows `VALID_API_KEYS=key1,key2`
- Existing 97 tests still pass (tests run with default dev environment)

### Todo List
1. Add `valid_api_keys_raw: str = ""` to `config/settings.py`
2. Add `valid_api_keys` property to `Settings` class (split on comma,
   strip whitespace, drop empty strings)
3. Add `app.add_middleware(APIKeyMiddleware, valid_keys=settings.valid_api_keys)`
   to `api/main.py` — place it after `RateLimitMiddleware` so key checking
   runs before rate limiting
4. Add `VALID_API_KEYS=` to `.env.example` with a comment explaining format
5. Run the full test suite to confirm no regressions

### Completion Condition
`pytest tests/ -v` passes 97/97. A manual or test-level check confirms that
`APIKeyMiddleware` appears in `app.middleware_stack` when the app is
constructed.

---

## Task 3 — Enable scikit-learn in Dockerfile [AGENT]

**Status:** [x] done

### Intent
`scikit-learn` is commented out of `requirements.txt` because no wheel exists
for the developer's local Python 3.14 environment. The `Dockerfile` uses
`python:3.11-slim` which has full scikit-learn wheel support. Add it to the
`Dockerfile` pip install command so that `IsolationForest` anomaly detection
is available inside the container. The local-dev fallback path in
`agents/violation_agent.py` remains in place and still protects users running
Python 3.14 locally.

### Relevant Context
- `Dockerfile` line 13–20 — explicit pip install list (does not use
  `requirements.txt`); `scikit-learn` simply needs to be appended
- `agents/violation_agent.py` lines 9–18 — the
  `try: import sklearn except ImportError: _ML_AVAILABLE = False` guard
  already handles the fallback cleanly; no agent code changes needed
- `requirements.txt` lines 17–20 — the existing comment explaining why it is
  commented out should remain, but a note should be added clarifying it is
  installed in the Docker image

### Expected Outcomes
- `Dockerfile` pip install block includes `scikit-learn`
- `requirements.txt` comment is updated to note that scikit-learn IS
  installed inside the Docker image (Python 3.11 has wheels)
- A container built from the updated Dockerfile and running
  `python -c "from sklearn.ensemble import IsolationForest; print('ok')`
  exits 0

### Todo List
1. Add `scikit-learn` to the pip install command in `Dockerfile`
2. Update the comment in `requirements.txt` to clarify that scikit-learn
   is included in the Docker image but optional for local Python 3.14 installs

### Completion Condition
`docker build .` completes without error. `docker run <image> python -c
"from sklearn.ensemble import IsolationForest"` exits 0. No test regressions.

---

## Task 4 — Docker Production Hardening [AGENT]

**Status:** [x] done

### Intent
Fix three concrete issues in the `Dockerfile` and `docker-compose.yml` that
would cause problems in a production deployment:
1. The container runs as root — a security risk
2. The `Dockerfile` has no `HEALTHCHECK` — orchestrators cannot detect a
   crashed app
3. `docker-compose.yml` mounts the entire repo into the container with
   `--reload` (dev mode) — this must not be the production compose command
4. `pgadmin` has hardcoded credentials (`admin`/`admin`) that should come
   from env vars

The `docker-compose.yml` is a dev convenience file. The fix for items 3 and 4
is to make the compose file environment-variable-aware without breaking the
existing dev workflow.

### Relevant Context
- `Dockerfile` lines 1–27 — full file
- `docker-compose.yml` lines 1–79 — full file; `api` service uses volume
  mount and `--reload`
- `.env.example` — pgadmin credentials should be added here

### Expected Outcomes
- `Dockerfile` creates a non-root user `ecoguard` (uid 1000) and switches
  to it before `EXPOSE` and `CMD`
- `Dockerfile` includes a `HEALTHCHECK` that curls `/api/health`
- `docker-compose.yml` `api` service uses env vars
  `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` sourced from the
  `.env` file rather than hardcoded values
- `docker-compose.yml` `api` service `command:` and `volumes:` remain as-is
  for dev convenience; a comment is added noting that production deployments
  should override `command:` to remove `--reload` and remove the volume mount
- `.env.example` gains `PGADMIN_EMAIL` and `PGADMIN_PASSWORD` fields

### Todo List
1. In `Dockerfile`, add `RUN useradd -m -u 1000 ecoguard` after the pip
   install block
2. Add `USER ecoguard` before `EXPOSE 8000`
3. Add `HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
   CMD curl -f http://localhost:8000/api/health || exit 1`
4. In `docker-compose.yml`, replace hardcoded pgadmin env values with
   `${PGADMIN_EMAIL:-admin@ecoguardai.com}` and
   `${PGADMIN_PASSWORD:-admin}` (keeps dev default, allows override)
5. Add `PGADMIN_EMAIL` and `PGADMIN_PASSWORD` to `.env.example` with
   a comment
6. Add an inline comment to the `api` service `command:` and `volumes:`
   fields noting they are dev-mode settings

### Completion Condition
`docker build .` succeeds. `docker run --rm <image> whoami` prints `ecoguard`
(not `root`). `docker inspect <image>` shows a `Healthcheck` key.
`docker-compose config` parses without errors.

---

## Task 5 — Root .gitignore [AGENT]

**Status:** [x] done

### Intent
There is no `.gitignore` at the repository root (only one inside
`.pytest_cache/`). Without it, Python bytecode (`__pycache__/`, `*.pyc`),
the secret `.env` file, log files, and test caches will be committed if a
developer runs `git add .`. This is a low-effort correctness fix.

### Relevant Context
- `.pytest_cache/.gitignore` — exists but only covers that subdirectory
- `logs/ecoguardai_2026-08-22.log` — currently unprotected
- `.env` — must never be committed

### Expected Outcomes
- `.gitignore` at repo root covers: `.env`, `logs/`, `__pycache__/`,
  `*.pyc`, `*.pyo`, `.pytest_cache/`, `*.egg-info/`, `dist/`, `build/`,
  `.mypy_cache/`, `node_modules/`, `*.log`

### Todo List
1. Create `.gitignore` at repo root with the standard Python + project
   entries listed above

### Completion Condition
`git check-ignore -v .env` reports `.gitignore`. `git check-ignore -v logs/`
reports `.gitignore`. File does not break any existing tests.

---

## Task 6 — GitHub Actions CI Pipeline [AGENT]

**Status:** [x] done

### Intent
There is no automated testing pipeline. Create a minimal GitHub Actions
workflow that runs the test suite on every push and pull request. This
gives the project a quality gate that catches regressions before merge.
The workflow must not require any external services (no DB, no IBM Cloud)
since all 97 tests are designed to run fully mocked.

### Relevant Context
- `pytest.ini` — `asyncio_mode = auto`, `testpaths = tests`
- `requirements.txt` — full dependency list; `pytest` and `pytest-asyncio`
  already listed
- All tests mock database and IBM Granite — no live services needed
- Python version in `Dockerfile` is 3.11; local dev uses 3.14

### Expected Outcomes
- `.github/workflows/ci.yml` exists
- Workflow triggers on `push` and `pull_request` to any branch
- Workflow runs on `ubuntu-latest` with Python 3.11
- Steps: checkout → install dependencies → run pytest
- No secrets or credentials required
- Badge-ready: workflow name is "CI"

### Todo List
1. Create `.github/workflows/ci.yml` with:
   - `name: CI`
   - Triggers: `push` and `pull_request`
   - Job: `test` on `ubuntu-latest`
   - Steps: `actions/checkout@v4`, `actions/setup-python@v5` (3.11),
     `pip install -r requirements.txt`, `pytest tests/ -v`

### Completion Condition
The workflow file is valid YAML and passes `act` dry-run or GitHub's
workflow linter. All 97 tests pass in the workflow environment (confirmed
by the existing test design — no live services needed).

---

## Task 7 — Add LICENSE File [AGENT]

**Status:** [x] done

### Intent
`README.md` states "MIT © EcoGuardAI Contributors" but no `LICENSE` file
exists. Without a `LICENSE` file the project has no legal open-source
status. This is a one-file addition.

### Relevant Context
- `README.md` line 229 — "MIT © EcoGuardAI Contributors"

### Expected Outcomes
- `LICENSE` file exists at repo root with the standard MIT license text
- Year is 2025, holder is "EcoGuardAI Contributors"

### Todo List
1. Create `LICENSE` with the standard MIT license text (2025,
   EcoGuardAI Contributors)

### Completion Condition
`LICENSE` file exists. Its content is valid MIT license text matching
what README.md states.

---

## Tasks Requiring Manual Action (MANUAL)

The following items **cannot be completed by the agent** because they
require external credentials, external services, or human decisions.

---

### M1 — Configure real IBM Cloud credentials [MANUAL]

**What:** Copy `.env.example` to `.env` and fill in:
```
IBM_CLOUD_API_KEY=<your real key>
WATSONX_PROJECT_ID=<your project UUID>
```
The Granite integration has already been verified passing with real
credentials (see `scripts/test_granite_integration.py`).

**How to test:** `python scripts/test_granite_integration.py`

**Completion condition:** Script prints `[PASS] Real IBM Granite
integration confirmed.`

---

### M2 — Set production secrets in .env [MANUAL]

**What:** Before deploying with `ENVIRONMENT=production`, set:
```
SECRET_KEY=<random 64-char hex string>
VALID_API_KEYS=<comma-separated keys for API consumers>
ALERT_EMAIL_PASSWORD=<real SMTP password>
REGULATORY_WEBHOOK_URL=<real GPCB/regulatory endpoint>
PGADMIN_EMAIL=<secure email>
PGADMIN_PASSWORD=<strong password>
```
Generate a secret key with: `python -c "import secrets; print(secrets.token_hex(32))"`

**How to test:** `curl -H "X-API-Key: <key>" http://localhost:8000/api/violations`
returns 200. `curl http://localhost:8000/api/violations` returns 401.

**Completion condition:** All production env vars set to non-placeholder
values before `docker-compose up`.

---

### M3 — Harden MQTT for internet-exposed deployment [MANUAL]

**What:** If the MQTT broker is reachable from outside the Docker network,
`mosquitto.conf` must be updated with a password file and TLS certificates.
`allow_anonymous true` is acceptable only when the broker is on a private
internal network.

**How to test:** Attempt anonymous connection from outside the Docker
network — should be rejected.

**Completion condition:** Either (a) broker confirmed private/internal
only, or (b) password file and TLS certs configured in `mosquitto.conf`.

---

### M4 — Provision TimescaleDB on IBM Cloud [MANUAL]

**What:** Provision "IBM Databases for PostgreSQL" or a TimescaleDB-enabled
instance. Update `DATABASE_URL` in the production secrets store.
Run `alembic upgrade head` against the cloud database before first
deployment.

**How to test:** `alembic current` against the cloud DB returns the
head revision hash.

**Completion condition:** `alembic current` shows head. `psql` confirms
all four tables exist.

---

### M5 — Push image to IBM Container Registry and deploy to Code Engine [MANUAL]

**What:** The README already contains the correct commands:
```bash
docker build -t us.icr.io/<namespace>/ecoguardai:latest .
ibmcloud cr login
docker push us.icr.io/<namespace>/ecoguardai:latest
ibmcloud ce application create --name ecoguardai-api \
  --image us.icr.io/<namespace>/ecoguardai:latest \
  --port 8000 --env-from-secret ecoguardai-secrets
```
**Completion condition:** `curl https://<code-engine-url>/api/health`
returns `{"status":"healthy"}`.

---

## Execution Order

```
Agent tasks (can be done in one session):
  Task 1  Database migrations (Alembic)
  Task 2  APIKeyMiddleware wiring
  Task 3  scikit-learn in Dockerfile
  Task 4  Docker hardening
  Task 5  Root .gitignore
  Task 6  GitHub Actions CI
  Task 7  LICENSE file

Manual tasks (developer must do these):
  M1  IBM Cloud credentials in .env
  M2  Production secrets in .env
  M3  MQTT hardening (if public-facing)
  M4  Provision cloud database + run alembic upgrade head
  M5  Build, push, deploy to IBM Code Engine
```

After all agent tasks are complete, the only things left before the
application is live are the five manual steps, all of which are
credential/infrastructure actions that no automated tool can perform.
