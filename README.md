# Secure Multi-Agent Banking Assistant

A portfolio sandbox that demonstrates safe, authenticated AI-assisted banking
workflows. It uses synthetic data only; it is not connected to a financial
institution and must not be used with real customer data.

![Architecture diagram](docs/architecture.svg)

## Why this project exists

Many AI demos show an LLM calling a tool. This project focuses on the
engineering controls around that interaction:

- JWT-authenticated customer context that is carried into every tool call
- LangGraph routing to account, transaction, and service-request agents
- MCP tools that query only the signed-in customer's data
- validated LLM structured outputs and deterministic policy enforcement
- PII redaction before third-party model calls and a response-field allowlist
- two-step daily-limit, own-account transfer, and service-request workflows
- encrypted customer-scoped chat session for follow-ups and one pending proposal
- idempotency keys, expiry, revalidation, and durable audit records for writes
- request IDs and privacy-conscious operational logging

## Technology

Python, FastAPI, PostgreSQL, SQLAlchemy, LangGraph, MCP, OpenAI-compatible
LLM APIs, PyJWT, and Argon2 password hashing.

## Project status and safety boundaries

This is a deliberately constrained demonstration system. Write requests create
a pending proposal; a separate authenticated confirmation is required before a
change is applied. The app never guesses which proposal a bare "yes" refers to.

The login limiter is process-local and PII redaction is pattern-based. Chat
sessions are encrypted at rest with a local AES-GCM key and expire after a
short TTL; they do not store transcripts. Those controls are suitable for a
single-process, synthetic-data portfolio demo, not for production banking.
Full account numbers are retained only in the database; public account and MCP
responses expose `account_number_last4` only. If you already have a Compose
volume from before chat sessions existed, recreate it with
`docker compose down -v` before `docker compose up --build`.

## Local development

Requirements: Python 3.12+, a PostgreSQL-compatible database, and optionally
Ollama for local model inference.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Set the database URL and a strong local-only JWT secret in `.env`. The current
demo stack is self-contained and uses only versioned synthetic fixtures:

```sh
docker compose up --build
```

Then open `http://127.0.0.1:8000` and sign in with:

- Email: `demo.customer@banking.local`
- Password: `DemoBank@2026`

For the chat experience, configure an LLM in `.env`. The default uses Ollama
on the host. Native runs use `OLLAMA_BASE_URL`, while Docker Compose
automatically uses `OLLAMA_DOCKER_BASE_URL` (default:
`http://host.docker.internal:11434/v1/`) so the container can reach the host
server. Docker Compose deliberately defaults `LLM_DOCKER_PROVIDER` to
`ollama`, even if a native `.env` selects a remote provider; change that
Docker-specific variable only when remote inference is intentional. Set the
provider, model, and key values for an OpenAI- or Gemini-compatible provider
instead. Direct authenticated API and MCP endpoints work without an LLM
provider.

Run the API:

```sh
.venv/bin/uvicorn app.main:app --reload
```

Interactive API docs are enabled only when `API_DOCS_ENABLED=true`; do not
expose them publicly. To reset all synthetic local data, run
`docker compose down -v` and then start the stack again.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app scripts
.venv/bin/python -m ruff check app tests scripts
```

## Model evaluation

The repository includes two synthetic, non-production evaluation layers:

- `python -m scripts.evaluate_intents` checks intent routing, unsafe domain
  selection, and command routing.
- `python -m scripts.evaluate_chat` signs in to the running local Docker demo
  and checks response relevance, source-grounded facts, privacy leaks,
  command clarification safety, and latency. It only uses read queries plus a
  missing-account daily-limit request, so it does not create or confirm a
  banking proposal.

Both commands print only aggregate JSON metrics. Run the chat evaluation after
`docker compose up --build` and before changing the local model or prompt.

The suite covers authorization isolation, JWT handling, LLM-intent routing,
MCP context propagation, PII filtering, idempotency, and the explicit command
confirmation workflow. GitHub Actions runs the same lint, test, and compile
checks for pull requests and pushes to `main`.

## Documentation

- [Architecture](docs/architecture.md)
- [Quality controls and demo scenarios](docs/quality-controls.md)
