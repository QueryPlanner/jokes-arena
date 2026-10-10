"""Deployment admission, SQLite backup, readiness, and rollback contracts."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from deployment import deploy
from arena.config import load_config, active_profiles
from arena.store import Store

DIGEST = 'sha256:' + 'a' * 64
REVISION = 'b' * 40
OLD_REVISION = 'c' * 40
OLD_IMAGE = deploy.IMAGE + '@sha256:' + 'd' * 64


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.database = self.root / 'data/arena.db'
        self.store = Store(self.database)
        self.store.new_session('existing-session')
        self.calls = []

    def command(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if 'inspect' in args:
            return 'https://github.com/QueryPlanner/jokes-arena' if 'source' in args[-1] else REVISION
        return ''

    def test_online_backup_includes_uncheckpointed_wal_and_validates_schema(self):
        with self.store.connection() as writer:
            writer.execute("INSERT INTO sessions VALUES ('wal-session')")
            backup = self.root / 'backup.db'
            deploy.backup(self.database, backup)
            with sqlite3.connect(backup) as snapshot:
                self.assertEqual(snapshot.execute('select count(*) from sessions').fetchone()[0],2)
        with sqlite3.connect(self.database) as source:
            source.execute('PRAGMA writable_schema=ON')
            source.execute("UPDATE sqlite_master SET sql='CREATE TABLE sessions (id TEXT, broken)' WHERE name='sessions'")
        with self.assertRaises((RuntimeError,sqlite3.Error)):
            deploy.backup(self.database,self.root/'invalid.db')

    def test_existing_database_is_backed_up_without_reseeding(self):
        with patch.object(deploy,'run',side_effect=self.command), patch.object(deploy,'wait_ready') as ready:
            deploy.deploy(self.root,DIGEST,REVISION,'registry-token')
        state=json.loads((self.root/'current.json').read_text())
        self.assertEqual(state,{'image':f'{deploy.IMAGE}@{DIGEST}','revision':REVISION})
        self.assertEqual(len(list((self.root/'backups').glob('*.db'))),1)
        self.assertTrue(self.store.has_session('existing-session'))
        self.assertFalse(any('arena.seed' in args for args,_ in self.calls))
        login=[(args,kw) for args,kw in self.calls if 'login' in args][0]
        self.assertEqual(login[1]['input'],'registry-token')
        self.assertNotIn('registry-token',' '.join(login[0]))
        self.assertFalse(Path(login[1]['env']['DOCKER_CONFIG']).exists())
        ready.assert_called_once_with(REVISION)

    def test_backup_rejects_failed_integrity_check(self):
        from unittest.mock import MagicMock
        source, target = MagicMock(), MagicMock()
        target.execute.return_value.fetchone.return_value = ('corrupt index',)
        with patch.object(deploy.sqlite3, 'connect', side_effect=[source, target]):
            source.__enter__.return_value = source
            target.__enter__.return_value = target
            with self.assertRaisesRegex(RuntimeError, 'integrity check failed'):
                deploy.backup(self.database, self.root/'corrupt.db')

    def test_failed_release_restores_image_retains_database_and_returns_failure(self):
        previous={'image':OLD_IMAGE,'revision':OLD_REVISION}
        (self.root/'current.json').write_text(json.dumps(previous))
        with patch.object(deploy,'run',side_effect=self.command), patch.object(deploy,'wait_ready',side_effect=[RuntimeError('wrong revision'),None]) as ready:
            with self.assertRaisesRegex(RuntimeError,'wrong revision'):
                deploy.deploy(self.root,DIGEST,REVISION)
        self.assertEqual(json.loads((self.root/'current.json').read_text()),previous)
        self.assertEqual((self.root/'image.env').read_text(),f'ARENA_IMAGE={OLD_IMAGE}\n')
        self.assertEqual([call.args[0] for call in ready.call_args_list],[REVISION,OLD_REVISION])
        self.assertTrue(self.store.has_session('existing-session'))

    def test_first_deployment_requires_private_seed_and_stops_unhealthy_service(self):
        self.database.unlink()
        with patch.object(deploy,'run',side_effect=self.command):
            with self.assertRaisesRegex(RuntimeError,'private validated seed'):
                deploy.deploy(self.root,DIGEST,REVISION)
        seed=self.root/'seed/receipt.jsonl';seed.parent.mkdir();seed.write_text('private fixture')
        with patch.object(deploy,'run',side_effect=self.command), patch.object(deploy,'wait_ready',side_effect=RuntimeError('unhealthy')):
            with self.assertRaisesRegex(RuntimeError,'unhealthy'):
                deploy.deploy(self.root,DIGEST,REVISION)
        self.assertTrue(any('arena.seed' in args for args,_ in self.calls))
        self.assertEqual(self.calls[-1][0][-1],'stop')
        self.assertFalse((self.root/'current.json').exists())
        with patch.object(deploy,'run',side_effect=self.command), patch.object(deploy,'wait_ready'):
            deploy.deploy(self.root,DIGEST,REVISION)
        self.assertTrue((self.root/'current.json').exists())

    def test_provenance_and_port_conflicts_stop_before_database_changes(self):
        for failure in [RuntimeError('port conflict'),'wrong-source']:
            with self.subTest(failure=failure), patch.object(deploy,'run',side_effect=failure if isinstance(failure,Exception) else lambda *_args,**_kw: failure):
                with self.assertRaises(RuntimeError): deploy.deploy(self.root,DIGEST,REVISION)
            self.assertFalse((self.root/'current.json').exists())
            self.assertFalse((self.root/'backups').exists())

    def test_readiness_rejects_wrong_revision_empty_pool_and_network_errors(self):
        responses=[URLError('offline'),OSError('unavailable'),io.StringIO('invalid json'),
                   io.StringIO(json.dumps({'status':'ready','revision':OLD_REVISION,'pool_pairs':1})),
                   io.StringIO(json.dumps({'status':'ready','revision':REVISION,'pool_pairs':0})),
                   io.StringIO(json.dumps({'status':'ready','revision':REVISION,'pool_pairs':1}))]
        with patch.object(deploy,'urlopen',side_effect=responses),patch.object(deploy.time,'sleep'):
            deploy.wait_ready(REVISION,attempts=6)
        with patch.object(deploy,'urlopen',side_effect=URLError('offline')),patch.object(deploy.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'Readiness failed'): deploy.wait_ready(REVISION,attempts=1)

    def test_command_failures_are_sanitized(self):
        with patch.object(deploy.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'output\n')):
            self.assertEqual(deploy.run(['docker','pull']),'output')
        with patch.object(deploy.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['docker'],stderr='sensitive provider output')):
            with self.assertRaisesRegex(RuntimeError,'Deployment command failed: docker') as error: deploy.run(['docker'])
        self.assertNotIn('sensitive',str(error.exception))

    def test_forced_command_admission_and_errors(self):
        cases=[('', '',64),('deploy latest '+REVISION,'',64),('deploy '+DIGEST+' '+REVISION+'; bash','',64),
               ('deploy '+DIGEST+' '+REVISION,'x'*8193,64),('deploy '+DIGEST+' '+REVISION,'\x00',64)]
        for command,token,code in cases:
            with self.subTest(command=command),patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':command}),patch('sys.stdin',io.StringIO(token)),patch.object(deploy,'deploy') as execute,contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(deploy.main(),code);execute.assert_not_called()
        for failure,expected in [(None,0),(OSError('secret error'),1),(sqlite3.Error('secret error'),1),(ValueError('bad state'),1)]:
            with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':f'deploy {DIGEST} {REVISION}'}),patch('sys.stdin',io.StringIO('short-lived-token\n')),patch.object(deploy,'deploy',side_effect=failure) as execute,contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(deploy.main(),expected)
                self.assertNotIn('secret error',output.getvalue())
                execute.assert_called_once_with(deploy.ROOT,DIGEST,REVISION,'short-lived-token')
        with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':''}),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error: runpy.run_module('deployment.deploy',run_name='__main__')
        self.assertEqual(error.exception.code,64)

    def test_seed_command_is_idempotent_and_preserves_votes(self):
        receipt=self.root/'receipt.jsonl'
        profiles=list(active_profiles(load_config()).values())[:2]
        receipt.write_text('\n'.join(json.dumps(dict(status='validated',word='coffee',requested_model=p['api_model'],returned_model=p['api_model'],profile_id=p['profile_id'],reasoning=p['reasoning'],prompt_version='joke-v1',joke=f"coffee joke {p['name']}")) for p in profiles))
        for count in [1,0]:
            with patch('sys.argv',['seed','--database',str(self.database),'--receipt',str(receipt)]),contextlib.redirect_stdout(io.StringIO()) as output:
                runpy.run_module('arena.seed',run_name='__main__')
            self.assertIn(f'Imported {count} complete pairs',output.getvalue())
            active=Store(self.database,active_profiles(load_config()))
            if count:
                battle=active.battle('existing-session');active.vote('existing-session',battle['id'],'left')
            self.assertEqual(active.leaderboard()['total_votes'],1)

    def test_initial_seed_failure_is_retryable_and_publication_is_exclusive(self):
        from arena import seed
        receipt = self.root/'seed.jsonl'
        destination = self.root/'fresh/arena.db'
        profiles = list(active_profiles(load_config()).values())[:2]
        valid = '\n'.join(json.dumps(dict(status='validated',word='coffee',requested_model=p['api_model'],returned_model=p['api_model'],profile_id=p['profile_id'],reasoning=p['reasoning'],prompt_version='joke-v1',joke=f"coffee joke {p['name']}")) for p in profiles)
        arguments = ['seed', '--database', str(destination), '--receipt', str(receipt)]
        for content in ['invalid', '']:
            receipt.write_text(content)
            with patch('sys.argv', arguments), self.assertRaises(ValueError):
                seed.main()
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])
        receipt.write_text(valid)
        legacy = '\n'.join(json.dumps(dict(status='validated',word='coffee',requested_model=model,returned_model=model,joke='coffee joke')) for model in ['legacy/a','legacy/b'])
        receipt.write_text(legacy)
        with patch('sys.argv', arguments), self.assertRaisesRegex(ValueError, 'active pair'):
            seed.main()
        self.assertFalse(destination.exists())
        receipt.write_text(valid)
        with patch('sys.argv', arguments), patch.object(seed.os, 'link', side_effect=FileExistsError), self.assertRaises(FileExistsError):
            seed.main()
        self.assertFalse(destination.exists())
        with patch('sys.argv', arguments), contextlib.redirect_stdout(io.StringIO()):
            seed.main()
        self.assertEqual(Store(destination,active_profiles(load_config())).readiness(), 1)
        self.assertEqual(list(destination.parent.iterdir()), [destination])
