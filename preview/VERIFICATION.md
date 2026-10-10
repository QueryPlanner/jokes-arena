# Verification on 2026-10-10

## Current local implementation

The service at http://127.0.0.1:4173/preview/ uses SQLite in `runtime/arena.db`.
It contains three pairs imported from the existing validated OpenRouter receipt.
Its leaderboard was verified at zero votes after implementation. All browser test
votes went to a separate database outside the repository. No new paid API calls.

Automated commands run successfully in this session:

```sh
python3 -m coverage run --source=arena,scripts -m unittest discover -s tests -v
python3 -m coverage report --fail-under=100 -m
node --test tests/frontend.test.cjs
```

Results: 16 Python tests passed; all 345 Python statements covered, 100% line
coverage across `arena/server.py`, `arena/store.py`, and the generation script.
Six frontend event tests passed. The frontend tests use a DOM test double;
Python line coverage does not measure browser behavior or CSS.

Contracts checked include atomic receipt validation/import, anonymous assignments,
randomized side attribution, session ownership, concurrent assignment/voting,
idempotent retries, conflicting votes, pool exhaustion, database constraints,
restart persistence, leaderboard totals, exact Origin checks, JSON input limits,
static-file allowlisting, and explicit storage-failure responses.

Frontend tests check automatic advance, duplicate click suppression, failed vote
retry, blocking the opposite choice after an unconfirmed vote, recovery from a
stale-tab HTTP 409, a failed next-pair fetch after a saved vote, initial-load
recovery, completion state, ranks, no-vote display, and result refresh failures.

## Independent browser checks

Used an isolated service on port 4174 with a separate temporary SQLite database.

- Leaderboard initially displayed both real model names, no ranks, 'No votes',
  zero votes, and three pool pairs.
- Clicking a joke saved a vote and advanced to a different shared word.
- Keyboard Enter on the second card saved a vote and advanced.
- At a 390px viewport the second card began below the first card's bottom edge.
- Mobile leaderboard overflow stayed within its scrollable table container.
- Three selections completed the pool, disabled further voting, and offered
  See leaderboard. The leaderboard showed three votes, three wins total,
  three losses total, and three comparisons for each model.
- Restarted the actual service process and re-imported the receipt. It imported
  zero new pairs. The browser still showed three saved votes and the same results.
- Independent review found a stale-tab conflicting-vote retry loop. Fixed it and
  added a regression test. Ambiguous failures now retain the attempted choice.
- Reloaded the user's existing port-4173 preview after replacing its static
  server. Verified the current leaderboard has zero votes and the same real pool.
- At a 1280px viewport the two battle cards were side by side and the page did
  not overflow. Reset temporary viewport overrides afterward.

The final exhausted-pool UI hides the cards instead of leaving loading text after
reload. Automated tests cover this final behavior.

Artifacts:

- `leaderboard-desktop.jpg`: current user's leaderboard, zero saved votes.
- `leaderboard.jpg`: current leaderboard at the normal narrow panel size.
- `leaderboard-mobile-battle.jpg`: stacked cards at a 505px panel width.

## Limits

The frontend is plain HTML/JavaScript, not Vue. The server is a localhost
standard-library development service, not a production serving stack. There was
no production build, Git delivery, or VM deployment in this change. Browser
sessions do not identify unique humans. Three pairs cannot establish general
model humor ability. The previous prototype screenshots describe earlier stages.

## Expanded pool verification, 2026-10-10

The 100-word configuration contains 26 chart entries: 24 catalog profiles and
two unavailable entries. A real 48-request batch published 20 complete pairs,
40 playable jokes, and 23 participating profiles. Muse Spark has zero jokes
after two HTTP 403 failures. Qwen omitted its required word once, and Step
exhausted its output allowance once. No paid requests were retried.

OpenRouter reported $0.041091969 in usage charges; the two HTTP errors have no
cost metadata. This is not independent verification of total account billing.

Both the separate port-4174 validation database and the normal database imported
20 pairs. A repeated validation import added zero. Normal service health passed;
its API returned 24 profiles, 20 active pairs, 100 words, and zero active votes.
Legacy history remains stored separately from active leaderboard totals.

The real browser on port 4174 displayed generated jokes about piano. Selecting
one saved a vote and advanced to job. Its leaderboard displayed one saved vote,
one win, one loss, and the 24 configured profiles. Muse Spark remained unranked
with zero jokes. At 390px, the cards had the same horizontal position; the second
began below the first. Page width did not exceed the viewport. Validation votes
were confined to the separate database; the normal active leaderboard stayed at
zero votes.

Final automated validation passed: 32 Python tests, 608 statements with 100%
line coverage, and seven frontend event tests. At 1280px, battle cards shared
the same vertical position and appeared side by side. The viewport override was
reset. The main browser displayed a new generated pair about fence. Its word
bucket disclosure displayed all 100 words and the two unavailable entries.
Screenshots: `expanded-battle.png` and `expanded-leaderboard.png`.

## Post-vote model reveal

Selecting a card now saves the vote, reveals both model names on their cards,
labels the selected joke Your pick, and waits for Next pair. No Anonymous model
label appears before voting. Keyboard focus moves to Next pair without scrolling
past the revealed cards; explicit advance focuses the first new card.

The browser on the isolated port-4174 database showed GLM 5.3 Flash and Claude
Haiku 5.5 for a pair about flower after a successful selection. The pair remained
visible until Next pair was clicked. It then advanced to music and hid both model
labels. At 390px, both names appeared on stacked cards without horizontal page
overflow. Screenshot: `model-reveal-mobile.png`.

All 32 Python tests and seven frontend event tests passed. Python coverage was
100% across 611 statements. Tests verify attribution in both randomized orders,
same-choice retries, ownership, hidden names on failed votes, explicit advance,
and retained attribution when loading the next pair fails.
