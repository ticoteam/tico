"""How a written test receipt line is read (backend/test_receipts.py): which head a pass or failure belongs to, and
which earlier failures a line only mentions. The release checklist (scripts/release_checklist.py) reads them. No gate
counts one as passing checks any more: checks pass reads GitHub only (docs/pipelines-v3.md, D44)."""

import pytest

from backend import test_receipts

HEAD, OLD = 'a' * 40, 'b' * 40


@pytest.mark.parametrize(
    'text,status',
    [
        (f'Tests at head {HEAD}: 9 passed after fixing the earlier failure', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; fixed the failure at head {OLD}', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; the earlier error is now repaired', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; 2 failures now fixed', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; tests at head {OLD}: 2 failed', 'passed'),
        (f'Tests at head {HEAD}: 9 passed, failure at head {OLD}', 'passed'),
        (f'Tests: base {OLD}, head {HEAD}: 9 passed', 'passed'),
        (f'Tests at head {HEAD}: 2 failed after fixing the earlier failure', 'failed'),
        (f'Tests at head {HEAD}: 9 passed, 1 failed; fixed an earlier failure', 'failed'),
        (f'Tests at head {HEAD}: 9 passed; 1 failed', 'failed'),
        (f'Tests completed and head {HEAD}: 9 passed', 'passed'),
        (f'Tests at head {OLD}: 2 failed. Tests at head {HEAD}: 9 passed', 'failed'),
    ],
)
def test_historical_failure_phrases_are_not_current_run_results(text, status):
    receipt = test_receipts.recognise(text)
    assert receipt['status'] == status
    if receipt['head'] == OLD:
        assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] != 'failed'


