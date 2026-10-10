"""Shared REST application, with a development HTTP adapter and Gunicorn WSGI factory."""
import argparse
import hashlib
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from arena.store import ArenaError, Store
from arena.config import load_config, active_profiles

STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/preview/': ('index.html', 'text/html; charset=utf-8'),
    '/preview/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/preview/app.js': ('app.js', 'text/javascript; charset=utf-8'),
}
ROOT = Path(__file__).resolve().parent.parent / 'preview'
MAX_BODY = 4096


class Application:
    def __init__(self, store, origin, config=None, revision='development'):
        self.store, self.origin, self.config, self.revision = store, origin, config, revision

    def session(self, headers, create=False):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('cookie', ''))
        except CookieError:
            cookie = SimpleCookie()
        value = cookie['arena_session'].value if 'arena_session' in cookie else ''
        if re.fullmatch(r'[0-9a-f]{64}', value):
            identity = hashlib.sha256(value.encode()).hexdigest()
            if self.store.has_session(identity):
                return identity, None
        if not create:
            raise ArenaError(404, 'Comparison not found.')
        token = secrets.token_hex(32)
        identity = hashlib.sha256(token.encode()).hexdigest()
        self.store.new_session(identity)
        secure = '; Secure' if self.origin.startswith('https://') else ''
        return identity, f'arena_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=15552000{secure}'

    def body(self, headers, stream):
        if headers.get('origin') != self.origin:
            raise ArenaError(403, 'Request origin is not allowed.')
        if headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise ArenaError(415, 'Use application/json.')
        length = headers.get('content-length', '')
        if not length.isdigit():
            raise ArenaError(400, 'Content-Length is required.')
        if int(length) > MAX_BODY:
            raise ArenaError(413, 'Request body is too large.')
        try:
            body = json.loads(stream.read(int(length)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ArenaError(400, 'Request body must be valid JSON.') from None
        if not isinstance(body, dict):
            raise ArenaError(400, 'Request body must be an object.')
        return body

    def route(self, method, path, headers, stream):
        path = urlsplit(path).path
        if method == 'GET':
            if path in ('/api/health', '/api/ready'):
                count = self.store.readiness()
                if path == '/api/ready':
                    if not count:
                        raise ArenaError(503, 'The active joke pool is empty.')
                    return {'status': 'ready', 'revision': self.revision, 'pool_pairs': count}, None
                return {'status': 'ok'}, None
            if path == '/api/leaderboard':
                results = self.store.leaderboard()
                if self.config:
                    results['word_bucket'] = self.config['words']
                    results['unavailable_models'] = [m['name'] for m in self.config['models'] if not m['available']]
                    results['model_notes'] = [m['note'] for m in self.config['models'] if m.get('note') and m['available']]
                return results, None
            if path in STATIC:
                filename, mime = STATIC[path]
                return (ROOT / filename).read_bytes(), mime
            raise ArenaError(404, 'Not found.')
        if method != 'POST':
            raise ArenaError(405, 'Method not allowed.')
        data = self.body(headers, stream)
        if path == '/api/battles':
            identity, cookie = self.session(headers, create=True)
            return self.store.battle(identity), cookie
        match = re.fullmatch(r'/api/battles/([0-9a-f]{32})/vote', path)
        if match:
            identity, _ = self.session(headers)
            return self.store.vote(identity, match[1], data.get('choice')), None
        raise ArenaError(404, 'Not found.')

    def response(self, method, path, headers, stream):
        status, cookie, mime = 200, None, 'application/json; charset=utf-8'
        try:
            payload, extra = self.route(method, path, headers, stream)
            if isinstance(payload, bytes):
                mime = extra
            else:
                cookie = extra
        except ArenaError as error:
            status, payload = error.status, {'error': str(error)}
        except (OSError, sqlite3.Error):
            status, payload = 503, {'error': 'The arena is temporarily unavailable. Please try again.'}
        data = json.dumps(payload).encode() if mime.startswith('application/json') else payload
        result_headers = [('Content-Type', mime), ('Content-Length', str(len(data))),
                          ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                          ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")]
        if cookie:
            result_headers.append(('Set-Cookie', cookie))
        return status, result_headers, data

    def __call__(self, environ, start_response):
        headers = {key[5:].replace('_', '-').lower(): value for key, value in environ.items() if key.startswith('HTTP_')}
        headers['content-type'] = environ.get('CONTENT_TYPE', '')
        headers['content-length'] = environ.get('CONTENT_LENGTH', '')
        status, response_headers, data = self.response(environ['REQUEST_METHOD'], environ['PATH_INFO'], headers, environ['wsgi.input'])
        start_response(f'{status} {HTTPStatus(status).phrase}', response_headers)
        return [data]


def handler(store, origin, config=None):
    application = Application(store, origin, config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def serve(self):
            status, headers, data = application.response(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile)
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        do_GET = serve
        do_POST = serve
    return Handler


def create_app():
    """Gunicorn factory: initialize a separate store in each worker, without preloading."""
    os.umask(0o077)
    database = Path(os.environ.get('ARENA_DATABASE', 'runtime/arena.db'))
    database.parent.mkdir(parents=True, exist_ok=True)
    config = load_config()
    origin = os.environ.get('ARENA_ORIGIN', 'http://127.0.0.1:4173')
    return Application(Store(database, active_profiles(config)), origin, config, os.environ.get('ARENA_REVISION', 'development'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.environ.get('ARENA_DATABASE', 'runtime/arena.db'))
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--origin', default=os.environ.get('ARENA_ORIGIN', 'http://127.0.0.1:4173'))
    parser.add_argument('--import-receipt', type=Path)
    args = parser.parse_args()
    database = Path(args.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    config = load_config()
    store = Store(database, active_profiles(config))
    if args.import_receipt:
        print(f'Imported {store.import_receipt(args.import_receipt)} new joke pairs.', flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(store, args.origin, config))
    server.daemon_threads = True
    print(f'Jokes Arena running at {args.origin}/preview/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
