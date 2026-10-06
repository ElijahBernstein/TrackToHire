# TrackToHire

TrackToHire is a full-stack job application tracker that helps users organize applications and review recruiting-email updates in one place. It combines a FastAPI and PostgreSQL backend with a responsive vanilla JavaScript frontend, optional Gmail integration, rule-based filtering, and AI-assisted email classification.

The application is deployed at [tracktohire.app](https://tracktohire.app). This repository began tracking the project after its initial production release, so its first commit is an honest import of the existing application rather than a reconstructed development history.

## Features

- Account creation and login with bcrypt password hashing and expiring JWTs
- User-owned application creation, listing, full editing, status history, and deletion
- Dashboard search, filtering, sorting, pagination, and status counts
- Optional Gmail connection through OAuth 2.0 using the read-only Gmail scope
- Manual Gmail synchronization and user-controlled automatic monitoring
- Rules-first email screening with AI classification for relevant or ambiguous messages
- Application matching restricted to the authenticated user's records
- Reviewable Gmail suggestions that users can confirm or ignore
- Durable, deduplicated PostgreSQL jobs for Gmail Pub/Sub notifications
- Separate background worker for email processing and Gmail watch renewal
- Encrypted Gmail refresh-token storage

Application fields can be edited without deleting and recreating a record. Partial API updates preserve omitted fields, and an edit creates a history event only when it changes the application status.

## Architecture

```mermaid
flowchart TD
    U[Browser] --> F[Static frontend]
    F --> A[FastAPI API]
    A --> D[(PostgreSQL)]
    G[Gmail and Pub/Sub] --> A
    A --> J[(Processing jobs)]
    W[Gmail worker] --> J
    W --> D
    W --> G
```

The Gmail pipeline keeps incoming web requests short and durable:

1. Gmail sends a mailbox-change notification through Google Pub/Sub.
2. The FastAPI webhook validates the shared verification token, saves a deduplicated processing job, and returns `204 No Content` quickly.
3. The separate Gmail worker claims pending jobs and retrieves new message information.
4. Rules discard clearly unrelated messages before AI is considered.
5. Relevant messages are classified and matched only against applications owned by the connected user.
6. A stored suggestion is shown to the user for confirmation or dismissal.
7. Confirmed changes update the application and create a status-history record.

Email bodies are used transiently for analysis and are not stored in the database. Saved suggestions retain only the metadata and result needed for review and deduplication.

## Technology Stack

| Layer | Technologies |
|---|---|
| Frontend | HTML, CSS, vanilla JavaScript |
| API | Python 3.11, FastAPI, Uvicorn, Pydantic v2 |
| Database | PostgreSQL, SQLAlchemy 2.x |
| Authentication | bcrypt, PyJWT, HTTP Bearer tokens |
| Email integration | Google OAuth 2.0, Gmail API, Google Pub/Sub |
| Classification | Rules-first analysis, OpenAI API fallback |
| Secret protection | Environment variables, Fernet token encryption, AWS Secrets Manager in production |
| Containers and cloud | Docker, Amazon ECR, CloudFront, Route 53, ACM, WAF, RDS |

## Repository Structure

```text
TrackToHire/
├── backend/
│   ├── tests/
│   │   └── test_email_monitoring.py
│   ├── .dockerignore
│   ├── .env.example
│   ├── Dockerfile
│   ├── ai_service.py
│   ├── auth.py
│   ├── database.py
│   ├── gmail_oauth.py
│   ├── gmail_service.py
│   ├── gmail_worker.py
│   ├── main.py
│   ├── models.py
│   ├── requirements.txt
│   ├── schemas.py
│   └── token_encryption.py
├── frontend/
│   ├── app.js
│   ├── index.html
│   ├── style.css
│   └── tracktohire-logo.svg
├── .gitignore
└── docker-compose.yml
```

## Local Backend Setup

### Prerequisites

- Python 3.11 or newer
- Docker Desktop and Docker Compose
- Git

Google OAuth and OpenAI credentials are optional for basic application tracking, but they are required to exercise the corresponding Gmail and AI features.

### 1. Clone the repository

```powershell
git clone https://github.com/ElijahBernstein/TrackToHire.git
Set-Location TrackToHire
```

### 2. Start PostgreSQL

```powershell
docker compose up -d db
```

The development database defined by `docker-compose.yml` is available on `localhost:5432`.

### 3. Create and activate a virtual environment

```powershell
py -3.11 -m venv backend\venv
backend\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

### 4. Configure environment variables

```powershell
Copy-Item backend\.env.example backend\.env
```

Fill in `backend/.env` locally. Never commit this file.

```dotenv
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/job_tracker
JWT_SECRET_KEY=
GMAIL_TOKEN_ENCRYPTION_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/gmail/oauth/callback
OPENAI_API_KEY=
OPENAI_MODEL=
GMAIL_PUBSUB_TOPIC=
GMAIL_PUBSUB_VERIFICATION_TOKEN=
GMAIL_WORKER_POLL_SECONDS=5
GMAIL_WORKER_MAX_ATTEMPTS=3
GMAIL_WATCH_RENEWAL_CHECK_SECONDS=3600
GMAIL_WATCH_RENEWAL_LEAD_SECONDS=86400
```

Generate development secrets with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use the first output for `JWT_SECRET_KEY` and the second for `GMAIL_TOKEN_ENCRYPTION_KEY`.

### 5. Run the API

```powershell
Set-Location backend
uvicorn main:app --reload
```

The API runs at `http://localhost:8000`. Interactive API documentation is available at `http://localhost:8000/docs`.

### 6. Run the background worker when testing Gmail monitoring

Open a second activated terminal in `backend/`:

```powershell
python gmail_worker.py
```

Automatic Gmail monitoring also requires a publicly reachable Pub/Sub webhook and valid Google Cloud configuration. Basic application tracking works without Gmail.

## Local Frontend

The current frontend constants in `frontend/app.js` point to the production API. For local full-stack testing, temporarily change the three API constants at the top of that file from `https://api.tracktohire.app` to `http://localhost:8000`, then serve the directory from the repository root:

```powershell
py -m http.server 5500 --directory frontend
```

Open `http://localhost:5500`. The backend CORS configuration permits both `localhost:5500` and `127.0.0.1:5500`.

Do not commit the temporary local API-address change. A future improvement is to make the frontend API base URL environment-aware.

## Tests

The regression tests use a temporary SQLite database created before the application modules are imported, preventing them from connecting to the configured PostgreSQL database.

With the backend virtual environment active:

```powershell
Set-Location backend
python -m unittest discover -s tests -v
```

The current suite covers ownership enforcement, Gmail suggestion confirmation, email extraction, classification safeguards, matching thresholds, deduplication behavior, and monitoring controls.

## Security Design

- Passwords are hashed with native bcrypt and never stored in plaintext.
- Protected endpoints derive ownership from the authenticated JWT user rather than accepting a frontend-provided user ID.
- Application, Gmail connection, suggestion, and history queries enforce user ownership.
- JWTs use HS256, contain the database user ID in `sub`, and expire after one hour.
- Gmail access uses OAuth 2.0 with the `gmail.readonly` scope; TrackToHire never requests or stores a Gmail password.
- OAuth refresh tokens are encrypted with Fernet before database storage.
- Pub/Sub webhook requests require a verification token and are deduplicated in PostgreSQL.
- Secrets and local environment files are excluded from Git and supplied through environment variables or AWS Secrets Manager.
- The backend container runs as a non-root user.

## Production Deployment

The production environment is primarily hosted in AWS `us-west-2`. The frontend and API are delivered through separate CloudFront distributions and TrackToHire domains managed with Route 53. CloudFront uses an ACM certificate in `us-east-1` and is protected by an associated AWS WAF configuration.

The FastAPI service and Gmail worker run as separate Docker containers built from the same backend image stored in Amazon ECR. PostgreSQL runs on Amazon RDS, and production secrets are supplied through AWS Secrets Manager.

The repository does not yet contain a complete reproducible infrastructure definition or deployment runbook. Those will be documented through future tracked changes after the current production configuration is verified.

## Current Limitations and Roadmap

- Make the frontend API base URL environment-aware.
- Expand isolated API and authentication test coverage.
- Add automated continuous integration checks.
- Document the verified production deployment and rollback process.
- Add reviewed database migrations for future schema changes.

## API Documentation

When the backend is running, FastAPI exposes interactive OpenAPI documentation at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## License

No license has been added yet. All rights are reserved unless a license is added in a future commit.
