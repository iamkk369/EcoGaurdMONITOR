# EcoGuardAI

**Agentic AI platform for real-time smart industrial pollution monitoring** in the Golden
Corridor — Vapi · Ankleshwar · Vatva, Gujarat, India — powered by **IBM Granite** on
**IBM watsonx.ai**.

> Submission for **Challenge 9 – Smart Industrial Pollution Monitoring for Golden Corridor**
> (Vapi–Ankleshwar). Built as part of the IBM workshop initiative.

---

## Overview

EcoGuardAI is a real-time environmental monitoring and compliance platform for one of India's most
industrially dense and environmentally stressed regions. It ingests **simulated** sensor readings
from industrial effluent and emission units, evaluates them against regulatory limits, detects
exceedances, classifies their severity, issues regulatory alerts, computes air / water quality and
public-health risk, and renders everything through a live web dashboard.

The system currently operates with a **sensor simulator** in place of a physical sensor network, so
it demonstrates a complete monitoring, analysis, alerting, and health-risk pipeline that can be
re-pointed at real sensor feeds (REST or MQTT) without backend changes.

**Why real-time pollution monitoring matters:** effluent and emission exceedances are often
detected only after the damage is done. Continuous, automated monitoring lets regulators and
communities react to breaches as they happen instead of discovering them late.

The project name is **EcoGuardAI**; the web dashboard uses the operating brand
**"EcoGaurdMONITOR"**.

---

## Key Features

Verified in the current implementation:

- **Real-time sensor monitoring** — a sensor simulator streams readings into the API (REST and
  MQTT ingestion paths).
- **Air-quality monitoring** — AQI computed from air pollutants (SO2, NOx, PM2.5, PM10, CO, O3)
  against bundled NAAQ-style limits.
- **Water-quality monitoring** — effluent parameters (BOD, COD, TSS, pH, TDS, heavy metals)
  against IS:10500 / CPCB / GPCB limits.
- **Violation detection** — threshold + ML (Isolation Forest when `scikit-learn` is available)
  anomaly confirmation with severity classification (LOW / MEDIUM / HIGH / CRITICAL).
- **Regulatory alerts** — severity-based escalation (email / webhook / dashboard) with per-station
  rate limiting.
- **Public-health risk** — AQI / WQI indices, affected-radius and population-risk estimates, and
  plain-language health advisories.
- **Station monitoring** — 12 sample industrial stations across Vapi, Ankleshwar, Vatva, Bharuch
  and Sachin are bundled as sample data.
- **Live dashboard** — HTML / CSS / JavaScript frontend with charts (Chart.js), served by the
  backend.
- **SSE live updates** — Server-Sent Events feed for browser live updates.
- **IBM Granite analysis** — generates formal violation reports and health advisories (see
  *IBM Technology*).
- **Multi-agent architecture** — five autonomous agents coordinated by an event-bus orchestrator.

> **Not yet implemented** (do not treat as available): a conversational "EcoAI" chat backend
> endpoint, Redis-backed caching / rate limiting in application code, Streamlit UI, Langflow
> orchestration, and RAG / ChromaDB retrieval. See *Current Status*.

---

## Agentic AI Architecture

EcoGuardAI uses a lightweight multi-agent design coordinated by a central `AgentOrchestrator`.
Agents subscribe to event types and respond asynchronously:

**Implemented agents**

| Agent | Responsibility |
| --- | --- |
| `EmissionMonitoringAgent` | Ingests sensor readings, computes AQI, flags threshold exceedances, emits `emission.violation`. |
| `ViolationDetectionAgent` | Confirms anomalies with `Isolation Forest` (threshold-only when sklearn is unavailable), classifies severity, generates an IBM **Granite** violation report, emits `violation.detected`. |
| `RegulatoryAlertAgent` | Escalates confirmed violations via email / webhook / dashboard with per-station rate limiting. |
| `PublicHealthRiskAgent` | Calculates AQI / WQI, estimated affected radius + population risk, and produces a **Granite** health advisory. |
| `DashboardAgent` | Aggregates live statistics and broadcasts them to SSE subscribers. |

