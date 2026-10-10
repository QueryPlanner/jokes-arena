"""Production WSGI transport and readiness contracts."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena.server import Application, create_app
from arena.store import Store


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'arena.db'
        self.store = Store(self.path)
        self.app = Application(self.store, 'https://jokesarena.lordpatil.com', revision='revision-one')

    def request(self, path, method='GET', body=None, cookie=''):
        data = json.dumps(body).encode() if body is not None else b''
        environ = {'REQUEST_METHOD':method, 'PATH_INFO':path, 'wsgi.input':io.BytesIO(data),
                   'CONTENT_TYPE':'application/json', 'CONTENT_LENGTH':str(len(data)),
                   'HTTP_ORIGIN':'https://jokesarena.lordpatil.com', 'HTTP_COOKIE':cookie}
        response = []
        content = b''.join(self.app(environ, lambda status, headers: response.extend([status, dict(headers)])))
        return response[0], response[1], content

    def seed(self):
        receipt = Path(self.temp.name) / 'receipt.jsonl'
        receipt.write_text('\n'.join(json.dumps(dict(status='validated',requested_model=m,returned_model=m,word='coffee',joke=f'coffee {m}'))
                                    for m in ['openai/gpt-5-mini','anthropic/claude-haiku-4.5']))
        self.store.import_receipt(receipt)

    def test_readiness_and_database_failure(self):
        self.assertEqual(self.request('/api/health')[0], '200 OK')
        self.assertEqual(self.request('/api/ready')[0], '503 Service Unavailable')
        self.seed()
        self.assertEqual(json.loads(self.request('/api/ready')[2]), {'status':'ready','revision':'revision-one','pool_pairs':1})
        with self.store.connection() as db:
            db.execute('DROP TABLE model_profiles')
        self.assertEqual(self.request('/api/ready')[0], '503 Service Unavailable')
        self.assertEqual(self.request('/api/health')[0], '503 Service Unavailable')

    def test_wsgi_vote_blinding_ownership_and_idempotence(self):
        self.seed()
        status, headers, content = self.request('/api/battles', 'POST', {})
        self.assertEqual(status, '200 OK')
        self.assertIn('; Secure', headers['Set-Cookie'])
        battle = json.loads(content)
        self.assertEqual(set(battle), {'id','word','jokes'})
        cookie = headers['Set-Cookie'].split(';')[0]
        path = f"/api/battles/{battle['id']}/vote"
        self.assertEqual(self.request(path,'POST',{'choice':'left'})[0], '404 Not Found')
        saved = self.request(path,'POST',{'choice':'left'},cookie)
        self.assertEqual(saved[0], '200 OK')
        self.assertEqual(self.request(path,'POST',{'choice':'left'},cookie)[2], saved[2])
        self.assertEqual(self.request(path,'POST',{'choice':'right'},cookie)[0], '409 Conflict')
        self.assertEqual(self.request('/api/leaderboard','PUT')[0], '405 Method Not Allowed')
        self.assertEqual(self.request('/preview/')[1]['Content-Type'],'text/html; charset=utf-8')
        self.assertEqual(self.request('/runtime/arena.db')[0], '404 Not Found')

    def test_factory_uses_runtime_configuration(self):
        previous = os.umask(0o077)
        try:
            with patch.dict(os.environ, {'ARENA_DATABASE':str(self.path), 'ARENA_ORIGIN':'https://jokesarena.lordpatil.com','ARENA_REVISION':'immutable-revision'}):
                app = create_app()
                self.assertEqual(app.origin, 'https://jokesarena.lordpatil.com')
                self.assertEqual(app.revision, 'immutable-revision')
                self.assertEqual(app.store.leaderboard()['total_votes'],0)
        finally:
            os.umask(previous)
