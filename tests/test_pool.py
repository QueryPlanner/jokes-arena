"""Model profiles, balanced generation, spending limits, and active pool storage."""
from collections import Counter
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from arena.config import load_config, active_profiles
from arena import populate
from arena.store import Store, ArenaError
from scripts.generate_preview import request_completion


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = load_config()
        self.config['models'] = [p for p in self.config['models'] if p['available']][:3]
        self.catalog = {'data': [dict(id=p['api_model'], reasoning={'mandatory': True, 'supported_efforts':['high','low']},
                                     pricing={'prompt':'0.0000001','completion':'0.0000002'})
                                 for p in self.config['models']]}
        self.plan = populate.build_plan(self.config, self.catalog, 2)
        self.key_guard = patch.object(populate,'verify_key_budget')
        self.key_guard.start()
        self.addCleanup(self.key_guard.stop)

    def response(self, key, model, word, options):
        self.assertEqual(key, 'test-secret')
        self.assertEqual(options['max_tokens'], 2048)
        self.assertFalse(options['provider']['allow_fallbacks'])
        self.assertTrue(options['provider']['require_parameters'])
        return {'model':model, 'choices':[{'message':{'content':f'An original joke about {word}.'},'finish_reason':'stop'}],
                'usage':{'cost':0.00001}}

    def test_default_configuration_and_balanced_random_cycle(self):
        full = load_config()
        self.assertEqual(len(full['words']), 100)
        self.assertEqual(len(set(full['words'])), 100)
        self.assertIn('umbrella', full['words'])
        self.assertEqual(len(active_profiles(full)), 24)
        self.assertEqual({p['name'] for p in full['models'] if not p['available']}, {'Gemini 4 Argon','K2 Horizon 375B A23B'})
        counts = Counter(identity for pair in self.plan['pairs'] for identity in pair['profiles'])
        self.assertEqual(set(counts.values()), {2})
        self.assertEqual(len({pair['word'] for pair in self.plan['pairs']}), 3)
        for pair in self.plan['pairs']:
            self.assertNotEqual(*pair['profiles'])
            self.assertIn(pair['word'], full['words'])

    def test_invalid_configuration_rejected(self):
        model_path = self.directory / 'models.json'
        word_path = self.directory / 'words.json'
        variants = [([],self.config), (['two words'],self.config), (['coffee','coffee'],self.config),
                    (['coffee'],dict(self.config,prompt_version='bad')),
                    (['coffee'],dict(self.config,models=[]))]
        for field, value in [('name',''),('api_model','wrong'),('reasoning',{}),('profile_id','wrong')]:
            config = deepcopy(self.config);config['models'][0][field]=value
            variants.append((['coffee'],config))
        config=deepcopy(self.config);config['models'].append(config['models'][0]);variants.append((['coffee'],config))
        config=deepcopy(self.config);config['models']=config['models'][:2];variants.append((['coffee'],config))
        config=deepcopy(self.config);config['models'][0]['available']=False;variants.append((['coffee'],config))
        for words, config in variants:
            model_path.write_text(json.dumps(config));word_path.write_text(json.dumps(words))
            with self.subTest(words=words,config=config), self.assertRaises(ValueError):
                load_config(model_path,word_path)

    def test_plan_guards_budget_catalog_settings_and_prompt_size_before_calls(self):
        for budget in (0,-1,3,'NaN','Infinity','0.00000001'):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                populate.build_plan(self.config,self.catalog,budget)
        variants=[]
        catalog=deepcopy(self.catalog);catalog['data']=[];variants.append(catalog)
        catalog=deepcopy(self.catalog);catalog['data'][0]['reasoning']['supported_efforts']=['high'];variants.append(catalog)
        catalog=deepcopy(self.catalog);catalog['data'][0]['pricing']=None;variants.append(catalog)
        catalog=deepcopy(self.catalog);catalog['data'][0]['pricing']['prompt']='bad';variants.append(catalog)
        catalog=deepcopy(self.catalog);catalog['data'][0]['pricing']['prompt']='-1';variants.append(catalog)
        for catalog in variants:
            with self.assertRaises(ValueError):populate.build_plan(self.config,catalog,2)
        config=deepcopy(self.config);config['models'][0]['reasoning']={'enabled':False}
        with self.assertRaises(ValueError):populate.build_plan(config,self.catalog,2)
        config=deepcopy(self.config);config['words']=['a'*3000,'b'*3000,'c'*3000]
        with self.assertRaises(ValueError):populate.build_plan(config,self.catalog,2)
        config=deepcopy(self.config);config['models']=config['models'][:2]
        with self.assertRaises(ValueError):populate.build_plan(config,self.catalog,2)

    def test_complete_batch_imports_profiles_and_persists_configuration(self):
        target=self.directory/'batch'
        with patch.object(populate,'request_completion',side_effect=self.response) as transport, contextlib.redirect_stdout(io.StringIO()):
            summary=populate.generate_batch('test-secret',self.plan,target)
        self.assertEqual(transport.call_count,6)
        self.assertEqual(summary['pairs'],3)
        self.assertEqual(set(summary['published_appearances'].values()),{2})
        profiles=active_profiles(self.config)
        store=Store(self.directory/'pool.db',profiles)
        self.assertEqual(store.import_receipt(target/'receipt.jsonl'),3)
        self.assertEqual(Store(self.directory/'pool.db',profiles).import_receipt(target/'receipt.jsonl'),0)
        rows=store.leaderboard()['models']
        self.assertEqual(len(rows),3)
        self.assertEqual({row['reasoning_label'] for row in rows},{'low'})
        self.assertEqual({row['unique_jokes'] for row in rows},{2})
        records=[json.loads(line) for line in (target/'receipt.jsonl').read_text().splitlines()]
        records[0]['reasoning']={'effort':'high'}
        (target/'bad.jsonl').write_text('\n'.join(map(json.dumps,records)))
        with self.assertRaises(ValueError):store.import_receipt(target/'bad.jsonl')
        changed=deepcopy(profiles);next(iter(changed.values()))['reasoning']={'effort':'high'}
        with self.assertRaises(ValueError):Store(self.directory/'pool.db',changed)
        self.assertNotIn('test-secret',''.join(p.read_text() for p in target.iterdir()))
        with self.assertRaises(FileExistsError):populate.generate_batch('test-secret',self.plan,target)

    def test_partial_failures_publish_only_complete_pairs(self):
        calls=0
        def transport(*args):
            nonlocal calls
            calls+=1
            if calls==1:raise ValueError('OpenRouter HTTP 429')
            result=self.response(*args)
            if calls==3:result['choices'][0]['message']['content']='Missing word'
            if calls==5:result['usage']=None
            return result
        target=self.directory/'partial'
        with patch.object(populate,'request_completion',side_effect=transport), contextlib.redirect_stdout(io.StringIO()):
            summary=populate.generate_batch('test-secret',self.plan,target)
        self.assertEqual(summary['pairs'],1)
        self.assertEqual(summary['missing_cost_calls'],2)
        self.assertEqual(len((target/'attempts.jsonl').read_text().splitlines()),6)
        self.assertEqual(len((target/'receipt.jsonl').read_text().splitlines()),2)
        self.assertEqual(sum(summary['published_appearances'].values()),2)
        self.assertLess(len(summary['published_appearances']),3)

    def test_runtime_reservation_and_cost_anomaly_stop_before_next_call(self):
        plan=deepcopy(self.plan);plan['budget_usd']='0.0000001'
        with patch.object(populate,'request_completion') as transport, self.assertRaises(ValueError):
            populate.generate_batch('test-secret',plan,self.directory/'too-small')
        transport.assert_not_called()
        stopped=json.loads((self.directory/'too-small'/'summary.json').read_text())
        self.assertEqual(stopped['status'],'stopped')
        self.assertEqual(stopped['calls'],0)
        self.assertIn('Remaining budget',stopped['stop_reason'])
        def expensive(*args):
            result=self.response(*args);result['usage']['cost']=2
            return result
        with patch.object(populate,'request_completion',side_effect=expensive) as transport, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            populate.generate_batch('test-secret',self.plan,self.directory/'expensive')
        self.assertEqual(transport.call_count,1)
        stopped=json.loads((self.directory/'expensive'/'summary.json').read_text())
        self.assertEqual(stopped['calls'],1)
        self.assertEqual(stopped['status'],'stopped')
        with self.assertRaises(ValueError):populate.generate_batch('',self.plan,self.directory/'no-key')

    def test_last_call_billing_breach_is_reported_and_keeps_the_summary(self):
        calls=0
        def transport(*args):
            nonlocal calls
            calls+=1
            result=self.response(*args)
            if calls==6:result['usage']['cost']=3
            return result
        target=self.directory/'last-call'
        with patch.object(populate,'request_completion',side_effect=transport), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            populate.generate_batch('test-secret',self.plan,target)
        summary=json.loads((target/'summary.json').read_text())
        self.assertEqual(summary['status'],'over-budget')
        self.assertEqual(summary['calls'],6)
        self.assertEqual(summary['pairs'],2)
        self.assertEqual(len((target/'attempts.jsonl').read_text().splitlines()),6)
        self.assertIn('above the budget',summary['stop_reason'])

    def test_invalid_usage_cannot_be_published(self):
        for value in ([],{'cost':'bad'},{'cost':-1}):
            def invalid(*args):
                result=self.response(*args);result['usage']=value
                return result
            target=self.directory/('invalid-'+str(len(list(self.directory.iterdir()))))
            with patch.object(populate,'request_completion',side_effect=invalid), contextlib.redirect_stdout(io.StringIO()):
                summary=populate.generate_batch('test-secret',self.plan,target)
            self.assertEqual(summary['pairs'],0)
            self.assertEqual((target/'receipt.jsonl').read_text(),'')

    def test_public_catalog_transport_errors_are_explicit(self):
        with patch.object(populate,'urlopen',return_value=io.StringIO(json.dumps(self.catalog))):
            self.assertEqual(populate.read_catalog(),self.catalog)
        for response in ('bad','[]'):
            with patch.object(populate,'urlopen',return_value=io.StringIO(response)), self.assertRaises(ValueError):populate.read_catalog()
        with patch.object(populate,'urlopen',side_effect=URLError('unavailable')), self.assertRaises(ValueError):populate.read_catalog()

    def test_request_carries_reasoning_token_and_provider_price_limits(self):
        model=next(iter(self.plan['profiles'].values()))
        options=self.plan['requests'][model['profile_id']]['options']
        response=self.response('test-secret',model['api_model'],'coffee',options)
        with patch('scripts.generate_preview.urlopen',return_value=io.StringIO(json.dumps(response))) as transport:
            request_completion('test-secret',model['api_model'],'coffee',options)
        request=transport.call_args.args[0]
        body=json.loads(request.data)
        self.assertEqual(body['reasoning'],{'effort':'low','exclude':True})
        self.assertEqual(body['max_tokens'],2048)
        self.assertEqual(body['provider']['max_price'],{'prompt':0.1,'completion':0.2,'request':0.0})
        self.assertEqual(body['model'],model['api_model'])

    def test_command_dry_run_execution_failure_and_entrypoint(self):
        for execute in (False,True):
            args=['populate','--directory',str(self.directory/('execute' if execute else 'plan'))]
            if execute:args.append('--execute')
            with patch('sys.argv',args), patch.object(populate,'load_config',return_value=self.config), patch.object(populate,'read_catalog',return_value=self.catalog), patch.object(populate,'request_completion',side_effect=self.response) as transport, patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-secret'}), contextlib.redirect_stdout(io.StringIO()) as out:
                previous=os.umask(0o077)
                try:self.assertEqual(populate.main(),0)
                finally:os.umask(previous)
            self.assertEqual(transport.call_count,6 if execute else 0)
            self.assertIn('6 requests',out.getvalue())
        with patch('sys.argv',['populate','--directory',str(self.directory/'bad-cli'),'--budget','0']), patch.object(populate,'read_catalog',return_value=self.catalog), contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(populate.main(),1)
        self.assertIn('Batch stopped',out.getvalue())
        with patch('sys.argv',['populate','--directory',str(self.directory/'module')]), patch('urllib.request.urlopen',side_effect=URLError('unavailable')), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(str(Path('arena/populate.py').resolve()),run_name='__main__')
        self.assertEqual(error.exception.code,1)

    def test_profiles_sharing_api_id_and_legacy_votes_stay_separate(self):
        profiles=active_profiles(self.config)
        first=deepcopy(next(iter(profiles.values())))
        second=deepcopy(first);second.update(profile_id=first['api_model']+'@high:joke-v1',reasoning={'effort':'high','exclude':True},reasoning_label='high')
        profiles[second['profile_id']]=second
        path=self.directory/'history.db'
        legacy=Store(path);legacy.new_session('session')
        old=[{'status':'validated','requested_model':m,'returned_model':m,'word':'coffee','joke':'A coffee joke.'} for m in ('openai/gpt-5-mini','anthropic/claude-haiku-4.5')]
        receipt=self.directory/'old.jsonl';receipt.write_text('\n'.join(map(json.dumps,old)))
        legacy.import_receipt(receipt)
        outstanding=legacy.battle('session')
        store=Store(path,profiles)
        self.assertEqual(store.battle('session'),outstanding)
        store.vote('session',outstanding['id'],'left')
        self.assertEqual(store.leaderboard()['total_votes'],0)
        self.assertEqual(store.leaderboard()['pool_pairs'],0)
        with self.assertRaises(ArenaError):store.battle('session')
        fresh=[]
        for profile in (first,second):
            fresh.append(dict(status='validated',requested_model=profile['api_model'],returned_model=profile['api_model'],profile_id=profile['profile_id'],reasoning=profile['reasoning'],prompt_version='joke-v1',word='airport',joke='Another airport joke.'))
        receipt.write_text('\n'.join(map(json.dumps,fresh)))
        self.assertEqual(store.import_receipt(receipt),1)
        battle=store.battle('session');store.vote('session',battle['id'],'right')
        self.assertEqual(store.leaderboard()['total_votes'],1)
        self.assertEqual(Store(path).leaderboard()['total_votes'],2)
        self.assertEqual(len([r for r in store.leaderboard()['models'] if r['comparisons']]),2)


    def test_provider_key_limit_is_required_before_paid_generation(self):
        self.key_guard.stop()
        valid={'data':{'limit':2,'limit_remaining':2,'limit_reset':None}}
        with patch.object(populate,'urlopen',return_value=io.StringIO(json.dumps(valid))) as transport:
            populate.verify_key_budget('test-secret',self.plan)
        self.assertEqual(transport.call_args.args[0].full_url,'https://openrouter.ai/api/v1/key')
        cases=[{'limit':None,'limit_remaining':None}, {'limit':3,'limit_remaining':3},
               {'limit':2,'limit_remaining':0}, {'limit':2,'limit_remaining':2,'limit_reset':'daily'},
               {'limit':2,'limit_remaining':0.0000001}, {'limit':'NaN','limit_remaining':2},
               {'limit':1,'limit_remaining':2}, {}]
        for details in cases:
            with patch.object(populate,'urlopen',return_value=io.StringIO(json.dumps({'data':details}))), \
                    patch.object(populate,'request_completion') as paid, self.assertRaises(ValueError):
                populate.generate_batch('test-secret',self.plan,self.directory/'no-pay',require_key_limit=True)
            paid.assert_not_called()
            self.assertFalse((self.directory/'no-pay').exists())
        with patch.object(populate,'urlopen',side_effect=URLError('unavailable')), self.assertRaises(ValueError):
            populate.verify_key_budget('test-secret',self.plan)