Event flow: `sensor.reading → emission.violation → violation.detected → alert / health /
dashboard`. Each agent is fail-safe: a persistence or downstream failure never stops the pipeline.

There are no other agents in the current source. See *Future Scope* for planned agents.

---

## IBM Technology

EcoGuardAI uses **IBM watsonx.ai** to run **IBM Granite** large-language models through the
official `ibm-watsonx-ai` Python SDK (`APIClient`, `Credentials`, `ModelInference`).

**What Granite currently does in the project:**

- **Violation reporting** — generates a structured regulatory report for each confirmed
  exceedance (stored as `report_text` and returned by the API).
- **Health advisories** — generates plain-language public-health risk assessments for affected
  communities.

**Credentials** are loaded only from environment variables and never appear in code or the repo:

```
IBM_CLOUD_API_KEY
IBM_WATSON_URL
IBM_GRANITE_MODEL_ID
WATSONX_PROJECT_ID
```

If the SDK is missing or credentials are not configured, `GraniteClient` runs in **stub mode** —
it returns a deterministic placeholder instead of making a live call, so all other features still
work offline. A standalone connectivity probe lives in `scripts/test_granite_integration.py`.

> No API keys, credentials, or tokens are stored in the repository. See the provided
> `.env.example` for the full list of required variables.

---

## System Architecture

```mermaid
flowchart LR
    SIM[Sensor Simulator] --> REST[POST /api/monitoring/readings]
    MQTT[MQTT broker] --> ING[MQTTIngestionClient]
    REST --> ORCH[AgentOrchestrator]
    ING --> ORCH

    ORCH --> EM[EmissionMonitoringAgent]
    EM -->|emission.violation| VI[ViolationDetectionAgent]
    VI -->|violation.detected| RA[RegulatoryAlertAgent]
    VI -->|violation.detected| HR[PublicHealthRiskAgent]
    VI -->|violation.detected| DA[DashboardAgent]

    VI --> GR[IBM Granite via watsonx.ai]
    HR --> GR

    VI --> DB[(PostgreSQL / TimescaleDB image)]
    RA --> DB
    DB --> API_2[FastAPI REST]
    DA --> SSE[SSE /api/stream]
    SSE --> UI[HTML/CSS/JS Dashboard]
    API_2 --> UI
```

The default environment targets local development with `docker-compose`, which provisions a
PostgreSQL / TimescaleDB image, MQTT (Mosquitto), Redis, and pgAdmin. Redis is provisioned in
Compose but **not yet used by the application code**; TimescaleDB is provided as the PostgreSQL
image (no hypertable-specific code exists yet).

---

## Technology Stack

Only technologies actually used by the repository:

- **Python 3** · **FastAPI** · Pydantic v2
- **SQLAlchemy** (async) + **asyncpg** · **PostgreSQL** (TimescaleDB image in Compose)
- **Alembic** (migrations)
- **MQTT** (`paho-mqtt`) + Mosquitto broker
- **IBM watsonx.ai** · **IBM Granite** (`ibm-watsonx-ai` SDK)
- **HTML / CSS / JavaScript** dashboard + **Chart.js**
- **Server-Sent Events**
- **Docker** / **Docker Compose**
- **pytest** / **pytest-asyncio** (anyio)

---

## API

