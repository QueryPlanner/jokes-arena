# Jokes Arena implementation status

The latest requested scope is binary card selection plus a saved leaderboard,
using the user's pasted OpenAI design context. Ties, neither-funny, and skip were
removed. This is a local implementation, not a production deployment.

## Completed

- Existing real OpenRouter jokes: GPT-5 mini and Claude Haiku 4.5, three pairs,
  six validated completions. No new paid generation in the leaderboard work.
- Python standard-library REST service with SQLite WAL and foreign keys.
- Atomic, idempotent import from private validated model-attribution receipts.
- Anonymous comparison assignment with randomized side order, session ownership,
  one outstanding pair, no repeated pairs per session, exposure-aware sampling.
- Binary votes with idempotent retries, conflicting-vote rejection, and correct
  side-to-model attribution. Concurrent assignment and vote tests.
- Leaderboard showing win rate, wins, losses, comparisons, unique jokes, total
  votes, and pool size. The UI assigns shared ranks for equal win rates.
- Plain HTML/JavaScript frontend with Battle and Leaderboard, stacked mobile cards,
  keyboard access, automatic advance, failed-vote recovery, and pool exhaustion.
- Exact Origin checks, bounded JSON bodies, hashed session cookies, and literal
  static-file allowlist. Database and private receipts are not served.
- Automated Python and frontend tests, plus isolated real-browser verification.

## Remaining original scope

The user originally requested Vue. The current frontend is plain JavaScript;
Vue and a production backend stack have not been selected or installed. The prior
questions about dependency choices remain unanswered. A Vue conversion needs
approval to add its dependencies under the supplied development instructions.

The VM has not received this code. Before deployment, decide the production
server, model-generation workflow, process supervision, HTTPS, request limits,
SQLite persistence and backups, then prepare and verify a deployable artifact.
Obtain confirmation for the concrete deployment before changing the VM.

## Interpretation

Each vote gives one model a win and the other a loss. Win rate describes votes
on the current sampled jokes and opponents. It is not a fitted statistical
preference model or a claim about general humor ability. A three-pair pool is
small. Anonymous browser sessions are not verified unique people.

## Independent review

The plan reviewer required binary voting with no skip, server-owned attribution,
atomic receipt imports, owner-bound votes, duplicate-safe transactions, exact
Origin checks on both POST routes, bounded input, safe static serving, correct
no-vote display, and isolated validation data. Those changes are implemented.

## Previously inspected sources

- https://invertedpassion.com/how-funny-are-the-frontier-ai-models/
- https://help.arena.ai/articles/4489017547-how-to-use-battle-mode
- https://arena.ai/
- https://openrouter.ai/docs/api_reference/overview

These sources were inspected in earlier work on 2026-10-10. The design now follows
the user's supplied document rather than a new web search.

## Expanded pool work

The user requested the chart roster and more words, then selected lower reasoning
and a $2 starter budget. Added a 100-word bucket and 26 chart entries with 24
verified OpenRouter IDs. Gemini 4 Argon and K2 Horizon remain unavailable.
Muse Spark 1.3 is an explicit version selection for the unversioned chart label.

Generation plans a shuffled cycle with 24 pairs and 48 requests. Exact active
profiles preserve requested model, reasoning, and prompt version; old data stays
stored separately. Dry-run planning, live catalog checks, provider price caps,
client budget reservation, complete-pair publication, and realized participation
reports support bounded generation. Account-level provider spending remains
separate from client reservation checks. Vue and VM deployment remain pending.

The user subsequently waived the separate provider key limit and authorized
generation first. The completed batch made 48 requests and published 20 complete
pairs from 23 profiles. Muse Spark returned HTTP 403 twice; Qwen failed required
word validation once; Step exhausted its output allowance once. No retries or
substitutions were made. Reported charges were $0.041091969, with two HTTP errors
without cost metadata. Both local databases imported 20 pairs; repeat import
added zero. The normal service now serves the expanded pool with zero active
votes. Its legacy pairs, assignments, and votes remain stored.
