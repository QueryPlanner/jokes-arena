"""Storage and HTTP contracts using disposable SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from arena.server import handler
from arena.config import load_config
from arena.store import ArenaError, Store


class ArenaFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'arena.db'
        self.receipt = Path(self.temp.name) / 'receipt.jsonl'
        self.store = Store(self.path)
        self.records = [dict(status='validated', requested_model=model, returned_model=model,
                             word=word, joke=f'{word} joke from {model}.')
                        for word in ('coffee', 'calendar', 'umbrella')
                        for model in ('openai/gpt-5-mini', 'anthropic/claude-haiku-4.5')]
        self.write_receipt(self.records)
        self.assertEqual(self.store.import_receipt(self.receipt), 3)

    def write_receipt(self, records):
        self.receipt.write_text('\n'.join(json.dumps(record) for record in records))


class ArenaTests(ArenaFixture, unittest.TestCase):
    def test_votes_attribution_retries_and_restart(self):
        self.store.new_session('one')
        self.assertTrue(self.store.has_session('one'))
        self.assertFalse(self.store.has_session('other'))
        self.assertTrue(all(row['win_rate'] is None for row in self.store.leaderboard()['models']))
        with patch('arena.store.secrets.randbelow', return_value=1):
            pair = self.store.battle('one')
        self.assertEqual(self.store.battle('one'), pair)
        self.assertEqual(set(pair), {'id', 'word', 'jokes'})
        self.assertIn('claude', pair['jokes'][0])
        reveal = {'saved': True, 'models': ['Claude Haiku 4.5', 'GPT-5 mini']}
        self.assertEqual(self.store.vote('one', pair['id'], 'left'), reveal)
        self.assertEqual(self.store.vote('one', pair['id'], 'left'), reveal)
        for session, choice, status in [('one', 'right', 409), ('other', 'left', 404), ('one', 'tie', 400)]:
            with self.assertRaises(ArenaError) as error:
                self.store.vote(session, pair['id'], choice)
            self.assertEqual(error.exception.status, status)
        with patch('arena.store.secrets.randbelow', return_value=0):
            second = self.store.battle('one')
        self.assertNotEqual(second['word'], pair['word'])
        self.assertIn('gpt', second['jokes'][0])
        self.assertEqual(self.store.vote('one', second['id'], 'right')['models'],
                         ['GPT-5 mini', 'Claude Haiku 4.5'])
        last = self.store.battle('one')
        self.store.vote('one', last['id'], 'left')
        with self.assertRaises(ArenaError) as error:
            self.store.battle('one')
        self.assertEqual(error.exception.status, 409)
        before = self.store.leaderboard()
        reopened = Store(self.path)
        self.assertEqual(reopened.import_receipt(self.receipt), 0)
        self.assertEqual(reopened.leaderboard(), before)
        self.assertEqual(before['total_votes'], 3)
        self.assertEqual(sum(row['wins'] for row in before['models']), 3)
        self.assertEqual(sum(row['losses'] for row in before['models']), 3)
        for row in before['models']:
            self.assertEqual(row['comparisons'], 3)
            self.assertEqual(row['unique_jokes'], 3)
            self.assertEqual(row['win_rate'], row['wins'] / 3)
        self.assertGreaterEqual(before['models'][0]['win_rate'], before['models'][1]['win_rate'])

    def test_concurrent_assignments_and_votes(self):
        self.store.new_session('one')
        with ThreadPoolExecutor(max_workers=4) as pool:
            pairs = list(pool.map(lambda _: self.store.battle('one'), range(8)))
            self.assertEqual(len({pair['id'] for pair in pairs}), 1)
            responses = list(pool.map(lambda _: self.store.vote('one', pairs[0]['id'], 'right'), range(8)))
        self.assertTrue(all(response['saved'] for response in responses))
        self.assertEqual(self.store.leaderboard()['total_votes'], 1)

    def test_database_constraints_and_rollback(self):
        self.store.new_session('one')
        pair = self.store.battle('one')
        with self.store.connection() as db:
            assignment = db.execute('SELECT * FROM assignments WHERE id=?', (pair['id'],)).fetchone()
        for sql, params in [
            ('UPDATE assignments SET outcome=? WHERE id=?', ('tie', pair['id'])),
            ('UPDATE assignments SET first_on_left=2 WHERE id=?', (pair['id'],)),
            ('UPDATE pairs SET second_model=first_model', ()),
            ('INSERT INTO assignments(id,session,pair,first_on_left) VALUES (?,?,?,?)', ('new', 'one', assignment['pair'], 0)),
            ('INSERT INTO assignments(id,session,pair,first_on_left) VALUES (?,?,?,?)', ('new', 'absent', assignment['pair'], 0)),
        ]:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError), self.store.transaction() as db:
                db.execute(sql, params)
        self.assertEqual(self.store.battle('one'), pair)
        self.assertEqual(self.store.leaderboard()['total_votes'], 0)

    def test_import_validates_entire_batch_before_changes(self):
        invalid = [[], [None], [dict(self.records[0], status='failed')],
                   [dict(self.records[0], requested_model=None)],
                   [dict(self.records[0], returned_model='wrong')],
                   [dict(self.records[0], word=123)], [dict(self.records[0], word='two words')],
                   [dict(self.records[0], joke=None)], [dict(self.records[0], joke='')],
                   [dict(self.records[0], joke='coffee ' * 41)],
                   [dict(self.records[0], joke='No required word.')],
                   self.records[:1], [self.records[0], self.records[0]]]
        for records in invalid:
            with self.subTest(records=records):
                self.write_receipt(records)
                with self.assertRaises(ValueError):
                    self.store.import_receipt(self.receipt)
                self.assertEqual(self.store.leaderboard()['pool_pairs'], 3)
        self.receipt.write_text('not json')
        with self.assertRaises(ValueError):
            self.store.import_receipt(self.receipt)


class HTTPTests(ArenaFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.origin = 'http://127.0.0.1:4174'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store, self.origin, load_config()))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method='GET', body=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        data = json.dumps(body) if body is not None else None
        default = {'Origin': self.origin, 'Content-Type': 'application/json'}
        if headers is not None:
            default.update(headers)
        connection.request(method, path, body=data, headers=default)
        response = connection.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        connection.close()
        return result

    def test_http_blind_battle_cookie_ownership_and_leaderboard(self):
        status, headers, body = self.request('/api/battles', 'POST', {})
        self.assertEqual(status, 200)
        battle = json.loads(body)
        self.assertEqual(set(battle), {'id', 'word', 'jokes'})
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        path = f"/api/battles/{battle['id']}/vote"
        self.assertEqual(self.request(path, 'POST', {'choice': 'left'})[0], 404)
        self.assertEqual(self.request('/api/battles', 'POST', {}, {'Cookie': cookie})[2], body)
        status, _, payload = self.request(path, 'POST', {'choice': 'left'}, {'Cookie': cookie})
        self.assertEqual(status, 200)
        revealed = json.loads(payload)
        expected = ['GPT-5 mini' if 'gpt' in joke else 'Claude Haiku 4.5' for joke in battle['jokes']]
        self.assertEqual(revealed, {'saved': True, 'models': expected})
        self.assertEqual(self.request(path, 'POST', {'choice': 'left'}, {'Cookie': cookie})[2], payload)
        self.assertEqual(self.request(path, 'POST', {'choice': 'right'}, {'Cookie': cookie})[0], 409)
        results = json.loads(self.request('/api/leaderboard')[2])
        self.assertEqual(results['total_votes'], 1)
        self.assertEqual(len(results['word_bucket']), 100)
        self.assertIn('Gemini 4 Argon', results['unavailable_models'])
        self.assertEqual([row['win_rate'] for row in results['models']], [1.0, 0.0])
        self.assertEqual(json.loads(self.request('/api/health')[2]), {'status': 'ok'})
        for path in ('/', '/preview/', '/preview/app.js', '/preview/styles.css'):
            status, headers, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertGreater(len(content), 20)
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        for path in ('/runtime/arena.db', '/preview/generated-pairs.js', '/preview/../arena/store.py', '/preview/%2e%2e/arena/store.py', '/.env'):
            self.assertEqual(self.request(path)[0], 404)

    def test_http_input_origin_and_storage_failures(self):
        for path in ('/api/battles', '/api/battles/' + 'a' * 32 + '/vote'):
            for origin in ('', 'https://evil.example'):
                self.assertEqual(self.request(path, 'POST', {}, {'Origin': origin})[0], 403)
        for body, headers, expected in [({}, {'Content-Type': 'text/plain'}, 415),
                                        ({}, {'Content-Length': 'bad'}, 400),
                                        ({}, {'Content-Length': '5000'}, 413),
                                        ([], {}, 400), ({}, {'Content-Length': '1'}, 400)]:
            self.assertEqual(self.request('/api/battles', 'POST', body, headers)[0], expected)
        self.assertEqual(self.request('/unknown', 'POST', {})[0], 404)
        for cookie in ('arena_session=bad', 'arena_session=' + 'f' * 64):
            self.assertEqual(self.request('/api/battles', 'POST', {}, {'Cookie': cookie})[0], 200)
        with patch('arena.server.SimpleCookie.load', side_effect=__import__('http.cookies', fromlist=['CookieError']).CookieError):
            self.assertEqual(self.request('/api/battles', 'POST', {})[0], 200)
        with patch.object(self.store, 'leaderboard', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertEqual(self.request('/api/leaderboard')[0], 503)
        with patch.object(self.store, 'battle', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertEqual(self.request('/api/battles', 'POST', {})[0], 503)


class CommandTests(ArenaFixture, unittest.TestCase):
    def test_command_imports_existing_pool_and_closes_on_interrupt(self):
        import contextlib
        import io
        import os
        import runpy
        with patch('sys.argv', ['arena', '--database', str(self.path), '--port', '0',
                                '--origin', 'https://example.test', '--import-receipt', str(self.receipt)]), \
                patch('http.server.ThreadingHTTPServer.serve_forever', side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            previous_umask = os.umask(0o077)
            try:
                runpy.run_path(str(Path('arena/server.py').resolve()), run_name='__main__')
            finally:
                os.umask(previous_umask)
        self.assertIn('Imported 0 new joke pairs.', output.getvalue())
        self.assertIn('https://example.test/preview/', output.getvalue())
        self.assertEqual(Store(self.path).leaderboard()['pool_pairs'], 3)
