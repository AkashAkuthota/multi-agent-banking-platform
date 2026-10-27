# LLM quality and privacy controls

## PII handling

Before a message reaches the third-party intent classifier, the app redacts
email addresses, SSNs, and phone/account-like numeric sequences with at least
eight digits. Transaction merchant, category, and description text is also
redacted before response generation. Structured banking amounts remain intact,
and the presentation allowlist continues to omit internal IDs, raw account
numbers, customer IDs, and credentials. Direct account API and MCP responses
expose only `account_number_last4`, never a full account number.

This is targeted pattern-based protection, not comprehensive PII detection.
Names, addresses, unusual identifier formats, and sensitive information in
other free-text fields may not be detected. Do not send real customer data to
the portfolio deployment without an appropriate provider/data-processing
review and stronger controls.

## Intent evaluation

Run the synthetic live evaluation with the provider selected by `LLM_PROVIDER`.
For OpenAI, configure `OPENAI_API_KEY` and `OPENAI_MODEL`. To use the Gemini
API's free tier, create a key in [Google AI Studio](https://aistudio.google.com/apikey)
and set the following in your local `.env`:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-google-ai-studio-key
GEMINI_MODEL=gemini-3.8-flash
```

The default provider remains `openai`. Gemini uses Google's OpenAI-compatible
Chat Completions endpoint in JSON mode; Pydantic validates the complete output
before the application uses it. When the selected Gemini or OpenAI provider
fails to return valid structured output, the same request is retried with local
Ollama. If Ollama is unavailable or also fails, the operation fails explicitly.
Set `LLM_PROVIDER=openai` to use the OpenAI Responses API as the primary provider.
Do not commit `.env` or share API keys.

Free-tier access, quotas, model availability, and terms may change. Free-tier
content may be used by Google to improve products, so send only synthetic demo
data and review the current [Gemini API pricing and terms](https://ai.google.dev/gemini-api/docs/pricing)
before use. This does not make the portfolio application suitable for real
banking or customer information.

For local inference with Ollama, install Ollama, pull a model such as
`qwen3:1.7b`, and set:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1/
OLLAMA_MODEL=qwen3:1.7b
```

Ollama runs locally without a provider API key. Its OpenAI-compatible chat
endpoint is requested with JSON Schema constrained output, with Qwen thinking
disabled so the model returns the structured result within the output budget.
The model's local resource use and classification accuracy depend on the host
machine and model size; run the evaluation below before using a different model.

```sh
python -m scripts.evaluate_intents
```

The command prints aggregate exact-match accuracy and safety-violation counts;
it does not print individual prompts or model outputs. It exits unsuccessfully
if accuracy is below 90% or a safety violation occurs. Safety violations include
an unsupported request being treated as supported, selecting a read domain the
user did not request, or routing a supported command as a read. The cases are a
small regression set, not a substitute for a
representative, reviewed evaluation dataset.

## End-to-end chat evaluation

After starting the local Docker demo, run:

```sh
python -m scripts.evaluate_chat
```

This uses four synthetic, non-mutating cases: account balances, transaction
history, service-request history, and a daily-limit request that must ask the
user to select an account. It records aggregate case accuracy, relevance,
grounding, safety, availability, and latency metrics. A run fails when a
required fact is absent, a forbidden identifier is exposed, a clarification is
skipped, a request is unavailable, or accuracy is below the chosen threshold.
It does not print prompts, answers, or tokens, and it never creates a pending
proposal.

## Observability

## API documentation and bearer tokens

Swagger UI is intended for local development only. Its generated cURL examples
include the bearer token currently entered in the Authorize dialog, so do not
share screenshots, exported cURL commands, browser history, or logs containing
them. Set `API_DOCS_ENABLED=false` outside a trusted development environment to
remove the interactive `/docs` page. A bearer token shown outside its intended
audience must be treated as exposed.

`POST /auth/login` applies an in-process, per-email login-attempt limit (five
attempts in fifteen minutes by default). Configure it with
`LOGIN_RATE_LIMIT_ATTEMPTS` and `LOGIN_RATE_LIMIT_WINDOW_SECONDS`. This is
appropriate for the single-process portfolio deployment; use a shared limiter
such as Redis before operating multiple API instances.

