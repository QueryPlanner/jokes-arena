#!/usr/bin/env python3
"""Repository-specific forced SSH command: deploy one immutable Jokes Arena image."""
import fcntl
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import URLError

IMAGE = 'ghcr.io/queryplanner/jokes-arena'
ROOT = Path(__file__).resolve().parent.parent
COMMAND = re.compile(r'deploy (sha256:[0-9a-f]{64}) ([0-9a-f]{40})')


def run(command, **kwargs):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs).stdout.strip()
    except subprocess.CalledProcessError:
        raise RuntimeError(f'Deployment command failed: {command[0]}') from None


def backup(database, destination):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite backup integrity check failed')
        target.execute('SELECT outcome FROM assignments LIMIT 1')


def wait_ready(revision, attempts=30):
    for _ in range(attempts):
        try:
            with urlopen('http://127.0.0.1:8183/api/ready', timeout=3) as response:
                data = json.load(response)
            if data.get('status') == 'ready' and data.get('revision') == revision and data.get('pool_pairs', 0) > 0:
                return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Readiness failed: expected revision and populated SQLite pool were not verified')


def start(root, image):
    temporary = root / 'image.env.new'
    temporary.write_text(f'ARENA_IMAGE={image}\n')
    temporary.replace(root / 'image.env')
    run(['docker', 'compose', '--project-name', 'jokes-arena', '--env-file', str(root / 'image.env'),
         '--file', str(root / 'compose.yaml'), 'up', '--detach', '--pull', 'never', '--wait', '--wait-timeout', '90'])


def deploy(root, digest, revision, token=''):
    image = f'{IMAGE}@{digest}'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(['bash', str(root / 'deployment/check-host-port.sh'), '8183', 'jokes-arena'])
        with tempfile.TemporaryDirectory(prefix='jokes-arena-docker-') as docker_config:
            environment = {**os.environ, 'DOCKER_CONFIG': docker_config}
            if token:
                run(['docker', 'login', 'ghcr.io', '--username', 'QueryPlanner', '--password-stdin'], input=token, env=environment)
            run(['docker', 'pull', image], env=environment)
        source = run(['docker', 'image', 'inspect', image, '--format', '{{index .Config.Labels "org.opencontainers.image.source"}}'])
        built_revision = run(['docker', 'image', 'inspect', image, '--format', '{{index .Config.Labels "org.opencontainers.image.revision"}}'])
        if source != 'https://github.com/QueryPlanner/jokes-arena' or built_revision != revision:
            raise RuntimeError('Image provenance does not match this repository and revision')
        data = root / 'data'
        data.mkdir(mode=0o700, exist_ok=True)
        database = data / 'arena.db'
        previous = json.loads((root / 'current.json').read_text()) if (root / 'current.json').exists() else None
        if database.exists():
            backups = root / 'backups'
            backups.mkdir(mode=0o700, exist_ok=True)
            backup(database, backups / f'arena-{time.time_ns()}.db')
        else:
            receipt = root / 'seed/receipt.jsonl'
            if not receipt.is_file():
                raise RuntimeError('First deployment requires the private validated seed receipt')
            run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                 '--security-opt', 'no-new-privileges:true', '--mount', f'type=bind,src={data},dst=/data',
                 '--mount', f'type=bind,src={receipt},dst=/seed/receipt.jsonl,readonly', image,
                 'python', '-m', 'arena.seed', '--database', '/data/arena.db', '--receipt', '/seed/receipt.jsonl'])
        try:
            start(root, image)
            wait_ready(revision)
        except RuntimeError:
            if previous:
                start(root, previous['image'])
                wait_ready(previous['revision'])
                print('Previous healthy image restored; database and backup retained.', file=sys.stderr)
            else:
                run(['docker', 'compose', '--project-name', 'jokes-arena', '--env-file', str(root / 'image.env'),
                     '--file', str(root / 'compose.yaml'), 'stop'])
            raise
        state = root / 'current.json.new'
        state.write_text(json.dumps({'image': image, 'revision': revision}) + '\n')
        state.replace(root / 'current.json')
        print(f'Deployed {revision}; SQLite readiness passed.')


def main():
    os.umask(0o077)
    command = COMMAND.fullmatch(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    if command is None:
        print('Only the repository-specific deploy command is allowed.', file=sys.stderr)
        return 64
    token = sys.stdin.readline(8193).strip()
    if len(token) > 8192 or '\x00' in token:
        print('Invalid registry credential input.', file=sys.stderr)
        return 64
    try:
        deploy(ROOT, *command.groups(), token)
    except (OSError, RuntimeError, sqlite3.Error, ValueError):
        print('Deployment failed. Check readiness, host port, image provenance, and app backup; no success recorded.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
