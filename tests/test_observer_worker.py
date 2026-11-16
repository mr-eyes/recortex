"""Deterministic boundary tests; fake extraction/review does not measure semantic quality."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/recortex/scripts'))
import index
import observer
import observer_runtime as runtime
import observer_sources as sources
import observer_store as store
import observer_worker as worker
from tests.observer_source_fixture import fixture_trusted_ancestors

REPO='octo-org/octo-repo'
QUOTE='For the parser, use strict quoting and keep the warning.'
BODY='''<!-- ghim:event type=decision at=2026-03-01T12:00:00Z -->
### Decision: 2026-03-01 12:00 UTC - Strict parser

**In plain words, we decided to:** keep strict quoting with a warning

**Decision:** Use strict quoting and keep the warning.

**Decided by:** Owner, 2026-03-01 12:00 UTC

**Exact words:**

> For the parser, use strict quoting and keep the warning.

**Where recorded:** Original user message in the active session; source identity accompanies this record.

**Effect:** Strict quoting remains the default.

**Next step:** Update the parser tests, owner maintainer
'''
REAL_REFRESH=worker.refresh_record
CONTEXT={'targets':[{'issue':2,'title':'Parser','body':'Strict parser requirements.'}], 'memories':[], 'incomplete':False}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        metadata=fixture_trusted_ancestors(self.base);metadata.__enter__();self.addCleanup(metadata.__exit__,None,None,None)
        self.project=str(self.base/'project')
        env=patch.dict(os.environ,{'XDG_STATE_HOME':str(self.base/'state'),'XDG_CACHE_HOME':str(self.base/'cache'),
                                  'CLAUDE_CONFIG_DIR':str(self.base/'claude'),'CLAUDE_PROJECT_DIR':self.project},clear=True)
        env.start();self.addCleanup(env.stop)
        self.config={'repo':REPO,'observer':{'enabled':True,'publisher_id':runtime.publisher_id(create=True)}}
        self.folder=Path(sources.project_directory(self.project));self.folder.mkdir(parents=True)
        for d in (self.folder,self.folder.parent,self.folder.parent.parent):d.chmod(0o700)
        self.path=self.folder/'session-1.jsonl';self.path.write_text('');self.path.chmod(0o600)
        self.hook={'session_id':'session-1','transcript_path':str(self.path),'hook_event_name':'Stop'}
        with store.Store(REPO,config=self.config) as db:
            db.set_control('observer_approval',{'repo':REPO,'settings':runtime.settings(self.config),'publisher_id':self.config['observer']['publisher_id']})
            db.set_control('runtime_gate',{'fake_test_only':True})
        for target,value in ((worker.recortex,'allowed_users'),):
            p=patch.object(target,value,return_value={'octocat'});p.start();self.addCleanup(p.stop)
        p=patch.object(runtime,'gate_valid',return_value=True);p.start();self.addCleanup(p.stop)
        p=patch.object(worker,'record_context',return_value=CONTEXT);p.start();self.addCleanup(p.stop)
        p=patch.object(worker,'refresh_record',return_value=True);p.start();self.addCleanup(p.stop)
        self.calls=[]
    def capture(self,text=QUOTE,role='user'):
        with self.path.open('a') as f:f.write(json.dumps({'message':{'role':role,'content':text},'timestamp':'2026-03-01T12:00:00Z'})+'\n')
        rows,cursor=sources.scan(self.hook,self.project)
        with store.Store(REPO,config=self.config) as db:db.capture(rows)
        return rows[-1]['source_id']
    def candidate(self,sid,**changes):
        return dict({'kind':'decision','issue':2,'body':BODY,'sources':[sid],'excerpts':[QUOTE]},**changes)
    def invoke(self,candidate,accepted=True,bad_hash=False):
        def call(payload,schema,purpose,config):
            self.calls.append((purpose,payload))
            if purpose=='extract':return {'ok':True,'structured':{'candidates':[candidate],'ignored':[]}}
            return {'ok':True,'structured':{'candidate_hash':'0'*64 if bad_hash else payload['candidate_hash'],'accepted':accepted,'reason':'supported' if accepted else 'unsupported'}}
        return call
    def run_worker(self,invoke):return worker.run_once(REPO,self.config,self.project,invoke)
    def test_two_independent_passes_bind_review_before_persistence(self):
        sid=self.capture();candidate=self.candidate(sid)
        report=self.run_worker(self.invoke(candidate))
        self.assertEqual('ok',report['code']);self.assertEqual(1,len(report['ready']))
        self.assertEqual(['extract','review'],[x[0] for x in self.calls])
        with store.Store(REPO,config=self.config) as db:
            event=db.event(report['ready'][0]);self.assertTrue(db.review_valid(event));self.assertEqual('ready',event['state'])
            self.assertEqual(2,db.status()['invocations_24h'])
    def test_overlapping_candidates_never_demote_ready_source(self):
        sid=self.capture();candidate=self.candidate(sid);delegate=self.invoke(candidate)
        def call(payload,schema,purpose,config):
            if purpose=='extract':return {'ok':True,'structured':{'candidates':[candidate,candidate],'ignored':[]}}
            return delegate(payload,schema,purpose,config)
        report=self.run_worker(call)
        self.assertEqual(1,len(report['ready']))
        with store.Store(REPO,config=self.config) as db:self.assertEqual('ready',db.observation(sid)['state'])
    def test_wrong_review_hash_and_rejection_never_persist_raw_draft(self):
        sid=self.capture();candidate=self.candidate(sid)
        report=self.run_worker(self.invoke(candidate,bad_hash=True));self.assertFalse(report['ready'])
        with store.Store(REPO,config=self.config) as db:
            self.assertEqual([],db.events());self.assertNotIn(BODY.encode(),Path(db.path).read_bytes())
            self.assertEqual('review_failed',db.observation(sid)['reason'])
    def test_assistant_quote_cannot_create_user_decision(self):
        sid=self.capture(role='assistant')
        self.run_worker(self.invoke(self.candidate(sid)))
        self.assertEqual(['extract'],[x[0] for x in self.calls])
        with store.Store(REPO,config=self.config) as db:self.assertEqual('decision_authority',db.observation(sid)['reason'])
    def test_model_cannot_bind_unseen_source_or_destination(self):
        sid=self.capture();bad=self.candidate(sid,sources=[sid,'f'*64])
        self.run_worker(self.invoke(bad));self.assertEqual(1,len(self.calls))
        with store.Store(REPO,config=self.config) as db:self.assertEqual([],db.events())
    def test_secret_source_never_reaches_model(self):
        sid=self.capture('token ghp_'+'A'*36)
        self.run_worker(self.invoke(self.candidate(sid)))
        self.assertEqual([],self.calls)
        with store.Store(REPO,config=self.config) as db:self.assertEqual('privacy',db.observation(sid)['reason'])
    def test_budget_counts_failed_extractor_and_queued_data_survives(self):
        sid=self.capture();self.config['observer']['max_invocations']=1
        with store.Store(REPO,config=self.config) as db:db.set_control('observer_approval',{'repo':REPO,'settings':runtime.settings(self.config),'publisher_id':self.config['observer']['publisher_id']})
        def failed(*args):return {'ok':False,'structured':None}
        self.assertEqual('extract-failed',self.run_worker(failed)['code'])
        with store.Store(REPO,config=self.config) as db:
            self.assertEqual(1,db.status()['invocations_24h']);self.assertEqual('deferred',db.observation(sid)['state'])
    def test_permanent_deferred_sources_do_not_starve_new_work(self):
        for n in range(10):
            sid=self.capture('Unavailable '+str(n))
            with store.Store(REPO,config=self.config) as db:db.defer(sid,'source_unavailable')
        sid=self.capture()
        self.assertEqual(1,len(self.run_worker(self.invoke(self.candidate(sid)))['ready']))
    def test_ready_events_do_not_starve_new_work(self):
        sid=self.capture();self.run_worker(self.invoke(self.candidate(sid)))
        for n in range(10):
            sid=self.capture(QUOTE+' Additional '+str(n))
            self.assertEqual(1,len(self.run_worker(self.invoke(self.candidate(sid)))['ready']))
    def test_rejected_candidate_not_retried_on_every_hook(self):
        sid=self.capture();self.run_worker(self.invoke(self.candidate(sid),accepted=False))
        self.calls=[];self.run_worker(self.invoke(self.candidate(sid)));self.assertEqual([],self.calls)
    def test_automatic_publishes_existing_ready_when_capture_paused(self):
        sid=self.capture();report=self.run_worker(self.invoke(self.candidate(sid)))
        self.config['observer']['mode']='automatic'
        with store.Store(REPO,config=self.config) as db:
            db.set_control('capture_paused',True)
            db.set_control('observer_approval',{'repo':REPO,'settings':runtime.settings(self.config),'publisher_id':self.config['observer']['publisher_id']})
        with patch('observer_publisher.publish') as publish:
            self.run_worker(self.invoke(self.candidate(sid)))
            publish.assert_called_once_with(REPO,report['ready'][0],self.config)
    def test_refresh_interval_and_publication_request(self):
        options=runtime.settings(self.config)
        with store.Store(REPO,config=self.config) as db,patch.object(index,'sync',return_value={'complete':True}) as sync:
            self.assertTrue(REAL_REFRESH(REPO,self.config,db,options));self.assertEqual(1,sync.call_count)
            self.assertTrue(REAL_REFRESH(REPO,self.config,db,options));self.assertEqual(1,sync.call_count)
            db.set_control('sync_needed',True)
            self.assertTrue(REAL_REFRESH(REPO,self.config,db,options));self.assertEqual(2,sync.call_count)
            self.assertFalse(db.get_control('sync_needed'))
            db.set_control('sync_last_attempt',time.time()-301)
            self.assertTrue(REAL_REFRESH(REPO,self.config,db,options));self.assertEqual(3,sync.call_count)
            self.assertEqual(10,sync.call_args.kwargs['deadline'])
    def test_failed_refresh_prevents_inference_and_publication(self):
        sid=self.capture()
        with patch.object(worker,'refresh_record',return_value=False),patch('observer_publisher.publish') as publish:
            report=self.run_worker(self.invoke(self.candidate(sid)))
            self.assertEqual('record-unavailable',report['code']);self.assertEqual([],self.calls);publish.assert_not_called()
    def test_retention_runs_with_runtime_unavailable_and_capture_paused(self):
        with store.Store(REPO,config=self.config) as db:db.set_control('capture_paused',True)
        with patch.object(runtime,'gate_valid',return_value=False),patch.object(store.Store,'expire_handled',autospec=True) as expire:
            self.assertEqual('runtime-gate',self.run_worker(self.invoke({}))['code'])
            self.assertEqual(1,expire.call_count);self.assertEqual(7,expire.call_args.args[1])
            self.run_worker(self.invoke({}));self.assertEqual(1,expire.call_count)
    def test_failure_of_refresh_is_visible_and_rate_limited(self):
        options=runtime.settings(self.config)
        with store.Store(REPO,config=self.config) as db,patch.object(index,'sync',side_effect=worker.recortex.GhError('offline')) as sync:
            self.assertFalse(REAL_REFRESH(REPO,self.config,db,options));self.assertFalse(db.get_control('sync_ok'))
            self.assertFalse(REAL_REFRESH(REPO,self.config,db,options));self.assertEqual(1,sync.call_count)
    def test_original_restricted_strings_checked_before_json_escaping(self):
        self.config['restricted_patterns']=[{'pattern':r'^PRIVATE-SAMPLE$','name':'private'}]
        sid=self.capture('PRIVATE-SAMPLE')
        self.run_worker(self.invoke(self.candidate(sid)));self.assertEqual([],self.calls)
        self.assertTrue(worker.source_privacy_problem({'tool_results':[{'input':{'command':'PRIVATE\tSAMPLE'}}]},
                        {'restricted_patterns':[{'pattern':r'PRIVATE\s+SAMPLE','name':'private'}]}))
    def test_secret_tool_argument_never_reaches_either_model(self):
        invocation={'message':{'role':'assistant','content':[{'type':'tool_use','id':'call1','name':'Bash','input':{'command':'echo ghp_'+'A'*36}}]}}
        result={'message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'call1','content':'Safe output','is_error':False}]}}
        self.path.write_text(json.dumps(invocation)+'\n'+json.dumps(result)+'\n')
        rows,_=sources.scan(self.hook,self.project)
        with store.Store(REPO,config=self.config) as db:db.capture(rows)
        self.run_worker(self.invoke({}));self.assertEqual([],self.calls)
        with store.Store(REPO,config=self.config) as db:self.assertTrue(all(row['reason']=='privacy' for row in db.pending()))
    def test_cli_dryrun_and_pause_are_independent(self):
        with patch.object(observer.recortex,'load_config',return_value=self.config),contextlib.redirect_stdout(io.StringIO()):
            with patch.object(worker,'run_once') as run:
                self.assertEqual(0,observer.main(['run-worker','--dry-run']));run.assert_not_called()
            self.assertEqual(0,observer.main(['pause','publication']))
        with store.Store(REPO,config=self.config) as db:
            self.assertTrue(db.get_control('publication_paused'));self.assertFalse(db.get_control('capture_paused',False))
    def test_cli_failed_runtime_check_does_not_certify(self):
        output=io.StringIO()
        proof={'ok':False,'code':'timeout','requested_model':'opus','actual_model':'test-runtime-model'}
        with patch.object(observer.recortex,'load_config',return_value=self.config),contextlib.redirect_stdout(output),patch.object(runtime,'check_runtime',return_value=proof),patch.object(runtime,'gate_valid',return_value=False):
            self.assertEqual(1,observer.main(['runtime-check']))
        reported=json.loads(output.getvalue())
        self.assertEqual('opus',reported['requested_model'])
        self.assertEqual('test-runtime-model',reported['actual_model'])
        with store.Store(REPO,config=self.config) as db:
            self.assertIsNone(db.get_control('runtime_gate'));self.assertEqual(1,db.status()['invocations_24h'])
            self.assertEqual(reported,db.get_control('runtime_last'))


class RecordContextTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript(index.SCHEMA)
        self.db.executemany('INSERT INTO meta VALUES (?,?)',[('mind_derived','1'),('sync_complete','1'),('synced_at','2026-03-01T12:00:00Z')])
    def issue(self,n,author='octocat',title='Parser',body='Strict parser handling',edits=1):
        self.db.execute("INSERT INTO issues(number,title,body,state,author,is_pr,timeline_read,edits_read) VALUES(?,?,?,'open',?,0,1,?)",(n,title,body,author,edits))
        self.db.execute("INSERT INTO labels VALUES(?,'final-record')",(n,))
    def context(self,query='parser'):
        with patch.object(index,'connect',return_value=self.db),patch.object(worker.recortex,'allowed_users',return_value={'octocat'}):return worker.record_context(REPO,{},query)
    def test_destination_authors_edits_and_relevance(self):
        self.issue(1,author='outsider');self.issue(2,edits=0);self.issue(3);self.issue(4,title='Colors',body='Blue card')
        self.issue(5);self.db.execute("INSERT INTO timeline(issue,event,actor) VALUES(5,'edited','outsider')")
        result=self.context();self.assertEqual([3],[row['issue'] for row in result['targets']])
    def test_large_destination_is_not_cut_mid_constraint(self):
        self.issue(1,body='Parser must not '+('x'*7000));self.issue(2)
        result=self.context();self.assertEqual([2],[row['issue'] for row in result['targets']]);self.assertEqual(1,result['omitted_targets'])


class ObserverCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.project=self.base/'project';self.project.mkdir(mode=0o700)
        self.config=self.project/'.recortex.json';self.config.write_text(json.dumps({'repo':REPO}));self.config.chmod(0o600)
        trusted=self.base/'trusted';trusted.write_text(REPO+' octocat\n');trusted.chmod(0o600)
        self.env=dict(os.environ,CLAUDE_PROJECT_DIR=str(self.project),RECORTEX_TRUSTED_FILE=str(trusted),
                      XDG_STATE_HOME=str(self.base/'state'),XDG_CACHE_HOME=str(self.base/'cache'),XDG_CONFIG_HOME=str(self.base/'config'))
        self.env.pop('RECORTEX_CONFIG',None)
    def cli(self,*args):
        result=subprocess.run([sys.executable,str(ROOT/'skills/recortex/scripts/observer.py')]+list(args),
                              cwd=self.project,env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual('',result.stderr)
        return result,json.loads(result.stdout)
    def test_readonly_dryrun_and_status_do_not_create_state(self):
        for args in (('run-worker','--dry-run'),('status',),('pending',)):
            result,value=self.cli(*args);self.assertEqual(0,result.returncode);self.assertEqual('empty',value['code'])
            self.assertFalse((self.base/'state').exists())
    def test_enable_requires_explicit_confirmation_and_exact_local_settings(self):
        result,preview=self.cli('enable');self.assertFalse(preview['approved']);self.assertNotIn('observer',json.loads(self.config.read_text()))
        self.assertFalse((self.base/'state').exists())
        result,approved=self.cli('enable','--yes');self.assertTrue(approved['approved']);self.assertEqual(0,result.returncode)
        _,status=self.cli('status');self.assertTrue(status['approved']);self.assertFalse(status['runtime_ready'])
        config=json.loads(self.config.read_text());config['observer']['max_candidates']=2;self.config.write_text(json.dumps(config))
        _,status=self.cli('status');self.assertFalse(status['approved'])
    def test_model_preview_is_explicit_and_approval_binds_the_choice(self):
        before=self.config.read_bytes()
        result,preview=self.cli('enable','--model','opus')
        self.assertEqual(0,result.returncode);self.assertFalse(preview['approved'])
        self.assertEqual('opus',preview['settings']['model'])
        self.assertEqual({'source':'explicit','requested_model':'opus'},preview['model_selection'])
        self.assertEqual(before,self.config.read_bytes());self.assertFalse((self.base/'state').exists())
        result,approved=self.cli('enable','--model','opus','--yes')
        self.assertEqual(0,result.returncode);self.assertTrue(approved['approved'])
        _,status=self.cli('status');self.assertTrue(status['approved'])
        self.assertEqual('opus',status['settings']['model']);self.assertFalse(status['runtime_ready'])
        _,preview=self.cli('enable');self.assertEqual('opus',preview['settings']['model'])
        config=json.loads(self.config.read_text());config['observer']['model']='sonnet';self.config.write_text(json.dumps(config))
        _,status=self.cli('status');self.assertFalse(status['approved'])
    def test_isolated_default_is_named_and_can_be_selected_explicitly(self):
        _,preview=self.cli('enable')
        self.assertEqual('isolated_cli_default',preview['model_selection']['source'])
        self.assertIn('not loaded',preview['model_selection']['note'])
        self.cli('enable','--model','opus','--yes')
        _,preview=self.cli('enable','--model','')
        self.assertEqual('',preview['settings']['model'])
        self.assertEqual('isolated_cli_default',preview['model_selection']['source'])
        self.assertEqual('opus',json.loads(self.config.read_text())['observer']['model'])
    def test_invalid_model_choice_is_rejected_before_any_write(self):
        before=self.config.read_bytes()
        result,value=self.cli('enable','--model','model with spaces','--yes')
        self.assertEqual(1,result.returncode);self.assertEqual('observer-unavailable',value['code'])
        self.assertEqual(before,self.config.read_bytes());self.assertFalse((self.base/'state').exists())
    def test_publication_pause_survives_process_restart(self):
        self.cli('enable','--yes');self.cli('pause','publication')
        _,status=self.cli('status');self.assertTrue(status['publication_paused']);self.assertFalse(status['capture_paused'])
        self.cli('resume','publication');_,status=self.cli('status');self.assertFalse(status['publication_paused'])
    def test_automatic_enable_refused_without_runtime_proof(self):
        result,value=self.cli('enable','--mode','automatic','--yes')
        self.assertEqual(1,result.returncode);self.assertFalse(value['approved']);self.assertEqual('runtime-gate',value['code'])
        self.assertNotIn('observer',json.loads(self.config.read_text()))


if __name__=='__main__':unittest.main()