HTTP responses include an `X-Request-ID`. Application logs record route
templates, status, duration, selected chat domains, execution outcome, and LLM
latency/token counts when the provider returns usage data. Prompts, responses,
authorization headers, query strings, and exception messages are intentionally
excluded from application telemetry.

Conversation state is a customer-scoped AES-GCM blob with a short TTL. It
stores a single pending proposal ID, previously listed account IDs, and last
referenced account — not raw chat transcripts. Confirm/cancel from the demo
console uses `POST /chat/pending-proposal/confirm` or `/cancel` against that
stored ID. A free-text “yes” never selects a proposal. Replace
`SESSION_ENCRYPTION_KEY` before any shared deployment.

## Sandbox daily-limit workflow

The project includes a sandbox-only account daily-limit workflow. For a
self-contained local database with versioned synthetic fixtures, run:

```sh
docker compose up --build
```

The database container applies the schema, workflow migrations, and synthetic
accounts automatically. Sign in to the local demo with
`demo.customer@banking.local` and password `DemoBank@2026`. These are public,
synthetic credentials; do not reuse them outside this repository.

For an external Neon database populated from separately supplied CSV fixtures,
use the legacy setup script:

```sh
NEON_DATABASE_URL='postgresql://<your-neon-connection-string>' \
  .venv/bin/python -m scripts.setup_neon_demo
```

The setup script reads CSV files from `~/Downloads` by default; set
`DEMO_CSV_DIR` to use another local directory. It creates the application
tables, imports customers, credentials, accounts, transactions, service
requests, and KYC records, resets imported identity sequences, then applies
[`001_daily_limit_workflow.sql`](../scripts/migrations/001_daily_limit_workflow.sql)
[`002_banking_commands.sql`](../scripts/migrations/002_banking_commands.sql),
and [`003_chat_sessions.sql`](../scripts/migrations/003_chat_sessions.sql).
The script uses the project's installed `psycopg` driver, refuses to proceed
if application tables already exist, and never drops existing objects. Run it
only against a new, empty database; the migrations are not repeatable. Treat the imported credentials CSV as
sensitive; never commit it, paste its contents into chat, or put the Neon URL
in source control.

When the imported fixtures do not already give a customer two accounts, setup
adds a synthetic active `ACC_DEMO_002` account for the customer owning
`ACC_001`, with a demo-only balance and the same currency. This enables testing
own-account transfers without changing imported customer records. The limit
migration seeds all accounts with a 10,000.00 limit in each
account's currency. The deterministic policy permits values from 10,000.00
through 50,000.00, with at most two decimal places. New accounts must be
initialized in `daily_account_limits` by trusted account-provisioning code
before limit tools can be used.

The read-only MCP tools are `get_my_accounts`, `get_my_transactions`, and
`get_my_service_requests`. Additional read access to a limit uses
`get_my_daily_limit`. The proposal-only write tools are
`propose_daily_limit_change`, `propose_internal_transfer`, and
`create_service_request`. Each can be reached through its named authenticated
Swagger route or through the natural-language `/chat` command dispatcher. The
LLM extracts typed command fields; server-side MCP tools and deterministic
policies validate and create proposals. No proposal applies a write.

For chat proposals, clients must send an `Idempotency-Key` header, reuse it
when retrying the same HTTP turn, and use a new key for a new user action.
Chat returns a proposal UUID for review and stores it as the session's single
pending proposal. Confirm or cancel from the console without pasting the UUID,
or send an explicit `confirm <proposal UUID>` / `cancel <proposal UUID>` in
chat. A bare “yes” never selects a pending proposal. The authenticated
dispatcher only looks up proposals belonging to the current JWT customer.

## Scenario testing in Swagger

Open `/docs` and authorize with a bearer token from `POST /auth/login`. Use
either `/chat` for a natural-language command or the named
`/mcp/tools/...` operations for direct MCP/Swagger testing. Chat commands
return a `command` object containing a `request_id`, `PENDING` status, and
`applied: false`; direct tool proposals return their tool result directly.
Keep the proposal ID for the explicit chat decision or matching confirmation
or cancellation endpoint.

