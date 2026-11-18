"""The observer's refresh of the project's status summary (1.3.0, spec section 21), with the existing fake runtime: no model is
called. Deterministic boundary tests; the fake does not measure whether a summary is any good."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/recortex/scripts'))
import observer_runtime as runtime
import observer_sources as sources
import observer_store as store
import observer_worker as worker
import record_format
from tests.observer_source_fixture import fixture_trusted_ancestors

REPO = 'octo-org/octo-repo'
BLOCK = ("**In short:** The trial is in its second season.\n\nLately a result was added.\n\n"
         "**Last updated:** 2026-04-09 09:00 EST by the session\n**Current focus:** Draft the Discussion\n"
         "**Active issues:** none\n**Last decisions:** none yet\n**Next actions:**\n\n| Action | Owner | Where |\n"
         "|---|---|---|\n| Draft the Discussion | the first author | the topic |\n\n**Holds:** none")
MATERIAL = {'block': BLOCK, 'posted': '2026-04-09T14:00:00Z', 'plan': {'parts': 3},
            'memories': [{'kind': 'result', 'day': '2026-04-10', 'plain': 'the dry-spell comparison agrees with the pooled fit'}]}
FIRST = 'The trial is in its second season and the Results part is drafted.'
SECOND = 'Lately the dry-spell comparison was added. Next is the Discussion; whether texture explains it is in doubt.'


class SummaryBox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        metadata = fixture_trusted_ancestors(self.base)
        metadata.__enter__()
        self.addCleanup(metadata.__exit__, None, None, None)
        self.project = str(self.base / 'project')
        env = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.base / 'state'), 'XDG_CACHE_HOME': str(self.base / 'cache'),
                                      'XDG_CONFIG_HOME': str(self.base / 'config'), 'HOME': str(self.base / 'home'),
                                      'CLAUDE_CONFIG_DIR': str(self.base / 'claude'), 'CLAUDE_PROJECT_DIR': self.project},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.config = {'repo': REPO, 'hub': 4, 'observer': {'enabled': True, 'mode': 'automatic', 'level': 'everything',
                                                            'publisher_id': runtime.publisher_id(create=True)}}
        with store.Store(REPO, config=self.config) as db:
            db.set_control('runtime_gate', {'fake_test_only': True})
        runtime.approve(REPO, self.config)
        for target, name, value in ((worker.recortex, 'allowed_users', {'octocat'}), (worker, 'summary_wanted', True),
                                    (worker, 'summary_material', MATERIAL), (worker, 'recent_views', [])):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (('gate_valid', True),):
            patcher = patch.object(runtime, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []
        self.posted = []
        patcher = patch.object(worker, 'post_summary', side_effect=lambda repo, config, text: self.posted.append(text) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, paragraphs=(FIRST, SECOND), accepted=True, reason='supported', bad_hash=False, summary_ok=True):
        def call(payload, schema, purpose, options):
            self.calls.append((purpose, payload))
            if purpose == 'summary':
                return {'ok': summary_ok, 'structured': {'paragraphs': list(paragraphs)}}
            return {'ok': True, 'structured': {'summary_hash': '0' * 64 if bad_hash else payload['summary_hash'],
                                               'accepted': accepted, 'reason': reason}}
        return call

    def refresh(self, invoke=None, **kw):
        options = dict(runtime.settings(self.config), **kw)
        with store.Store(REPO, config=self.config) as db:
            return worker.refresh_summary(REPO, self.config, db, options, invoke or self.invoke(), self.project)

    def used(self):
        with store.Store(REPO, config=self.config) as db:
            return db.db.execute('SELECT COUNT(*) FROM invocations').fetchone()[0]


class SummaryRefreshTests(SummaryBox):
    def test_a_stale_summary_is_written_reviewed_and_posted_as_a_new_resume_block(self):
        self.assertEqual('posted', self.refresh())
        self.assertEqual(['summary', 'summary_review'], [purpose for purpose, _ in self.calls])
        self.assertEqual(1, len(self.posted))
        text = self.posted[0]
        self.assertTrue(text.startswith('**In short:** %s\n\n%s\n\n**Last updated:**' % (FIRST, SECOND)))
        self.assertEqual("%s\n\n%s" % (FIRST, SECOND), record_format.resume_in_short(text))
        self.assertIn(' by the observer', text.split('\n\n')[2].split('\n')[0])     # the stamp says who
        for field in ('Current focus:** Draft the Discussion', 'Active issues:** none', 'Holds:** none',
                      '| Draft the Discussion | the first author | the topic |'):
            self.assertIn(field, text)                                             # the main session's fields are as they were
        self.assertNotIn('The trial is in its second season.', text)               # the old In short is replaced
        with store.Store(REPO, config=self.config) as db:
            self.assertTrue(db.get_control('sync_needed'))                          # the next refresh reads the new block

    def test_the_time_stamp_of_the_new_block_is_in_the_zone_the_record_states_and_in_utc_when_it_states_none(self):
        stamp = re.compile(r'\*\*Last updated:\*\* ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}) ([A-Z]+) by the observer')
        self.config['timezone'] = 'US Eastern time, written as 2026-11-16 10:10 EST'
        self.assertEqual('posted', self.refresh())
        self.assertIn(stamp.search(self.posted[0]).group(2), ('EST', 'EDT'))
        del self.config['timezone']
        with store.Store(REPO, config=self.config) as db:
            db.set_control('summary_last', None)
        self.assertEqual('posted', self.refresh())
        self.assertEqual('UTC', stamp.search(self.posted[1]).group(2))

    def test_it_rests_on_the_index_and_the_conversation_the_worker_holds(self):
        views = [{'id': 'a' * 64, 'role': 'user', 'at': '2026-04-10T09:00:00Z', 'direct_user': True, 'text': 'Add the dry-spell comparison.'}]
        with patch.object(worker, 'recent_views', return_value=views):
            self.refresh()
        payload = self.calls[0][1]
        self.assertEqual(BLOCK, payload['previous'])
        self.assertEqual(MATERIAL['memories'], payload['memories'])
        self.assertEqual({'parts': 3}, payload['plan'])
        self.assertEqual(views, payload['sources'])
        review = self.calls[1][1]
        self.assertEqual([FIRST, SECOND], review['summary'])
        self.assertEqual(views, review['sources'])

    def test_both_calls_are_counted_in_the_daily_cap(self):
        self.assertEqual(0, self.used())
        self.refresh()
        self.assertEqual(2, self.used())
        self.assertEqual('budget', self.refresh(max_invocations=2))                 # the cap is spent: no call, no post
        self.assertEqual(['summary', 'summary_review'], [purpose for purpose, _ in self.calls])
        self.assertEqual(1, len(self.posted))
        self.assertEqual('budget', self.refresh(max_invocations=3))                 # the reviewer would be the fourth
        self.assertEqual(['summary', 'summary_review', 'summary'], [purpose for purpose, _ in self.calls])
        self.assertEqual(1, len(self.posted))

    def test_a_current_summary_costs_no_call(self):
        with patch.object(worker, 'summary_wanted', return_value=False):
            self.assertEqual('current', self.refresh())
        self.assertEqual([], self.calls)
        self.assertEqual(0, self.used())

    def test_without_a_block_to_refresh_nothing_is_called(self):
        with patch.object(worker, 'summary_material', return_value=None):
            self.assertEqual('no-block', self.refresh())
        self.assertEqual([], self.calls)

    def test_the_reviewer_can_stop_it_and_a_review_for_another_summary_counts_for_nothing(self):
        for kw in (dict(accepted=False, reason='unsupported'), dict(accepted=True, reason='numbers'), dict(bad_hash=True)):
            self.posted.clear()
            self.assertEqual('summary-rejected', self.refresh(self.invoke(**kw)), kw)
            self.assertEqual([], self.posted)

    def test_a_failed_call_posts_nothing(self):
        self.assertEqual('summary-failed', self.refresh(self.invoke(summary_ok=False)))
        self.assertEqual(['summary'], [purpose for purpose, _ in self.calls])         # no reviewer for what failed
        self.assertEqual([], self.posted)

    def test_a_summary_that_breaks_the_form_is_never_reviewed_or_posted(self):
        self.assertEqual('summary-failed', self.refresh(self.invoke(paragraphs=['One.', 'Two.', 'Three.'])))   # the schema says two
        self.assertEqual([], self.posted)
        self.calls.clear()
        bad = {'an issue number': ['The fix is in #12 now.'],
               'too long': ['A' * 1100, 'B' * 200], 'an address': ['See https://example.org for it.'],
               'a mention': ['Thanks to @someone for it.'], 'markup': ['A <b>bold</b> claim.'], 'a list': ['- one\n- two'],
               'bold': ['A **bold** claim.'], 'empty': ['  ']}
        for name, paragraphs in bad.items():
            self.calls.clear()
            self.assertEqual('summary-form', self.refresh(self.invoke(paragraphs=paragraphs)), name)
            self.assertEqual(['summary'], [purpose for purpose, _ in self.calls], name)
        self.assertEqual([], self.posted)

    def test_a_refresh_that_did_not_post_is_not_tried_again_at_once(self):
        rejecting = self.invoke(accepted=False, reason='unsupported')
        self.assertEqual('summary-rejected', self.refresh(invoke=rejecting))
        with store.Store(REPO, config=self.config) as db:
            db.set_control('summary_last', {'at': time.time(), 'code': 'summary-rejected'})
        self.assertEqual('waiting', self.refresh(invoke=rejecting))
        self.assertEqual(['summary', 'summary_review'], [purpose for purpose, _ in self.calls])   # no call while it waits
        with store.Store(REPO, config=self.config) as db:   # six hours on, it is tried again
            db.set_control('summary_last', {'at': time.time() - worker.SUMMARY_RETRY - 1, 'code': 'summary-rejected'})
        self.assertEqual('summary-rejected', self.refresh(invoke=rejecting))
        self.assertEqual(4, len(self.calls))

    def test_a_secret_in_the_summary_stops_it_and_a_secret_in_the_material_stops_the_call(self):
        key = 'sk-' + 'a1B2c3D4' * 4
        self.assertEqual('summary-privacy', self.refresh(self.invoke(paragraphs=['The key is %s.' % key])))
        self.assertEqual([], self.posted)
        self.calls.clear()
        with patch.object(worker, 'summary_material', return_value=dict(MATERIAL, memories=[
                {'kind': 'result', 'day': '2026-04-10', 'plain': 'the key is %s' % key}])):
            self.assertEqual('privacy', self.refresh())
        self.assertEqual([], self.calls)                                              # nothing was sent to a model

    def test_a_post_that_fails_is_a_code_and_not_an_exception(self):
        with patch.object(worker, 'post_summary', return_value=False):
            self.assertEqual('post-failed', self.refresh())
        with store.Store(REPO, config=self.config) as db:
            self.assertFalse(db.get_control('sync_needed'))

    def test_the_pure_pieces(self):
        self.assertEqual('', worker.summary_problem([FIRST, SECOND], self.config))
        self.assertEqual('', worker.summary_problem(['Only one paragraph.'], self.config))
        self.assertEqual('form', worker.summary_problem('not a list', self.config))
        self.assertEqual('form', worker.summary_problem([], self.config))
        self.assertEqual('form', worker.summary_problem([FIRST, 5], self.config))
        block = worker.summary_block(MATERIAL, [FIRST], '2026-04-11 10:00 UTC')
        self.assertEqual(FIRST, record_format.resume_in_short(block))
        self.assertIn('**Last updated:** 2026-04-11 10:00 UTC by the observer', block)
        self.assertEqual(1, block.count('**In short:**'))


FIELDS = {'active': [{'issue': 14, 'done': 3, 'steps': 5}, {'issue': 16, 'done': 0, 'steps': 0}],
          'next': [{'issue': 14, 'n': 4, 'text': 'Rerun the benchmark files'}],
          'decisions': [{'day': '2026-04-10', 'plain': 'keep strict quoting with a warning',
                         'address': 'https://github.com/octo-org/octo-repo/issues/2#issuecomment-9201'}]}


class StatusBlockTests(SummaryBox):
    """1.5.0: the model writes In short alone. The tasks, the next steps and the last decisions are built by code from
    the index, and the focus and the holds are copied from the newest block word for word."""

    def block(self, paragraphs=(FIRST, SECOND), material=None):
        return worker.summary_block(dict(material or MATERIAL, fields=FIELDS), list(paragraphs), '2026-04-11 10:00 UTC')

    def test_the_code_fields_are_built_from_the_index_and_the_model_writes_in_short_alone(self):
        text = self.block()
        self.assertEqual('%s\n\n%s' % (FIRST, SECOND), record_format.resume_in_short(text))
        self.assertIn('**Active issues:** #14, 3 of 5 steps; #16\n', text)
        self.assertIn('| Step 4: Rerun the benchmark files | not stated | #14 |', text)
        self.assertIn('**Last decisions:** 2026-04-10, keep strict quoting with a warning, '
                      'https://github.com/octo-org/octo-repo/issues/2#issuecomment-9201\n', text)
        self.assertIn('**Last updated:** 2026-04-11 10:00 UTC by the observer', text)
        self.assertNotIn('Draft the Discussion | the first author', text)   # the old table is not copied: code wrote it
        for field in ('Active issues', 'Last decisions', 'Next actions'):
            self.assertEqual(1, text.count('**%s:**' % field))
        # a model that writes the fields into its paragraphs changes none of them
        hostile = self.block(['The fix is done.', '**Active issues:** #99 forged'])
        self.assertIn('**Active issues:** #14, 3 of 5 steps; #16\n', hostile)
        self.assertEqual(2, hostile.count('**Active issues:**'))

    def test_the_focus_and_the_holds_are_copied_word_for_word(self):
        written = BLOCK.replace('**Current focus:** Draft the Discussion', '**Current focus:** Draft the Discussion, then the\nLimitations *and* the | table.') \
                       .replace('**Holds:** none', '**Holds:** Do not touch #5 until the owner says so.')
        text = self.block(material=dict(MATERIAL, block=written))
        self.assertIn('**Current focus:** Draft the Discussion, then the\nLimitations *and* the | table.\n**Active issues:**', text)
        self.assertTrue(text.endswith('**Holds:** Do not touch #5 until the owner says so.\n'))

    def test_a_block_with_nothing_to_tell_says_none(self):
        text = worker.summary_block(dict(MATERIAL, fields={'active': [], 'next': [], 'decisions': []}), [FIRST], '2026-04-11 10:00 UTC')
        self.assertIn('**Active issues:** none\n', text)
        self.assertIn('**Last decisions:** none\n', text)
        self.assertIn('| none | not stated | none |', text)

    def test_the_new_block_passes_the_resume_linter(self):
        import lint_drafts
        import resume_block
        comment = resume_block.build_comment(self.block())
        findings = lint_drafts.lint_text(comment, kind='resume', config=self.config)
        self.assertEqual([], [f for f in findings if f.level == 'error'])

    def test_a_material_without_code_fields_keeps_the_newest_blocks_fields(self):
        text = worker.summary_block(MATERIAL, [FIRST], '2026-04-11 10:00 UTC')
        self.assertIn('| Draft the Discussion | the first author | the topic |', text)
        self.assertIn('**Active issues:** none', text)

    def test_code_fields_come_from_the_told_tasks_and_decisions(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE events(comment INTEGER, url TEXT)')
        db.executemany('INSERT INTO events VALUES (?, ?)', [(1, 'https://github.com/octo-org/octo-repo/issues/2#issuecomment-1'), (2, '')])
        tasks = {'tasks': [
            {'issue': 14, 'state': 'open', 'counts': {'steps': 5, 'done': 3}, 'steps': [
                {'n': 1, 'state': 'done', 'text': 'a'}, {'n': 4, 'state': 'open', 'text': 'Rerun | the files'}]},
            {'issue': 15, 'state': 'closed', 'counts': {'steps': 2, 'done': 2}, 'steps': []},
            {'issue': 16, 'state': 'open', 'counts': {'steps': 0, 'done': 0}, 'steps': []}]}
        told = {1: {'id': 1, 'kind': 'decision', 'state': 'stands', 'time': 'b', 'at': '2026-04-10T10:00', 'plain': 'keep strict quoting', 'title': ''},
                2: {'id': 2, 'kind': 'decision', 'state': 'replaced', 'time': 'c', 'at': '2026-04-11T10:00', 'plain': 'old', 'title': ''},
                3: {'id': 3, 'kind': 'result', 'state': 'stands', 'time': 'd', 'at': '2026-04-12T10:00', 'plain': 'a result', 'title': ''}}
        with patch.object(worker.index, 'tasks', return_value=tasks):
            fields = worker.code_fields(db, {'octocat'}, told)
        self.assertEqual([{'issue': 14, 'done': 3, 'steps': 5}, {'issue': 16, 'done': 0, 'steps': 0}], fields['active'])
        self.assertEqual([{'issue': 14, 'n': 4, 'text': 'Rerun | the files'}], fields['next'])
        self.assertEqual([{'day': '2026-04-10', 'plain': 'keep strict quoting',
                           'address': 'https://github.com/octo-org/octo-repo/issues/2#issuecomment-1'}], fields['decisions'])
        self.assertIn('| Step 4: Rerun / the files | not stated | #14 |', worker.code_text(fields)[2])   # a pipe cannot break the table

    def test_both_calls_are_middle_calls_of_the_status_job_and_at_most_max_summaries_blocks_a_day(self):
        with patch.object(worker, 'summary_material', return_value=dict(MATERIAL, fields=FIELDS)):
            self.assertEqual('posted', self.refresh())
            with store.Store(REPO, config=self.config) as db:
                rows = db.usage_rows(1)
            self.assertEqual({('middle', 'status')}, {(row['tier'], row['job']) for row in rows})
            self.assertEqual(2, len(rows))
            self.assertEqual('cap', self.refresh(max_summaries=1))
            self.assertEqual(2, self.used())   # no call for a refresh the day's cap stops
            self.assertEqual('posted', self.refresh(max_summaries=2))
        self.assertIn('**Active issues:** #14, 3 of 5 steps; #16', self.posted[0])


class RunOnceTests(SummaryBox):
    """The refresh happens in the worker's pass, only where the observer publishes (automatic mode, not paused)."""

    def setUp(self):
        super().setUp()
        self.path = Path(sources.project_directory(self.project))
        self.path.mkdir(parents=True)
        for d in (self.path, self.path.parent, self.path.parent.parent):
            d.chmod(0o700)
        for name, value in (('refresh_record', True),):
            patcher = patch.object(worker, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(worker, 'refresh_summary', wraps=worker.refresh_summary)
        self.wrapped = patcher.start()
        self.addCleanup(patcher.stop)

    def pass_(self, mode='automatic', paused=False, invoke=None):
        self.config['observer']['mode'] = mode
        runtime.approve(REPO, self.config)
        with store.Store(REPO, config=self.config) as db:
            db.set_control('publication_paused', paused)
        return worker.run_once(REPO, self.config, self.project, invoke or self.invoke())

    def test_the_worker_refreshes_a_stale_summary_in_automatic_mode(self):
        self.pass_()
        self.assertEqual(1, self.wrapped.call_count)
        self.assertEqual(1, len(self.posted))
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual('posted', db.get_control('summary_last')['code'])

    def test_a_refresh_that_failed_is_not_tried_again_by_the_next_passes_and_the_wait_is_not_renewed(self):
        rejecting = self.invoke(accepted=False, reason='unsupported')
        self.pass_(invoke=rejecting)
        with store.Store(REPO, config=self.config) as db:
            first = db.get_control('summary_last')
            self.assertEqual('summary-rejected', first['code'])
        self.pass_(invoke=rejecting)
        self.pass_(invoke=rejecting)
        self.assertEqual(['summary', 'summary_review'], [purpose for purpose, _ in self.calls])
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual(first, db.get_control('summary_last'))      # waiting leaves the time of the failure as it was

    def test_the_worker_posts_no_summary_in_shadow_mode_or_while_publication_is_paused(self):
        self.pass_('shadow')
        self.pass_('automatic', paused=True)
        self.assertEqual(0, self.wrapped.call_count)
        self.assertEqual([], self.posted)
        self.assertEqual([], self.calls)

    def test_a_current_summary_leaves_no_trace_and_costs_no_call(self):
        with patch.object(worker, 'summary_wanted', return_value=False):
            self.pass_()
        self.assertEqual([], self.calls)
        with store.Store(REPO, config=self.config) as db:
            self.assertIsNone(db.get_control('summary_last'))


if __name__ == '__main__':
    unittest.main()
