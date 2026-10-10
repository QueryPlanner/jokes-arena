"""Transactional anonymous battles and binary vote totals."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3


MODEL_NAMES = {
    "openai/gpt-5-mini": "GPT-5 mini",
    "anthropic/claude-haiku-4.5": "Claude Haiku 4.5",
}


class ArenaError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, path, profiles=None):
        self.path = str(path)
        self.profiles = profiles
        self.names = {**MODEL_NAMES, **({identity: profile['name'] for identity, profile in profiles.items()} if profiles else {})}
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pairs (
                    id TEXT PRIMARY KEY, word TEXT NOT NULL,
                    first_model TEXT NOT NULL REFERENCES models(id),
                    second_model TEXT NOT NULL REFERENCES models(id),
                    first_joke TEXT NOT NULL, second_joke TEXT NOT NULL,
                    CHECK (first_model != second_model)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS model_profiles (
                    id TEXT PRIMARY KEY REFERENCES models(id),
                    api_model TEXT NOT NULL, reasoning TEXT NOT NULL,
                    prompt_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    pair TEXT NOT NULL REFERENCES pairs(id),
                    first_on_left INTEGER NOT NULL CHECK (first_on_left IN (0, 1)),
                    outcome TEXT CHECK (outcome IN ('left', 'right')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    voted_at TEXT,
                    UNIQUE (session, pair)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_outstanding
                    ON assignments(session) WHERE outcome IS NULL;
            """)
        if profiles:
            with self.transaction() as db:
                for identity, profile in profiles.items():
                    settings = json.dumps(profile['reasoning'], sort_keys=True)
                    previous = db.execute('SELECT * FROM model_profiles WHERE id=?', (identity,)).fetchone()
                    if previous and (previous['api_model'], previous['reasoning'], previous['prompt_version']) != (
                        profile['api_model'], settings, 'joke-v1'
                    ):
                        raise ValueError('An existing profile cannot change its generation settings')
                    db.execute('INSERT OR IGNORE INTO models VALUES (?, ?)', (identity, profile['name']))
                    db.execute('INSERT OR IGNORE INTO model_profiles VALUES (?, ?, ?, ?)',
                               (identity, profile['api_model'], settings, 'joke-v1'))

    def eligibility(self):
        if self.profiles is None:
            return '1=1', ()
        placeholders = ','.join('?' for _ in self.profiles)
        return (f'p.first_model IN ({placeholders}) AND p.second_model IN ({placeholders})',
                tuple(self.profiles) * 2)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def import_receipt(self, path):
        records = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        grouped = {}
        for record in records:
            if not isinstance(record, dict) or record.get("status") != "validated":
                raise ValueError("Receipt must contain only validated completions")
            model = record.get("requested_model")
            if not isinstance(model, str) or not model or model != record.get("returned_model"):
                raise ValueError("Receipt model attribution is invalid")
            identity = record.get('profile_id', model)
            if 'profile_id' in record:
                profile = (self.profiles or {}).get(identity)
                if not profile or profile['api_model'] != model or profile['reasoning'] != record.get('reasoning') or record.get('prompt_version') != 'joke-v1':
                    raise ValueError('Receipt does not match its configured model profile')
            word, joke = record.get("word"), record.get("joke")
            if not isinstance(word, str) or not re.fullmatch(r"[a-z]+", word):
                raise ValueError("Receipt word is invalid")
            if not isinstance(joke, str) or not joke.strip() or len(joke.split()) > 40:
                raise ValueError("Receipt joke is invalid")
            if not re.search(rf"\b{re.escape(word)}\b", joke, re.IGNORECASE):
                raise ValueError("Receipt joke omits its word")
            grouped.setdefault(word, []).append((identity, joke))
        if not grouped or any(len(items) != 2 or items[0][0] == items[1][0] for items in grouped.values()):
            raise ValueError("Each word needs two jokes from distinct models")
        added = 0
        with self.transaction() as db:
            for word, items in grouped.items():
                items.sort()
                identity = hashlib.sha256(json.dumps([word, items]).encode()).hexdigest()
                for model, _ in items:
                    db.execute("INSERT OR IGNORE INTO models VALUES (?, ?)", (model, self.names.get(model, model)))
                added += db.execute(
                    "INSERT OR IGNORE INTO pairs VALUES (?, ?, ?, ?, ?, ?)",
                    (identity, word, items[0][0], items[1][0], items[0][1], items[1][1]),
                ).rowcount
        return added

    def has_session(self, session):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone() is not None

    def new_session(self, session):
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?)", (session,))

    def battle(self, session):
        with self.transaction() as db:
            current = db.execute(
                "SELECT * FROM assignments WHERE session=? AND outcome IS NULL", (session,)
            ).fetchone()
            if current is None:
                eligible, parameters = self.eligibility()
                pair = db.execute(f"""
                    SELECT p.id FROM pairs p
                    LEFT JOIN assignments a ON a.pair=p.id
                    WHERE {eligible} AND NOT EXISTS (
                        SELECT 1 FROM assignments seen WHERE seen.session=? AND seen.pair=p.id
                    )
                    GROUP BY p.id ORDER BY COUNT(a.id), RANDOM() LIMIT 1
                """, (*parameters, session)).fetchone()
                if pair is None:
                    if db.execute(f'SELECT 1 FROM pairs p WHERE {eligible} LIMIT 1', parameters).fetchone() is None:
                        raise ArenaError(503, 'The new model pool is awaiting generation. Explore the word bucket and model roster in Leaderboard.')
                    raise ArenaError(409, "You have voted on every pair in the current pool.")
                assignment = secrets.token_hex(16)
                db.execute(
                    "INSERT INTO assignments(id, session, pair, first_on_left) VALUES (?, ?, ?, ?)",
                    (assignment, session, pair["id"], secrets.randbelow(2)),
                )
                current = db.execute("SELECT * FROM assignments WHERE id=?", (assignment,)).fetchone()
            pair = db.execute("SELECT * FROM pairs WHERE id=?", (current["pair"],)).fetchone()
            jokes = [pair["first_joke"], pair["second_joke"]]
            if not current["first_on_left"]:
                jokes.reverse()
            return {"id": current["id"], "word": pair["word"], "jokes": jokes}

    def vote(self, session, assignment, choice):
        if choice not in ("left", "right"):
            raise ArenaError(400, "Choose one of the two jokes.")
        with self.transaction() as db:
            row = db.execute(
                """SELECT a.outcome, a.first_on_left, first.name AS first_name,
                          second.name AS second_name
                   FROM assignments a JOIN pairs p ON p.id=a.pair
                   JOIN models first ON first.id=p.first_model
                   JOIN models second ON second.id=p.second_model
                   WHERE a.id=? AND a.session=?""", (assignment, session)
            ).fetchone()
            if row is None:
                raise ArenaError(404, "Comparison not found.")
            if row["outcome"] is not None and row["outcome"] != choice:
                raise ArenaError(409, "This comparison already has a different saved vote.")
            db.execute(
                "UPDATE assignments SET outcome=?, voted_at=CURRENT_TIMESTAMP WHERE id=? AND outcome IS NULL",
                (choice, assignment),
            )
            models = [row['first_name'], row['second_name']]
            if not row['first_on_left']:
                models.reverse()
        return {"saved": True, "models": models}

    def readiness(self):
        eligible, parameters = self.eligibility()
        with self.connection() as db:
            # Verify the schema used by voting as well as the active pool.
            db.execute('SELECT session, pair, first_on_left, outcome, voted_at FROM assignments LIMIT 1')
            db.execute('SELECT api_model, reasoning, prompt_version FROM model_profiles LIMIT 1')
            return db.execute(f'SELECT COUNT(*) FROM pairs p WHERE {eligible}', parameters).fetchone()[0]

    def leaderboard(self):
        eligible, parameters = self.eligibility()
        with self.connection() as db:
            models = db.execute("SELECT * FROM models ORDER BY name").fetchall()
            if self.profiles is not None:
                models = [model for model in models if model['id'] in self.profiles]
            votes = db.execute(f"""
                SELECT p.*, a.outcome, a.first_on_left FROM assignments a
                JOIN pairs p ON p.id=a.pair WHERE a.outcome IS NOT NULL AND {eligible}
            """, parameters).fetchall()
            available = db.execute(f'SELECT p.* FROM pairs p WHERE {eligible}', parameters).fetchall()
            pool_size = len(available)
        totals = {m["id"]: {"name": m["name"], "wins": 0, "losses": 0, "comparisons": 0, "unique_jokes": 0} for m in models}
        unique_jokes = {model: set() for model in totals}
        for pair in available:
            unique_jokes[pair["first_model"]].add(pair["first_joke"])
            unique_jokes[pair["second_model"]].add(pair["second_joke"])
        for model, jokes in unique_jokes.items():
            totals[model]["unique_jokes"] = len(jokes)
        for vote in votes:
            first_wins = (vote["outcome"] == "left") == bool(vote["first_on_left"])
            winner = vote["first_model"] if first_wins else vote["second_model"]
            loser = vote["second_model"] if first_wins else vote["first_model"]
            totals[winner]["wins"] += 1
            totals[loser]["losses"] += 1
            totals[winner]["comparisons"] += 1
            totals[loser]["comparisons"] += 1
        rows = list(totals.values())
        if self.profiles:
            for identity, row in totals.items():
                row['reasoning_label'] = self.profiles[identity]['reasoning_label']
        for row in rows:
            row["win_rate"] = row["wins"] / row["comparisons"] if row["comparisons"] else None
        rows.sort(key=lambda row: (-(row["win_rate"] if row["win_rate"] is not None else -1), -row["comparisons"], row["name"]))
        return {"models": rows, "total_votes": len(votes), "pool_pairs": pool_size}