Start with read-only checks:

1. `POST /mcp/tools/get_my_accounts` and verify the signed-in customer sees
   `ACC_001` and `ACC_DEMO_002`.
2. `POST /mcp/tools/get_my_transactions` and
   `POST /mcp/tools/get_my_service_requests` to check the seeded history.
   Transaction reads are bounded: chat/MCP defaults to the most recent 20
   transactions, and `GET /transactions/me` supports `limit` (1–100) and
   `offset` query parameters with a `next_offset` response field.
3. `POST /mcp/tools/get_my_daily_limit` with `{"account_id":"ACC_001"}`;
   the seeded limit should be `10000.00 INR`.

For an end-to-end chat proposal, send a new unique `Idempotency-Key` header
with the request:

```json
{"message":"Increase the daily limit on ACC_001 to 20000 INR"}
```

Expect a `PENDING` proposal with `applied: false`. The response includes its
proposal UUID and a `pending_proposal` object. Apply it with
`POST /chat/pending-proposal/confirm`, cancel with
`POST /chat/pending-proposal/cancel`, or send a second `/chat` request with
`{"message":"Confirm <proposal UUID>"}`. Confirmation is the only step that
applies the change. The chat must not be used to confirm by a bare “yes”.

Then exercise validation without applying mutations:

| Scenario | Proposal input | Expected result |
| --- | --- | --- |
| Valid daily-limit proposal | `ACC_001`, `20000.00`, a fresh idempotency key | `PENDING`, `applied: false` |
| Limit outside policy | `ACC_001`, `9000.00` | HTTP 422 |
| Unowned account | Another customer's account ID | HTTP 404 |
| Valid transfer proposal | `ACC_001` to `ACC_DEMO_002`, `100.00 INR` | `PENDING`, `applied: false` |
| Transfer over balance | Amount greater than the source balance | HTTP 422 |
| Same-account transfer | `ACC_001` to `ACC_001` | HTTP 422 |
| Unsupported service request | `ACCOUNT_CLOSURE` | HTTP 422 |
| Valid service proposal | `STATEMENT` and fresh idempotency key | `PENDING`, `applied: false` |

For a non-mutating direct-API write-path exercise, cancel each valid proposal with
`POST /mcp/tools/cancel_daily_limit_change`,
`POST /mcp/tools/cancel_internal_transfer`, or
`POST /mcp/tools/cancel_service_request`. This persists a cancellation and an
audit event but does not apply the banking operation. Proposals and their audit
history are intentionally durable and should not be manually deleted.

Only confirm a transfer if you intend to change both sample account balances;
only confirm a service proposal if you intend to add a service request. Use
fresh idempotency keys for each independent scenario. Reusing the same key
with different request details must return HTTP 409.

Limit proposals require an explicit owned account ID. Transfers are limited
to two active accounts owned by the same customer, in the same currency, with
available source balance and a maximum proposal of 50,000.00. Service-request
types are allow-listed to statements, cheque books, and card replacement.
Proposals expire after ten minutes, require idempotency keys, and persist
state-transition audit records.

For direct Swagger proposals, apply or cancel a pending proposal through the
matching authenticated HTTP action, such as `POST /mcp/tools/confirm_internal_transfer`,
`POST /mcp/tools/confirm_service_request`, or
`POST /mcp/tools/confirm_daily_limit_change`. Confirmation requires the request
ID and `{"confirmed": true}`; the service rechecks ownership, expiry, and
relevant account state before committing. Transfer confirmation atomically
updates both owned-account balances and records the corresponding ledger
transactions.

Chat writes are proposal-only until the customer confirms through the session
action or a later chat turn that includes the exact proposal UUID. This is a
portfolio sandbox, not a bank-integrated transaction or credit-card limit
change. The current identity
context is established by this application's JWT authentication. A bank-provided MCP server would need an explicit adapter
for that bank's transport, authentication/delegation model, tool contract,
and authorization semantics; this local workflow does not automatically work
against an external bank MCP server.
