"""The 1.5.0 pass: one sort call labels a stretch, code checks every label and every candidate, and only then is a draft
and a review paid for. Nothing here reaches GitHub or a model; the fake calls are scripted."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/recortex/scripts'))
sys.path.insert(0, str(ROOT))
from tests import test_observer_worker as tw  # noqa: E402

import observer_runtime as runtime  # noqa: E402
import observer_store as store  # noqa: E402
import observer_worker as worker  # noqa: E402

REPO = tw.REPO
QUOTE = tw.QUOTE
RESULT = (ROOT / 'examples/octo-repo/result-speed-1.md').read_text()
STEPS = (ROOT / 'examples/octo-repo/chain/6-steps.md').read_text()
APPROVAL = 'Yes, do the two steps in that order.'
CHECKPOINT = '''<!-- ghim:event type=checkpoint at=2026-03-01T12:00:00Z -->
### Checkpoint: 2026-03-01 12:00 UTC - Quoting work

**In plain words, we paused here:** the quoted newlines work is half done

**State:** Steps 1 and 2 of the newlines task are reported done.

**Done since the last checkpoint:** Steps 1 and 2.

**In progress:** The benchmark files rerun.

**Next actions:** Rerun the benchmark files, owner maintainer

**Open questions:** None
'''
PLAN = 'My plan: first carry the quote state across physical lines, then rerun the conformance set.'
EXTRA = {'tasks': [{'issue': 8, 'title': 'Newlines in quoted fields',
                    'steps': [{'n': 2, 'text': 'Handle CRLF inside quotes'}, {'n': 3, 'text': 'Rerun the benchmark files'}]}],
         'todo': 0, 'parts': [{'id': 9302, 'kind': 'claim', 'plain': 'a rye cover keeps the topsoil wetter', 'state': 'backed'},
                              {'id': 9305, 'kind': 'figure', 'plain': 'show the longest dry spell', 'state': 'planned'}],
         'memories': []}


def source(role, text, via='', typed=None, choice=False, agent=None):
    """A resolved source as the worker holds it."""
    typed = (role == 'user' and not via and not choice) if typed is None else typed
    return {'role': role, 'text': text, 'via': via, 'direct_user': typed, 'dialog_user': choice,
            'subagent': False, 'sidechain': False}


def label(job, kind='none', sources=(1,), task=0, steps=(), parts=()):
    return {'job': job, 'kind': kind, 'sources': list(sources), 'task': task, 'steps': list(steps), 'parts': list(parts)}


def draft(kind, body, sources, issue=2, title='', topic=0, marks=(), links=(), excerpts=()):
    return {'kind': kind, 'issue': issue, 'task_title': title, 'topic': topic, 'body': body, 'sources': list(sources),
            'excerpts': list(excerpts), 'marks': [dict(m) for m in marks], 'links': [dict(l) for l in links]}


class PassBox(unittest.TestCase):
    """The worker tests' sandbox (the same fakes) without their tests."""


for _name in ('setUp', 'capture', 'candidate', 'sid_of', 'shown', 'number', 'sorting', 'drafted', 'invoke', 'run_worker',
              'state', 'age'):
    setattr(PassBox, _name, tw.WorkerTests.__dict__[_name])


class Scripted(PassBox):
    def scripted(self, labels, drafts, accepted=True, reason=None, ignored=()):
        """A model that answers the sort with `labels`, the draft with `drafts` and every review with `accepted`; the two
        may be lists or functions of the payload."""
        def call(payload, schema, purpose, config):
            self.calls.append((purpose, payload))
            if purpose == 'sort':
                found = labels(payload) if callable(labels) else labels
                return {'ok': True, 'structured': {'labels': found, 'ignored': list(ignored)}}
            if purpose == 'draft':
                return {'ok': True, 'structured': {'candidates': drafts(payload) if callable(drafts) else drafts}}
            return {'ok': True, 'structured': {'candidate_hash': payload['candidate_hash'], 'accepted': accepted,
                                               'reason': reason or ('supported' if accepted else 'unsupported')}}
        return call

    def purposes(self):
        return [purpose for purpose, _ in self.calls]

    def level(self, level):
        self.config['observer']['level'] = level
        runtime.approve(REPO, self.config)

    def events(self, report):
        with store.Store(REPO, config=self.config) as db:
            return [db.event(key) for key in report['ready']]

    def index(self, **changes):
        return patch.object(worker, 'index_record', return_value=dict(EXTRA, **changes))


# ---------------------------------------------------------------- the labels