def test_two_heads_in_one_line_keep_their_own_outcomes():
    text = f'Tests at head {HEAD}: 9 passed; tests at head {OLD}: 2 failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed; 2 failures were fixed', 'passed'),
        ('9 passed; 2 failing tests were fixed; 1 failed', 'failed'),
        ('9 passed; 2 failures were not fixed', 'failed'),
        ('9 passed; the earlier failure is recurring', 'failed'),
        ('9 passed; the previous error is still present', 'failed'),
        ('9 passed; 2 failures were fixed; the earlier failure is recurring', 'failed'),
    ],
)
def test_qa_repair_phrases_preserve_unresolved_and_recurring_failures(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed; 2 failures were fixed. Re-run: 2 failed', 'failed'),
        ('9 passed; 2 failures were fixed. Re-run: 9 passed, 0 failed', 'passed'),
        ('9 passed. Earlier run: 2 failures were fixed', 'passed'),
        ('9 passed; the earlier failure was fixed but is still present', 'failed'),
        ('9 passed; the previous errors have been repaired but still persistent', 'failed'),
        ('9 passed; the earlier failure was fixed but is no longer present', 'passed'),
        ('9 passed; the earlier failure has not recurred', 'passed'),
        ('9 passed; the earlier failure is no longer persistent', 'passed'),
        (f'9 passed. Latest run: 2 failed at head {OLD}', 'passed'),
        ('9 passed; 2 failures were fixed. Re-run failed: 2', 'failed'),
        ('9 passed; 2 failures were fixed but they are back', 'failed'),
        ('9 passed; 2 failures were fixed, but it is still present', 'failed'),
        ('9 passed; 2 failures were fixed. Re-run: 11 passed', 'passed'),
    ],
)
def test_qa_rerun_counts_and_recurrence_stay_with_their_head(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed. The 2 failures seen at the prior head have been fixed.', 'passed'),
        ('9 passed. 2 earlier failures fixed.', 'passed'),
        (f'9 passed. Re-run at head {HEAD}: 2 failed.', 'failed'),
        ('9 passed. The failure was fixed, but it is still present', 'failed'),
        # Only where-it-was-seen words may sit between a failure and its repair.
        ('9 passed. 2 failed tests need fixing', 'failed'),
        ('9 passed. 2 failures found on the last run are not fixed', 'failed'),
        ('9 passed. The 2 failures seen at the prior head have been fixed but are back.', 'failed'),
        (f'9 passed. Re-run at head {OLD}: 2 failed.', 'passed'),
        ('9 passed. 2 prior failures', 'failed'),
        # A repair word that only describes where the failure happened repairs nothing.
        ('9 passed, 2 failed on the commit fixing the parser.', 'failed'),
        ('9 passed, 2 errors from the resolved merge conflict.', 'failed'),
        ('9 passed, 2 failed in the repaired fixture.', 'failed'),
    ],
)
def test_repaired_prior_failures_and_headed_rerun_failures_after_1546(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'note,status',
    [
        # A repaired failure count is not a test word: these were no receipt before #1588.
        (f'Lint at head {HEAD}: passed after 2 errors fixed.', 'missing'),
        (f'Deploy at head {HEAD}: success; 2 prior errors resolved.', 'missing'),
        (f'Re-run at head {HEAD}: 9 passed; 2 earlier failures fixed.', 'missing'),
        # Dev 517 QA: failures counted in words, or on a continuation, still block.
        (f'Tests at head {HEAD}: 9 passed. Two failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed; 2 failures were fixed. Two failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. The failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Three tests failed.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Failing tests: 2', 'failed'),
        (f'Tests at head {HEAD}: 9 passed.\nfailing tests: 2', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Lint: 1 failure at prior head, now fixed.', 'passed'),
        (f'Tests at head {HEAD}: 9 passed. No failures remain.', 'passed'),
        (f'Tests at head {HEAD}: 9 passed.\nBase at {OLD}: 2 failed', 'passed'),
        # A test word inside a repair phrase is no test context, on the next line or the same one.
        (f'Lint at head {HEAD}: passed.\n2 failing tests were fixed.', 'missing'),
        (f'Lint at head {HEAD}: passed after fixing 2 test failures.', 'missing'),
        (f'Tests at head {HEAD}: 9 passed.\n2 failing tests were fixed.', 'passed'),
    ],
)
def test_repaired_counts_give_no_test_context_and_word_counts_block(note, status):
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


def test_headed_failure_count_needs_no_test_word():
    note = f'Re-run at head {HEAD}: 2 failed.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'failed'
    # A pass still needs a test word.
    assert test_receipts.at_head({'id': 't', 'note': f'Re-run at head {HEAD}: 9 passed.'}, HEAD)[0] == 'missing'
    # Another head's failure count gives this head's pass no test context:
    # only the failure at the other head is read.
    note = f'Re-run at head {HEAD}: 9 passed, 2 failed at head {OLD}.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'stale'


def test_dev505_original_note_does_not_claim_a_current_failure():
    # Exact opening sentence of the refused 03:37Z note, not the rewritten receipt.
    head = 'e06bfdf7ec6bdbd1772fb16102fee40715536e92'
    note = (
        'All lifecycle fixes remain built; a hosted-test fixture failure is now repaired and pushed '
        f'in PR #1542 at {head}. Review 1 remains blocked by the GitHub App sign-in refusal '
        '(original operation 46146711-bf35-46bd-8ff2-32dd8d8ab1bb); no retry/relink/bypass.'
    )
    assert test_receipts.at_head({'id': 't', 'note': note}, head)[0] != 'failed'


@pytest.mark.parametrize(
    'tail',
    [
        f'2 failed at head {HEAD}',
        f'head {HEAD}: 2 failed',
        '2 earlier failed',
        '12 previous test failures',
        '2 failed.',
        '2 failed. 1 error',
    ],
)
def test_failure_counts_keep_test_context_across_clauses_and_sentences(tail):
    separator = '. ' if tail.startswith('2 failed.') else '; '
    text = f'Tests at head {HEAD}: 9 passed{separator}{tail}'
    assert test_receipts.recognise(text)['status'] == 'failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'failed'


def test_head_bound_failure_inherits_test_context_without_poisoning_other_head():
    text = f'Tests at head {HEAD}: 9 passed; 2 failed at head {OLD}'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'
