# Jokes Arena

A mobile-friendly arena for anonymous, binary joke comparisons. Selecting a card
saves a vote and reveals both models. The leaderboard shows saved preferences.
The current frontend uses HTML, CSS, and JavaScript; the backend uses Python and
SQLite. Production serving uses Gunicorn. Vue and Rust are not used.

See [deployment instructions](deployment/README.md) for the VM, Caddy, and
GitHub Actions setup. Joke receipts, vote databases, and credentials remain private.

## Run locally

```sh
python3 -m arena.server
```

Open http://127.0.0.1:4173/preview/. The database defaults to `runtime/arena.db`.
The service serves only the three frontend files, not the repository or database.
An empty database has no pairs; seed it from a validated generation receipt:

```sh
python3 -m arena.server \
  --import-receipt /absolute/private/path/receipt.jsonl
```

Importing the same receipt again preserves votes and adds no duplicate pairs.
The service reads its active competitor roster from `config/models.json`. Old
pairs and votes remain stored but are excluded from new assignments and active
leaderboard totals. An already assigned legacy pair can still be completed.

Select either card to save a vote and reveal both model names. Select Next pair
when ready to continue. Model labels are hidden before voting. Open Leaderboard
in the header to see model results. Each browser session
sees each pair once, with one outstanding comparison. Vote retries are idempotent;
changing an already saved choice returns HTTP 409. Clearing cookies creates a new
session. These are not verified unique people and do not prevent deliberate abuse.

The initial three pairs were generated on 2026-10-10 by `openai/gpt-5-mini` and
`anthropic/claude-haiku-4.5`, using calendar, coffee, and umbrella. Both models
received the same word and instructions. Six successful requests produced six
validated jokes. OpenRouter reported a total cost of $0.000895. No additional
provider calls are needed for voting. This small pool does not establish a general
model ranking.

## REST contract

The expanded batch generated 20 complete pairs (40 playable jokes) covering
23 models on 2026-10-10. The active leaderboard has 24 configured profiles;
Muse Spark 1.3 has zero jokes because both requests returned HTTP 403.
Qwen3.8 Max omitted its required word once; Step 5 Preview exhausted its output
allowance once. Those four incomplete pairs were excluded. No retries were made.
The 48 requests reported $0.041091969 in usage charges. The two HTTP 403 attempts
had no cost metadata, so this is reported cost, not independently verified total
spending. Generation records remain private under the ignored `runtime/` folder.

- `GET /api/health`: service and database schema health.
- `GET /api/ready`: populated active pool, schema health, and deployed revision.
- `POST /api/battles`, JSON `{}`: recover the outstanding comparison or assign an
  unseen pair. Sets an HttpOnly, SameSite=Strict cookie for new sessions. The
  response has `id`, `word`, and two ordered `jokes`, without model attribution.
- `POST /api/battles/{id}/vote`, JSON `{"choice":"left"}` or
  `{"choice":"right"}`: save the browser session's choice. Returns `saved: true`
  and `models`, two display names in the same order as the battle jokes. Names
  are disclosed only after an owned vote is saved; same-choice retries return
  the same attribution.
- `GET /api/leaderboard`: model totals, win rate, unique joke count, pool size,
  and total saved votes. Win rate is wins divided by comparisons. No-vote rates
  are null. Every vote contributes one win and one loss.

Both POST endpoints require an exact matching Origin and a JSON object bounded
at 4096 bytes. Configure the expected origin with `--origin` or `ARENA_ORIGIN`.
Configure database location with `--database` or `ARENA_DATABASE`. The service
binds to 127.0.0.1. Its standard-library HTTP server is for local development;
Gunicorn serves production through the container configuration. Caddy terminates
HTTPS. Compose keeps the database on the host. Deployment checks readiness and
backs up SQLite before upgrades. Public voting currently has no rate limiting.

## Expanded word bucket and chart roster

`config/words.json` contains 100 everyday single-word prompts.
`config/models.json` contains the 26 chart entries. The public OpenRouter catalog
verified 24 models on 2026-10-10. Gemini 4 Argon and K2 Horizon are unavailable.
Muse Spark uses the explicit available version 1.3 because the chart omits a
version. Mistral Large 4 uses the catalog name rather than the chart Preview label.

The user selected lower reasoning. Reasoning is disabled where supported; mandatory
reasoning uses the lowest advertised effort. This is not a reproduction of the
chart reasoning settings. Profiles identify API model, reasoning, and prompt
version. Imported receipts must match the profile exactly. Stored settings cannot
change under an existing profile identity.

Plan a fresh private batch with no paid calls:

```sh
python3 -m arena.populate --directory /private/tmp/jokes-plan-new --budget 2
```

Generate into a different fresh directory using the environment API key:

```sh
python3 -m arena.populate --directory /private/tmp/jokes-batch-new --budget 2 --execute
```

The batch forms a shuffled single cycle: 24 pairs, 48 requests, two planned
appearances per available model, and 24 random unique words. It verifies current
model availability and reasoning support before requests. The starter budget
cannot exceed $2. Provider routing caps prompt, completion, and request prices;
requests require parameter support and disable provider fallbacks. Each request
asks for at most 2048 output tokens and reserves 4096 output plus 2048 input tokens
as billing headroom. Reasoning tokens are billable. This reservation is a
conservative client guard, not an account-level OpenRouter spending limit.

The generator reserves each request before sending it and includes failures and
timeouts. It stops if the next reservation would exceed the remaining budget.
Reported costs exceeding reservations increase the amount reserved. It makes no
automatic retries. A provider billing anomaly can only be detected after a
response; keep the account-level provider spending limit for strict billing control.

Private output contains plan.json, attempts.jsonl, a receipt.jsonl containing
complete pairs only, and summary.json. Inspect actual published appearances:
failures can make the realized pool uneven. Import the complete receipt with
`--import-receipt`. The leaderboard exposes the word bucket and unavailable-model
notes. Every model initially faces only two opponents. This pool is an early
preference sample, not a general model ranking.

## Legacy two-model generator

The user authorized the starter batch without requiring a separate capped key.
`--require-key-limit` optionally checks a fixed provider credit limit before paid
requests. For hidden interactive key entry without saving it to a file or command
history, run `python3 scripts/run_expanded_batch.py --directory /private/tmp/jokes-batch-new --execute`.

Provide `OPENROUTER_API_KEY` through the process environment. The generator does
not store the key or send it to the frontend. Keep receipts outside public files.
Use fresh paths because generation outputs are exclusive-create files.

```sh
python3 scripts/generate_preview.py \
  --output /private/tmp/jokes-new-pairs.js \
  --receipt /private/tmp/jokes-new-receipt.jsonl \
  --words calendar coffee umbrella
```

The receipt contains model attribution and generation usage. Import the receipt
into SQLite; the sanitized JavaScript export is a legacy prototype artifact and
is no longer served. The generator makes at most six requests with a 60-second
request timeout and no retries. Invalid or incomplete batches produce no public
asset. Validation checks attribution, completion status, required-word inclusion,
and a 40-word limit. It does not prove originality or humor quality.

## Verify

```sh
python3 -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

If coverage is already installed:

```sh
python3 -m coverage run --source=arena,scripts,deployment -m unittest discover -s tests
python3 -m coverage report --fail-under=100 -m
```

HTTP tests bind to a temporary localhost port. SQLite tests use temporary files.
Provider tests mock transport and make no paid requests. Frontend tests exercise
DOM events, rendering, and retry behavior in a lightweight DOM test double.
Real-browser verification is documented in `preview/VERIFICATION.md`.