class LabelRows(unittest.TestCase):
    """Each row of spec 4.3: a label that code refuses costs nothing further."""

    def setUp(self):
        self.by_n = {1: source('user', 'Remind me to rerun with the other seed.'),
                     2: source('assistant', 'I reran the parser tests: 12 of 12 pass.'),
                     3: source('user', 'Approved the plan in the plan dialog.', via='plan_dialog', choice=True),
                     4: source('assistant', 'My plan: three steps.', via='plan_dialog'),
                     5: source('user', 'Chose "strict" when asked "Which quoting?"', via='question_dialog', choice=True),
                     6: source('assistant', 'The agent in a subagent said so.')}
        self.by_n[6]['subagent'] = True
        self.aux = {'tasks': {8: {'title': 'Task', 'steps': {1: 'a', 2: 'b'}}}, 'todo': 0, 'parts': {1: 9302}, 'topics': {2}}

    def problem(self, **fields):
        return worker.label_problem(label(**fields), self.by_n, self.aux, plan_agreed=fields.pop('agreed', False))

    def test_a_label_that_stands(self):
        self.assertIsNone(worker.label_problem(label('memory', 'result', [2]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('to_do', sources=[1]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('plan_agreed', sources=[4, 3]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('step_done', sources=[2], task=8, steps=[2]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('paper_part', sources=[2], parts=[1]), self.by_n, self.aux))

    def test_an_ordinal_the_stretch_does_not_hold_drops_the_label(self):
        for cited in ([9], [1, 9], [0], [2, 2]):
            self.assertEqual('ordinal', worker.label_problem(label('memory', 'result', cited), self.by_n, self.aux), cited)

    def test_a_task_that_is_not_an_open_task_of_the_payload_drops_a_step_done(self):
        self.assertEqual('task', worker.label_problem(label('step_done', sources=[2], task=9, steps=[1]), self.by_n, self.aux))
        self.assertEqual('task', worker.label_problem(label('step_done', sources=[2], task=8, steps=[]), self.by_n, self.aux))

    def test_task_zero_is_allowed_only_beside_a_plan_agreed_label_of_the_same_output(self):
        zero = label('step_done', sources=[2], task=0, steps=[1])
        self.assertEqual('task', worker.label_problem(zero, self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(zero, self.by_n, self.aux, plan_agreed=True))
        kept, dropped = worker.check_labels([zero], self.by_n, self.aux, 'everything')
        self.assertEqual(([], {'task': 1}), (kept, dropped))
        kept, dropped = worker.check_labels([label('plan_agreed', sources=[4, 3]), zero], self.by_n, self.aux, 'everything')
        self.assertEqual((2, {}), (len(kept), dropped))
        # a plan_agreed label that does not stand lets nothing through
        kept, dropped = worker.check_labels([label('plan_agreed', sources=[4]), zero], self.by_n, self.aux, 'everything')
        self.assertEqual(([], {'plan_agreed': 1, 'task': 1}), (kept, dropped))

    def test_a_to_do_rests_on_a_prompt_the_user_typed(self):
        self.assertEqual('to_do', worker.label_problem(label('to_do', sources=[2]), self.by_n, self.aux))
        self.assertEqual('to_do', worker.label_problem(label('to_do', sources=[3]), self.by_n, self.aux))   # a dialog answer
        self.assertEqual('to_do', worker.label_problem(label('to_do', sources=[5]), self.by_n, self.aux))

    def test_a_plan_agreed_needs_the_user_side_and_the_agent_side(self):
        self.assertEqual('plan_agreed', worker.label_problem(label('plan_agreed', sources=[4]), self.by_n, self.aux))
        self.assertEqual('plan_agreed', worker.label_problem(label('plan_agreed', sources=[3]), self.by_n, self.aux))
        self.assertEqual('plan_agreed', worker.label_problem(label('plan_agreed', sources=[1, 6]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('plan_agreed', sources=[2, 1]), self.by_n, self.aux))   # typed approval

    def test_a_step_done_rests_on_a_message_of_the_main_sessions_agent(self):
        base = dict(task=8, steps=[1])
        for cited in ([1], [6], [4], [3]):   # a prompt, a subagent, a plan shown in a dialog, a dialog answer
            self.assertEqual('step_done', worker.label_problem(label('step_done', sources=cited, **base), self.by_n,
                                                               self.aux), cited)

    def test_a_decision_rests_on_a_typed_prompt_or_a_question_dialog_choice(self):
        self.assertEqual('decision', worker.label_problem(label('memory', 'decision', [2]), self.by_n, self.aux))
        self.assertEqual('decision', worker.label_problem(label('memory', 'decision', [3]), self.by_n, self.aux))   # a plan answer
        self.assertIsNone(worker.label_problem(label('memory', 'decision', [1]), self.by_n, self.aux))
        self.assertIsNone(worker.label_problem(label('memory', 'decision', [5]), self.by_n, self.aux))

    def test_a_paper_part_must_be_a_part_the_payload_holds(self):
        self.assertEqual('paper_part', worker.label_problem(label('paper_part', sources=[2], parts=[2]), self.by_n, self.aux))
        self.assertEqual('paper_part', worker.label_problem(label('paper_part', sources=[2], parts=[]), self.by_n, self.aux))
        self.aux['parts'] = {}   # no plan
        self.assertEqual('paper_part', worker.label_problem(label('paper_part', sources=[2], parts=[1]), self.by_n, self.aux))

    def test_a_memory_label_names_its_kind(self):
        self.assertEqual('kind', worker.label_problem(label('memory', 'none', [2]), self.by_n, self.aux))

    def test_the_level_decides_which_jobs_are_drafted(self):
        labels = [label('memory', 'result', [2]), label('to_do', sources=[1]), label('paper_part', sources=[2], parts=[1]),
                  label('step_done', sources=[2], task=8, steps=[1])]
        kept, dropped = worker.check_labels(labels, self.by_n, self.aux, 'notes')
        self.assertEqual((['memory'], {'level': 3}), ([k['job'] for k in kept], dropped))
        for level in ('assistant', 'everything'):
            kept, dropped = worker.check_labels(labels, self.by_n, self.aux, level)
            self.assertEqual((4, {}), (len(kept), dropped))


class SourceAdmission(unittest.TestCase):
    def test_a_dialog_answer_is_said_in_the_conversation_and_gives_no_status_authority(self):
        choice = source('user', 'Chose "strict" when asked "Which quoting?"', via='question_dialog', choice=True)
        agent = source('assistant', 'I reran the tests: 12 of 12 pass.')
        self.assertTrue(worker._said(choice))
        self.assertTrue(worker._said(agent))
        self.assertFalse(worker._said(source('user', 'x', typed=False)))
        said = 'No, the earlier note was wrong: strict quoting stays the default.'
        correction = {'kind': 'correction', 'body': tw.CORRECTION.replace(
            '**Why:** the earlier note misread the session', '**Why:** the user said so:\n\n> ' + said)}
        self.assertEqual('', worker.status_authority(dict(correction, sources=['c']), {'c': choice}))
        self.assertEqual('user', worker.status_authority(dict(correction, sources=['u']), {'u': source('user', said)}))
        # beside the agent's own message it is the agent's message that backs it, never the choice
        self.assertEqual('agent', worker.status_authority(dict(correction, sources=['c', 'a']), {'c': choice, 'a': agent}))

    def test_a_plan_the_agent_showed_is_not_what_the_agent_reported_from_its_own_work(self):
        plan = source('assistant', 'My plan: three steps.', via='plan_dialog')
        self.assertTrue(worker._main_agent(plan))
        self.assertFalse(worker._plain_agent(plan))
        self.assertTrue(worker._plain_agent(source('assistant', 'I ran it.')))


# ---------------------------------------------------------------- the pass

class SortPass(Scripted):
    def test_an_empty_sort_spends_no_middle_call(self):
        sid = self.capture('Hello there, how are you today?')
        report = self.run_worker(self.scripted([], [], ignored=[1]))
        self.assertEqual(['sort'], self.purposes())
        self.assertEqual('ok', report['code'])
        self.assertEqual(('ignored', ''), self.state(sid))
        with store.Store(REPO, config=self.config) as db:
            used = db.usage_today()
        self.assertEqual((1, 0), (used['sort']['calls'], used['middle']['calls']))

    def test_the_sources_an_empty_sort_does_not_set_aside_wait_as_uncertain(self):
        said = self.capture('Hello there, how are you today?')
        kept = self.capture('Tell me about the build later.')
        self.run_worker(self.scripted([], [], ignored=[1]))
        self.assertEqual(('ignored', ''), self.state(said))
        self.assertEqual(('deferred', 'uncertain'), self.state(kept))

    def test_a_label_outside_the_level_spends_no_middle_call(self):
        self.level('notes')
        sid = self.capture('Remind me to rerun with the other seed.')
        report = self.run_worker(self.scripted([label('to_do', sources=[1])], []))
        self.assertEqual(['sort'], self.purposes())
        self.assertEqual({'level': 1}, report['dropped'])
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual({'level': 1}, db.get_control('worker_last')['dropped'])
        self.assertEqual(('deferred', 'uncertain'), self.state(sid))

    def test_an_ordinal_out_of_range_drops_that_label_and_nothing_else(self):
        first = self.capture(QUOTE)
        second = self.capture('The parser tests pass: 12 of 12 files.', role='assistant')
        labels = [label('memory', 'decision', [9]), label('memory', 'decision', [1])]
        report = self.run_worker(self.scripted(labels, lambda payload: [draft('decision', tw.BODY, [1], excerpts=[QUOTE])]))
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        self.assertEqual({'ordinal': 1}, report['dropped'])
        self.assertEqual([label('memory', 'decision', [1])], dict(self.calls)['draft']['labels'])
        self.assertEqual(1, len(report['ready']))
        self.assertEqual([first], self.events(report)[0]['candidate']['sources'])
        self.assertEqual(('deferred', 'uncertain'), self.state(second))

    def test_no_model_is_shown_a_64_character_id(self):
        self.capture(QUOTE)
        self.capture('I reran the parser tests: 12 of 12 pass.', role='assistant')
        report = self.run_worker(self.scripted([label('memory', 'decision', [1])],
                                               lambda payload: [draft('decision', tw.BODY, [1], excerpts=[QUOTE])]))
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        for purpose, payload in self.calls:
            shown = dict(payload, candidate_hash='')
            self.assertNotRegex(json.dumps(shown), '[0-9a-f]{64}', purpose)
            self.assertEqual([1, 2], [view['n'] for view in payload['sources']] if purpose != 'review'
                             else [view['n'] for view in payload['sources']] + [2])
        self.assertEqual(1, len(report['ready']))

    def test_the_sorter_sees_the_record_the_labels_are_checked_against(self):
        self.capture(QUOTE)
        with self.index():
            self.run_worker(self.scripted([], [], ignored=[1]))
        record = self.calls[0][1]['record']
        self.assertEqual([{'issue': 2, 'title': 'Parser'}], record['topics'])
        self.assertEqual(EXTRA['tasks'], record['tasks'])
        self.assertEqual([{'n': 1, 'kind': 'claim', 'plain': 'a rye cover keeps the topsoil wetter', 'state': 'backed'},
                          {'n': 2, 'kind': 'figure', 'plain': 'show the longest dry spell', 'state': 'planned'}],
                         record['plan'])
        self.assertEqual(0, record['todo'])

    def test_a_failed_sort_is_retried_in_halves_and_stops_after_three_attempts(self):
        poison = self.capture('POISON message the model cannot answer for.', role='assistant')
        calls = []

        def call(payload, schema, purpose, config):
            ids = self.shown(payload)
            calls.append(ids)
            if poison in ids:
                return {'ok': False, 'code': 'invalid-output', 'structured': None}
            return {'ok': True, 'structured': {'labels': [], 'ignored': [v['n'] for v in payload['sources']]}}
        for n in range(8):
            self.capture('Ordinary note %d.' % n, role='assistant')
        for _ in range(8):
            self.run_worker(call)
            self.age(301)
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual(('deferred', 'sort_failed_3'), (db.observation(poison)['state'], db.observation(poison)['reason']))
            states = {db.observation(sid)['state'] for sid in self.texts if sid != poison}
        self.assertEqual({'ignored'}, states)
        self.assertEqual(2, sum(ids == [poison] for ids in calls))

    def test_a_transient_failure_keeps_the_batch_whole(self):
        sids = [self.capture('Note %d.' % n) for n in range(4)]
        report = self.run_worker(lambda *args: {'ok': False, 'code': 'timeout', 'structured': None})
        self.assertEqual('sort-failed', report['code'])
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual([], db.get_control('retry_halves', []))
            self.assertEqual({'sort_failed'}, {db.observation(sid)['reason'] for sid in sids})

    def test_a_sort_tier_whose_proof_is_not_valid_stops_the_pass(self):
        self.capture(QUOTE)
        real = runtime.gate_valid
        with patch.object(runtime, 'gate_valid', side_effect=lambda gate, config=None, tier='middle': (
                False if tier == 'sort' else real(gate, config, tier))):
            report = self.run_worker(self.scripted([], []))
        self.assertEqual(('runtime-gate', []), (report['code'], self.calls))

    def test_a_strong_tier_whose_proof_is_not_valid_stops_only_the_keeper(self):
        self.config['observer']['strong_model'] = 'opus'
        runtime.approve(REPO, self.config)
        self.capture(QUOTE)
        self.capture('I reran the parser tests: 12 of 12 pass.', role='assistant')

        def gate(gate_, config=None, tier='middle'):
            return tier != 'strong'
        with patch.object(runtime, 'gate_valid', side_effect=gate):
            report = self.run_worker(self.scripted([label('memory', 'decision', [1])],
                                                   lambda payload: [draft('decision', tw.BODY, [1], excerpts=[QUOTE])]))
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        self.assertEqual(1, len(report['ready']))

    def test_the_sort_tier_has_a_cap_and_a_budget_of_its_own(self):
        self.capture(QUOTE)
        with store.Store(REPO, config=self.config) as db:
            while db.reserve_invocation('sort') is not None:
                pass
        report = self.run_worker(self.scripted([], []))
        self.assertEqual(('budget', []), (report['code'], self.calls))

    def test_the_labels_of_a_stretch_whose_draft_waited_are_not_sorted_twice(self):
        sid = self.capture(QUOTE)
        self.capture('I reran the parser tests: 12 of 12 pass.', role='assistant')
        found = self.scripted([label('memory', 'decision', [1])],
                              lambda payload: [draft('decision', tw.BODY, [1], excerpts=[QUOTE])])
        with store.Store(REPO, config=self.config) as db:   # the middle tier is full
            while db.reserve_invocation('draft') is not None:
                pass
        self.assertEqual('budget', self.run_worker(found)['code'])
        self.assertEqual(['sort'], self.purposes())
        with store.Store(REPO, config=self.config) as db:
            db.db.execute("DELETE FROM invocations WHERE tier='middle'")
        self.age(301)
        self.calls = []
        report = self.run_worker(found)
        self.assertEqual(['draft', 'review'], self.purposes())   # the same stretch: no second sort
        self.assertEqual(1, len(report['ready']))
        self.assertEqual(('ready', ''), self.state(sid))


# ---------------------------------------------------------------- the candidates

class Refusals(Scripted):
    """Each row of 4.5: a candidate that code refuses never reaches a reviewer. The pass-level tests run the whole pass;
    the others hand a stored candidate to candidate_problem with the sources it cites."""

    CONTEXT = {'targets': [{'issue': 2}], 'memories': []}

    def agreed(self, sids):
        """A new task the user agreed to by typing, as one pass finds it."""
        return draft('steps', STEPS, sids, issue=0, title='Quoted newlines in two steps', topic=2)

    def staged(self, drafts, labels, **kw):
        self.capture(PLAN, role='assistant')
        self.capture(APPROVAL)
        self.capture('I carried the quote state across lines: step 1 is done.', role='assistant')
        with self.index():
            return self.run_worker(self.scripted(labels, drafts, **kw))

    def assertRefused(self, reason, drafts, labels):
        report = self.staged(drafts, labels)
        self.assertEqual(['sort', 'draft'], self.purposes(), reason)
        self.assertEqual([], report['ready'], reason)
        reasons = []
        with store.Store(REPO, config=self.config) as db:
            for sid in self.texts:
                item = db.observation(sid)
                if item['state'] == 'deferred':
                    reasons.append(item['reason'])
        self.assertTrue(any(r.startswith(reason.replace('-', '_')) for r in reasons), (reason, reasons))

    def problem(self, kind, body, cited, marks=(), links=(), issue=2, title=None, topic=None, **plan):
        """candidate_problem of a stored candidate citing `cited` ({id: source})."""
        candidate = {'kind': kind, 'issue': issue, 'body': body, 'sources': list(cited), 'excerpts': [],
                     'marks': [dict(m) for m in marks], 'links': [dict(l) for l in links]}
        if title is not None:
            candidate.update(task_title=title, topic=topic)
        return worker.candidate_problem(candidate, cited, self.CONTEXT, {'repo': REPO}, REPO, plan or None)

    def test_a_new_task_needs_the_plan_and_the_approval_and_is_queued_with_its_title_and_topic(self):
        report = self.staged([self.agreed([1, 2])], [label('plan_agreed', sources=[1, 2])])
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        event = self.events(report)[0]['candidate']
        self.assertEqual((0, 'Quoted newlines in two steps', 2, 'steps'),
                         (event['issue'], event['task_title'], event['topic'], event['kind']))
        self.assertEqual(([], []), (event['marks'], event['links']))

    def title_case(self, title, reason):
        """A new task whose title is `title` is refused before any review, and nothing is queued."""
        self.assertRefused(reason, [self.agreed_titled([1, 2], title)], [label('plan_agreed', sources=[1, 2])])
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual([], db.events())

    def agreed_titled(self, sids, title):
        return draft('steps', STEPS, sids, issue=0, title=title, topic=2)

    def test_title_a_secret_in_the_title_of_a_new_task(self):
        self.title_case('Rotate ghp_' + 'A' * 36, 'privacy')

    def test_title_a_restricted_pattern_in_the_title_of_a_new_task(self):
        self.config['restricted_patterns'] = [{'name': 'site code', 'pattern': '\\bST-[0-9]{3}\\b', 'example': 'ST-204'}]
        self.title_case('Clean up site ST-204', 'privacy')

    def test_title_a_local_path_in_the_title_of_a_new_task(self):
        self.title_case('Fix the reader in /home/alice/project', 'title')

    def test_title_a_banned_word_in_the_title_of_a_new_task(self):
        self.config['banned_words'] = ['frobnicate']
        self.title_case('Frobnicate the reader', 'title')

    def test_title_a_control_or_bidi_character_in_the_title_of_a_new_task(self):
        self.title_case('Fix the reader\u202e gnp.exe', 'title')

    def test_title_a_line_break_in_the_title_of_a_new_task(self):
        self.title_case('Fix the reader\nthen the writer', 'title')

    def test_title_an_over_long_title_of_a_new_task(self):
        # The drafter's schema caps the title, so such a draft fails whole: it costs a draft call, queues nothing and
        # reaches no reviewer. The check in code holds for a candidate that got past it some other way.
        report = self.staged([self.agreed_titled([1, 2], 'x' * (worker.TITLE_MAX + 1))],
                             [label('plan_agreed', sources=[1, 2])])
        self.assertEqual((['sort', 'draft'], 'draft-failed', []), (self.purposes(), report['code'], report['ready']))
        self.assertEqual('title', worker.title_problem('x' * (worker.TITLE_MAX + 1), {'repo': REPO}, REPO))
        self.assertEqual('title', worker.title_problem('', {'repo': REPO}, REPO))
        self.assertEqual('title', worker.title_problem('   ', {'repo': REPO}, REPO))
        self.assertIsNone(worker.title_problem('x' * worker.TITLE_MAX, {'repo': REPO}, REPO))

    def test_title_markup_in_the_title_of_a_new_task(self):
        self.title_case('Fix <b>the</b> reader', 'title')

    def test_title_a_mention_in_the_title_of_a_new_task(self):
        self.title_case('Thanks @hubot for the reader', 'title')

    def test_title_an_outside_address_in_the_title_of_a_new_task(self):
        self.title_case('See https://collector.example.test/x', 'title')

    def test_title_a_reference_in_the_title_of_a_new_task(self):
        self.title_case('Redo the work of #12', 'title')

    def test_title_a_backtick_in_the_title_of_a_new_task(self):
        self.title_case('Use `code` here', 'title')

    def test_title_a_plain_title_of_the_longest_length_stands(self):
        report = self.staged([self.agreed_titled([1, 2], 'x' * worker.TITLE_MAX)], [label('plan_agreed', sources=[1, 2])])
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        self.assertEqual(1, len(report['ready']))

    def test_title_the_to_do_task_a_first_item_opens_has_a_fixed_title(self):
        self.assertIsNone(worker.title_problem('To do', {'repo': REPO}, REPO))

    def test_steps_authority_a_new_task_that_cites_no_approval(self):
        self.assertRefused('steps-authority', [self.agreed([1])], [label('plan_agreed', sources=[1, 2])])

    def test_steps_authority_a_new_task_that_cites_no_plan(self):
        self.assertRefused('steps-authority', [self.agreed([2])], [label('plan_agreed', sources=[1, 2])])

    def test_steps_authority_and_form_by_the_sources_the_candidate_cites(self):
        plan, typed = source('assistant', PLAN), source('user', APPROVAL)
        shown = source('assistant', PLAN, via='plan_dialog')
        approved = source('user', 'Approved the plan in the plan dialog.', via='plan_dialog', choice=True)
        rejected = source('user', 'Rejected the plan in the plan dialog.', via='plan_dialog', choice=True)
        chosen = source('user', 'Chose "yes" when asked "Go on?"', via='question_dialog', choice=True)
        dialog = STEPS.replace('By the project lead in the chat, 2026-03-12 11:00 PST:\n\n> Yes, do the two steps in that order.',
                               'Approved in the plan dialog, 2026-03-12 11:00 PST.')
        task = dict(issue=0, title='T', topic=2, role='task')

        def check(body, **cited):
            return self.problem('steps', body, {k * 64: v for k, v in cited.items()}, **task)
        self.assertIsNone(check(STEPS, a=plan, b=typed))                     # the plan, and the words typed
        self.assertIsNone(check(dialog, a=shown, b=approved))                # the plan dialog, approved
        self.assertIsNone(check(STEPS, a=plan, b=chosen, c=typed))           # chat form, a choice beside typed words
        self.assertEqual('steps-authority', check(STEPS, b=typed))           # no plan
        self.assertEqual('steps-authority', check(STEPS, a=plan))            # no approval
        self.assertEqual('steps-authority', check(dialog, a=shown, b=rejected))   # a rejected plan is no approval
        # the form must agree with what is cited
        self.assertEqual('steps-authority', check(dialog, a=plan, b=typed))  # typed words approve only what the memory quotes
        self.assertEqual('steps-form', check(dialog, a=shown, b=chosen))     # a question choice is not the plan dialog
        quoted = STEPS.replace('Yes, do the two steps in that order.', 'Yes, do all of it at once.')
        self.assertEqual('steps-form', check(quoted, a=plan, b=chosen))      # chat form whose quote nobody typed
        self.assertEqual('steps-form', check(STEPS.replace('**Agreed:**', '**Agreed:** Maybe.\n'), a=plan, b=typed))

    def test_steps_form_more_than_twenty_steps_and_a_step_longer_than_one_line(self):
        plan, typed = source('assistant', PLAN), source('user', APPROVAL)
        cited = {'a' * 64: plan, 'b' * 64: typed}
        many = STEPS.replace('2. Rerun the conformance set', '\n'.join('%d. Step number %d' % (n, n) for n in range(2, 23)))
        long = STEPS.replace('2. Rerun the conformance set', '2. Rerun the conformance set\n   and then the whole benchmark')
        for body in (many, long):
            self.assertEqual('steps-form', self.problem('steps', body, cited, issue=0, title='T', topic=2, role='task'))
        twenty = STEPS.replace('2. Rerun the conformance set', '\n'.join('%d. Step number %d' % (n, n) for n in range(2, 21)))
        self.assertIsNone(self.problem('steps', twenty, cited, issue=0, title='T', topic=2, role='task'))

    def test_todo_authority_quoted_words_that_are_not_in_a_typed_prompt(self):
        one = STEPS.replace('1. Carry the quote state across physical lines\n2. Rerun the conformance set',
                            '1. Rerun with the other seed')
        said = source('user', 'Remind me to rerun with the other seed. Thanks.')
        quoted = one.replace('Yes, do the two steps in that order.', 'Remind me to rerun with the other seed.')
        other = one.replace('Yes, do the two steps in that order.', 'Words nobody typed.')
        agent = source('assistant', 'Remind me to rerun with the other seed.')
        kwargs = dict(issue=0, role='todo')
        self.assertIsNone(self.problem('steps', quoted, {'a' * 64: said}, **kwargs))
        self.assertEqual('todo-authority', self.problem('steps', other, {'a' * 64: said}, **kwargs))
        self.assertEqual('todo-authority', self.problem('steps', quoted, {'a' * 64: agent}, **kwargs))   # not typed
        choice = source('user', 'Remind me to rerun with the other seed.', via='question_dialog', choice=True)
        self.assertEqual('todo-authority', self.problem('steps', quoted, {'a' * 64: choice}, **kwargs))

    def test_todo_authority_through_the_pass(self):
        self.capture('Remind me to rerun with the other seed. Thanks.')
        one = STEPS.replace('1. Carry the quote state across physical lines\n2. Rerun the conformance set',
                            '1. Rerun with the other seed')
        words = one.replace('Yes, do the two steps in that order.', 'Words nobody typed.')
        with self.index():
            report = self.run_worker(self.scripted([label('to_do', sources=[1])], [draft('steps', words, [1], issue=0)]))
        self.assertEqual((['sort', 'draft'], []), (self.purposes(), report['ready']))

    def test_mark_authority_marks_that_cite_no_message_of_the_agent(self):
        self.assertRefused('mark-authority',
                           [draft('correction', tw.CORRECTION, [2], marks=[{'task': 8, 'steps': [2]}])],
                           [label('memory', 'correction', [2])])
        agent, typed = source('assistant', 'I reran it.'), source('user', 'Yes.')
        marks = [{'task': 8, 'steps': [2]}]
        tasks = {8: {'title': 'T', 'steps': {2: 'x'}}}
        self.assertEqual('mark-authority', self.problem('correction', tw.CORRECTION, {'a' * 64: typed}, marks, tasks=tasks))
        self.assertIsNone(self.problem('correction', tw.CORRECTION, {'a' * 64: typed, 'b' * 64: agent}, marks, tasks=tasks))
        shown = source('assistant', 'My plan', via='plan_dialog')   # a plan the agent showed is not a report of work
        self.assertEqual('mark-authority', self.problem('correction', tw.CORRECTION, {'a' * 64: shown}, marks, tasks=tasks))

    def mark_case(self, marks):
        self.assertRefused('mark-binding', [draft('result', RESULT, [3], marks=marks)], [label('memory', 'result', [3])])

    def test_mark_binding_a_task_that_is_not_open(self):
        self.mark_case([{'task': 9, 'steps': [2]}])

    def test_mark_binding_a_step_that_is_not_an_open_step_of_the_task(self):
        self.mark_case([{'task': 8, 'steps': [1]}])

    def test_mark_binding_one_unknown_step_among_known_ones(self):
        self.mark_case([{'task': 8, 'steps': [2, 7]}])

    def test_mark_binding_task_zero_with_no_steps_candidate_in_the_pass(self):
        self.mark_case([{'task': 0, 'steps': [1]}])

    def test_mark_binding_a_checkpoint_that_marks_two_tasks(self):
        agent = source('assistant', 'Steps are done.')
        marks = [{'task': 8, 'steps': [2]}, {'task': 9, 'steps': [1]}]
        checkpoint = CHECKPOINT
        self.assertEqual('mark-binding', self.problem('checkpoint', checkpoint, {'a' * 64: agent}, marks, issue=8,
                                                      tasks={8: {'title': 'T', 'steps': {2: 'x'}}}))

    def test_mark_kind_marks_on_a_kind_that_cannot_carry_them(self):
        idea = (ROOT / 'examples/octo-repo/chain/1-idea.md').read_text()
        agent = source('assistant', 'I have an idea. Step 2 is done.')
        tasks = {8: {'title': 'T', 'steps': {2: 'x'}}}
        self.assertEqual('mark-kind', self.problem('idea', idea, {'a' * 64: agent}, [{'task': 8, 'steps': [2]}], tasks=tasks))

    def test_part_binding_a_part_that_is_not_standing_against_on_an_idea_and_any_link_on_a_checkpoint(self):
        agent = source('assistant', 'I ran it. The result is in.')
        idea = (ROOT / 'examples/octo-repo/chain/1-idea.md').read_text()
        checkpoint = CHECKPOINT
        cited = {'a' * 64: agent}
        self.assertEqual('part-binding', self.problem('result', RESULT, cited, links=[{'key': 'checks', 'part': 7}], parts={9302}))
        self.assertEqual('part-binding', self.problem('idea', idea, cited, links=[{'key': 'against', 'part': 9302}], parts={9302}))
        self.assertEqual('part-binding', self.problem('checkpoint', checkpoint, cited, links=[{'key': 'checks', 'part': 9302}],
                                                      parts={9302}))
        self.assertIsNone(self.problem('result', RESULT, cited, links=[{'key': 'against', 'part': 9302}], parts={9302}))

    def test_part_binding_through_the_pass(self):
        self.capture('I ran the speed benchmark: median 1.31 s over 5 runs.', role='assistant')
        with self.index():
            report = self.run_worker(self.scripted([label('memory', 'result', [1])],
                                                   [draft('result', RESULT, [1], links=[{'key': 'checks', 'part': 7}])]))
        self.assertEqual((['sort', 'draft'], []), (self.purposes(), report['ready']))
        with store.Store(REPO, config=self.config) as db:
            self.assertEqual('part_binding', db.observation(next(iter(self.texts)))['reason'])

    def test_level_a_candidate_whose_job_is_outside_the_level_is_refused_even_if_a_label_got_through(self):
        plan, typed = source('assistant', PLAN), source('user', APPROVAL)
        cited = {'a' * 64: plan, 'b' * 64: typed}
        self.assertEqual('level', self.problem('steps', STEPS, cited, issue=0, title='T', topic=2, level='notes'))
        agent = source('assistant', 'I ran it.')
        self.assertEqual('level', self.problem('result', RESULT, {'a' * 64: agent}, [{'task': 8, 'steps': [2]}], level='notes'))
        self.assertEqual('level', self.problem('result', RESULT, {'a' * 64: agent}, links=[{'key': 'checks', 'part': 9302}],
                                               level='notes'))


class Queued(Scripted):
    """What a passing candidate looks like when it is queued, and where code puts it."""

    def test_a_result_that_reports_a_step_carries_its_mark_in_the_queue(self):
        self.capture('I ran the speed benchmark: median 1.31 s over 5 runs. Step 2 is done.', role='assistant')
        marks = [{'task': 8, 'steps': [3, 2, 3]}]
        with self.index():
            report = self.run_worker(self.scripted(
                [label('memory', 'result', [1]), label('step_done', sources=[1], task=8, steps=[2])],
                [draft('result', RESULT, [1], issue=2, marks=marks)]))
        self.assertEqual(['sort', 'draft', 'review'], self.purposes())
        candidate = self.events(report)[0]['candidate']
        self.assertEqual([{'task': 8, 'steps': [2, 3]}], candidate['marks'])
        self.assertEqual((2, 'result'), (candidate['issue'], candidate['kind']))
        self.assertNotIn('task_title', candidate)
        self.assertNotIn('topic', candidate)

    def test_a_checkpoint_that_marks_steps_is_posted_on_the_task_it_marks(self):
        self.capture('Step 2 is done: CRLF inside quotes now round-trips.', role='assistant')
        with self.index():
            report = self.run_worker(self.scripted(
                [label('step_done', sources=[1], task=8, steps=[2])],
                [draft('checkpoint', CHECKPOINT, [1], issue=0, marks=[{'task': 8, 'steps': [2]}])]))   # the model says 0
        candidate = self.events(report)[0]['candidate']
        self.assertEqual((8, 'checkpoint', [{'task': 8, 'steps': [2]}]), (candidate['issue'], candidate['kind'], candidate['marks']))

    def test_a_plan_link_is_queued_as_the_comment_id_of_the_part(self):
        self.capture('I ran the speed benchmark: median 1.31 s over 5 runs.', role='assistant')
        with self.index():
            report = self.run_worker(self.scripted(
                [label('memory', 'result', [1]), label('paper_part', sources=[1], parts=[1])],
                [draft('result', RESULT, [1], links=[{'key': 'checks', 'part': 1}])]))
        self.assertEqual([{'key': 'checks', 'part': 9302}], self.events(report)[0]['candidate']['links'])
        review = dict(self.calls)['review']
        self.assertEqual([{'key': 'checks', 'part': 1}], review['candidate']['links'])   # the reviewer reads the number

    def test_an_against_link_carries_the_status_authority_the_cited_message_gives(self):
        self.capture('I ran the speed benchmark: median 1.31 s over 5 runs.', role='assistant')
        with self.index():
            report = self.run_worker(self.scripted(
                [label('memory', 'result', [1]), label('paper_part', sources=[1], parts=[1])],
                [draft('result', RESULT, [1], links=[{'key': 'against', 'part': 1}])]))
        self.assertEqual('agent', self.events(report)[0]['review']['status_authority'])

    def test_an_item_for_the_to_do_task_goes_on_it_and_carries_neither_title_nor_topic(self):
        self.capture('Remind me to rerun with the other seed.')
        one = STEPS.replace('Yes, do the two steps in that order.', 'Remind me to rerun with the other seed.').replace(
            '1. Carry the quote state across physical lines\n2. Rerun the conformance set', '1. Rerun with the other seed')
        with self.index(todo=14):
            report = self.run_worker(self.scripted([label('to_do', sources=[1])],
                                                   [draft('steps', one, [1], issue=0, title='ignored', topic=2)]))
        candidate = self.events(report)[0]['candidate']
        self.assertEqual((14, 'steps'), (candidate['issue'], candidate['kind']))
        self.assertNotIn('task_title', candidate)
        self.assertNotIn('topic', candidate)

    def test_with_no_to_do_task_the_first_item_opens_one(self):
        self.capture('Remind me to rerun with the other seed.')
        one = STEPS.replace('Yes, do the two steps in that order.', 'Remind me to rerun with the other seed.').replace(
            '1. Carry the quote state across physical lines\n2. Rerun the conformance set', '1. Rerun with the other seed')
        with self.index(todo=0):
            report = self.run_worker(self.scripted([label('to_do', sources=[1])], [draft('steps', one, [1], issue=7)]))
        candidate = self.events(report)[0]['candidate']
        self.assertEqual((0, 'To do', 0), (candidate['issue'], candidate['task_title'], candidate['topic']))

    def test_a_mark_of_task_zero_follows_its_steps_memory_and_names_it(self):
        agent = self.capture(PLAN, role='assistant')
        self.capture(APPROVAL)
        self.capture('I carried the quote state across lines. Step 1 is done: 12 of 12 files pass.', role='assistant')
        steps = draft('steps', STEPS, [1, 2], issue=0, title='Quoted newlines in two steps', topic=2)
        result = draft('result', RESULT, [3], issue=2, marks=[{'task': 0, 'steps': [1]}])
        labels = [label('plan_agreed', sources=[1, 2]), label('memory', 'result', [3]),
                  label('step_done', sources=[3], task=0, steps=[1])]
        with self.index():
            report = self.run_worker(self.scripted(labels, [result, steps]))   # the result is listed first
        self.assertEqual(2, len(report['ready']))
        events = self.events(report)
        by_kind = {event['candidate']['kind']: event['candidate'] for event in events}
        self.assertEqual(agent, by_kind['steps']['sources'][0])
        self.assertEqual(by_kind['steps']['sources'][0], by_kind['result']['with'])
        self.assertEqual([{'task': 0, 'steps': [1]}], by_kind['result']['marks'])

    def test_a_mark_of_task_zero_whose_steps_memory_was_not_queued_is_refused(self):
        self.capture(PLAN, role='assistant')
        self.capture('Fine.')
        self.capture('I carried the quote state across lines. Step 1 is done: 12 of 12 files pass.', role='assistant')
        weak = draft('steps', STEPS, [1, 2], issue=0, title='T', topic=2)
        result = draft('result', RESULT, [3], issue=2, marks=[{'task': 0, 'steps': [1]}])
        labels = [label('plan_agreed', sources=[1, 2]), label('memory', 'result', [3]),
                  label('step_done', sources=[3], task=0, steps=[1])]
        with self.index():
            report = self.run_worker(self.scripted(labels, [result, weak]))
        self.assertEqual([], report['ready'])   # "Fine." quotes nothing: no steps memory, so no mark
        with store.Store(REPO, config=self.config) as db:
            self.assertTrue(any(db.observation(sid)['reason'].startswith('mark_binding') for sid in self.texts
                                if db.observation(sid)['state'] == 'deferred'))


class Decisions(Scripted):
    def test_a_decision_may_rest_on_the_fixed_words_of_a_question_dialog_choice(self):
        words = 'Chose "strict" when asked "Which quoting?"'
        body = tw.BODY.replace('> For the parser, use strict quoting and keep the warning.', '> ' + words)
        candidate = {'kind': 'decision', 'issue': 2, 'body': body, 'sources': ['c' * 64], 'excerpts': [],
                     'marks': [], 'links': []}
        context = {'targets': [{'issue': 2}], 'memories': []}
        choice = {'c' * 64: source('user', words, via='question_dialog', choice=True)}
        self.assertIsNone(worker.candidate_problem(candidate, choice, context, {'repo': REPO}, REPO))
        plan_answer = {'c' * 64: source('user', 'Approved the plan in the plan dialog.', via='plan_dialog', choice=True)}
        self.assertEqual('decision-authority', worker.candidate_problem(candidate, plan_answer, context, {'repo': REPO}, REPO))


if __name__ == '__main__':
    unittest.main()