Base path `/api` (interactive docs at `http://localhost:8000/docs`).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/monitoring/stations` | List monitoring stations |
| GET | `/api/monitoring/stations/{id}/readings` | Recent readings for a station |
| POST | `/api/monitoring/readings` | Ingest a single sensor reading |
| POST | `/api/monitoring/readings/bulk` | Bulk-ingest a batch of readings |
| GET | `/api/monitoring/aqi` | Current AQI per station |
| GET | `/api/violations` | List violations (filters available) |
| GET | `/api/violations/{id}` | Violation detail incl. the Granite report |
| PATCH | `/api/violations/{id}/status` | Update violation status |
| GET | `/api/alerts` | List alerts |
| POST | `/api/alerts/test` | Send a test alert |
| GET | `/api/health` | System health + subsystem status |
| GET | `/api/health/risk-assessment` | Current risk levels per station |
| GET | `/api/stream` | Server-Sent Events live-dashboard feed |

---

## Dashboard

The repository ships a browser dashboard (`dashboard/`) served by the backend (`/dashboard`). The
current implementation provides:

- **Live Home / Dashboard** pages fed by SSE — station AQI, active violations, compliance rate.
- **Air / water quality** monitoring pages backed by the monitoring REST API.
- **Industrial units** page listing the bundled sample stations.
- **Violations & Alerts** page listing confirmed violations and dispatches.
- **Health Risk** page showing per-station risk via `/api/health/risk-assessment`.
- **History** page plotting live-session AQI trends (no historical-AQI backend endpoint yet).
- **AI Analysis** page presenting the IBM Granite **violation** reports.

The **EcoGaurdMONITOR** shell also contains an **EcoAI assistant** UI and an *AI Analysis* page
that advertise IBM Granite. The assistant's chat posts to `/api/ai/chat`, which is **not
implemented in the backend yet** — the UI degrades honestly with an informative message.

Known dashboard limitations (matching the backend): live-session history only, no dedicated
historical-AQI endpoint, and WQI is computed inside the health-risk agent but not exposed as its
own API field.

---

## Data & Regulatory Standards

This repository bundles **reference / sample data** (not live government API integrations):

- `data/pollutant_limits.json` — permitted limits with referenced standards (NAAQ 2009,
  IS:10500, CPCB Effluent Standards, GPCB Schedule-I, CPCB CETP norms, CPCB Pharma, and
  industry-specific limits for chemical, textile, CETP, pharmaceutical and fertilizer units).
- `data/sample_sensors.json` — 12 sample stations (Vapi, Ankleshwar, Vatva, Bharuch, Sachin,
  CETP) each with air / water / heavy-metal sensor definitions.
- `data/population_zones.json` — population density and vulnerable-group context used by the
  health-risk agent.

These are **bundled sample configurations**, not a real-time CPCB / GPCB live data feed.

---

## Project Structure

```
EcoGuardAI/
├── agents/          # Five autonomous AI agents
├── api/             # FastAPI app, routes, middleware
├── core/            # Granite client, IBM Cloud client, DB, MQTT, orchestrator
├── models/          # Pydantic v2 request/response models
├── services/        # Fail-safe persistence for violations & alerts
├── config/          # Settings (env-driven) + logging
├── dashboard/       # HTML/CSS/JS dashboard consuming SSE + REST
├── data/            # Sample / regulatory config
├── scripts/         # Sensor simulator, DB seeder, historical + scripted data
├── tests/           # pytest test suite
├── alembic/         # Database migrations
├── .github/         # GitHub Actions CI
├── archive/         # Earlier dashboard copy (not served)
├── Dockerfile
└── docker-compose.yml
```

---

## Installation / Local Setup

### 1. Prerequisites

- Python 3.10+ (3.11 recommended — see `Dockerfile` / `.github/workflows/ci.yml`)
- A PostgreSQL-compatible server and an MQTT broker — or run the bundled infrastructure via Docker
- IBM watsonx credentials (optional — the project runs in stub mode without them for offline demo)

### 2. Configuration

Copy the environment template and edit the values:

```bash
cp .env.example .env
```

For live IBM Granite calls through watsonx, provide:

```
IBM_CLOUD_API_KEY=your_key
IBM_WATSON_URL=https://us-south.ml.cloud.ibm.com
IBM_GRANITE_MODEL_ID=ibm/granite-4-h-small
WATSONX_PROJECT_ID=your_project_id
```

Without these, the Granite client runs in stub mode (offline-safe). The default `DATABASE_URL` is
`postgresql://ecoguard:ecoguard@localhost:5432/ecoguardai`.

### 3. Infrastructure (optional, recommended)

```bash
docker compose up -d db mqtt redis pgadmin
```

This provisions PostgreSQL (TimescaleDB image), Mosquitto (MQTT), Redis, and pgAdmin. Database
tables are created automatically for development (`create_all`) and tracked via Alembic.

### 4. API + dashboard

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload     # starts the API on port 8000
```

- API docs: <http://localhost:8000/docs> (root redirects there)
- Dashboard: <http://localhost:8000/dashboard>

### 5. Data tools

```bash
alembic upgrade head            # run DB migrations
python scripts/seed_database.py   # seed the 12 sample stations
python scripts/generate_historical_data.py   # optional 30 days of readings
python scripts/simulate_sensors.py           # real-time sensor simulator
python scripts/scripted_events.py trigger VAPI-001 PM25 --severity critical   # demo event
```

> The commands above reference only the scripts and utilities shipped in this repository. The
> simulator and data generators post to `http://localhost:8000`, so start the API first.

---

## Testing

```bash
pytest tests/ -v
```

**Verified result in this workspace** (Python 3.14.2, no PostgreSQL available): **93 passed,
1 failed, 20 errors**.

- The single failure is the DB-dependent persistence test (`tests/test_persistence.py`), which
  attempts a real PostgreSQL connection and fails only when one is unreachable.
- The 20 errors all come from `tests/test_api.py` in this environment, which relies on an
  `async_client` fixture that is **not defined** (no `conftest.py` exists in the repository), so
  the API-test classes cannot spin up.

This is the **real, observed** test result — it is not a fabricated "all green" claim, and no test
files were modified to reach it. The CI workflow (`.github/workflows/ci.yml`) is configured for
Python 3.11 and runs `pytest tests/`.

---

## Current Status

**Implemented & working** (verified in source):

- FastAPI REST API + SSE live-dashboard feed
- Multi-agent orchestrator (5 agents) with threshold anomaly detection and severity classification
- Violation + alert persistence in PostgreSQL (idempotent, fail-safe)
- IBM Granite / watsonx.ai integration for violation reports and health advisories
- Sensor simulator, DB seeder, historical data generator, scripted-events tool
- HTML / CSS / JS dashboard with Chart.js + SSE
- Alembic migrations, Docker / Compose, CI workflow
- A pytest test suite exists (see *Testing* for the honest result)

**Limited by data / environment**

- Data is **simulated** — no physical sensors and no live CPCB/GPCB feeds.
- Email / webhook alerts only fire when the corresponding SMTP / webhook credentials are supplied.
- TimescaleDB hypertable-specific code is not implemented; Redis is provisioned but not used by
  the application code.

**Planned / not yet implemented**

- Backend conversational AI endpoint (`/api/ai/chat`) and on-demand Granite analyses
- Streamlit UI (the dashboard is HTML/CSS/JS)
- Langflow orchestration
- RAG / vector search (ChromaDB)
- Redis-backed caching / rate limiting in app code

---

## Future Scope

- **Advanced historical analysis** — historical AQI endpoints and deeper trend reporting.
- **RAG** — retrieval-augmented generation over environmental regulations and station history.
- **Langflow orchestration** — visual agent-workflow design.
- **Expanded IBM Granite capabilities** — conversational EcoAI Assistant and on-demand analysis.
- **Cloud deployment** — IBM Cloud / Code Engine with real credentialed infrastructure.
- **Additional sensor integrations** — real IoT / hardware sensor feeds and official
  CPCB / GPCB data.

---

## Submission

This repository contains the required **Challenge 9** materials:

- **Application manifest** — `app.json`
- **Problem statement (PDF)** — `Challenge 9 – Smart Industrial Pollution Monitoring for Golden
  Corridor (Vapi–Ankleswar).pdf`
- Source code (backend, frontend, agents, IBM Granite / watsonx.ai integration), data, tests,
  docs, Docker and CI.

> The evaluator-required **project-presentation (PPTX)** is **not currently present** in this
> directory. Please copy it into the repository before publishing to GitHub.

---

## License

[MIT](./LICENSE) © EcoGuardAI Contributors.