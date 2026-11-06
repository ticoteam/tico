"""Exact text picks settle question tasks; a note that names no option records no pick."""
from backend import hubdb as H, question_tasks as QT
from backend.tests.test_api import api, get, headers, post, setup_attempt, legacy_send_approval  # noqa: F401

DRAFTS = [{'label': 'Blue', 'then': 'Keep the blue draft'}, {'label': 'Red', 'then': 'Keep the red draft'}]


def question(api, *, text='Which draft?', options=DRAFTS, attempt=None, **extra):
    attempt = attempt or setup_attempt(api)[2]
    out = post(api, 'task-questions', {'on': 'ana', 'question': text, 'options': options, **extra}, attempt['token'])
    return out['task']


def finish(api, card, note):
    """Ana closes the question task done with `note`."""
    post(api, f"tasks/{card['id']}", {'status': 'done', 'note': note, 'version': card['version']})
    with api.app.state.store.read() as c:
        row = H.task(c, card['id'])
        return row, QT.fields(row)['answer']


def test_exact_label_picks_the_option_and_closes_the_question(api):
    card = question(api)
    row, answer = finish(api, card, 'Blue')
    assert row['status'] == 'done' and not QT.is_open(row)
    assert answer['answers'] == {'decision': ['Blue']}
    assert answer['by'] == 'human:ana'


def test_nonmatching_note_records_no_pick(api):
    attempt = setup_attempt(api)[2]
    for text in ('Maybe Blue', 'Blue and Red'):
        card = question(api, attempt=attempt, text='Which draft? ' + text)
        row, answer = finish(api, card, text)
        assert answer['answers'] == {} and answer['other'] == text
        assert row['status'] == 'done'


def test_publish_label_settles_question_without_approving_a_send(api):
    card = question(api, text='Publish the draft?', options=[
        {'label': 'Publish', 'then': 'Select the prepared draft'}, {'label': 'Wait', 'then': 'Keep the draft'}])
    setup_attempt(api, bot='cpo')
    legacy_send_approval(api, {
        'to': 'colleague@acme.example', 'cc': [], 'subject': 'Synthetic draft',
        'body_sha256': 'a' * 64, 'mailbox': 'work'}, actor='bot:cpo')
    with api.app.state.store.read() as c:
        approvals = [tuple(r) for r in c.execute('SELECT * FROM approvals')]
    _, answer = finish(api, card, 'Publish')
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM approvals')] == approvals
    assert answer['answers'] == {'decision': ['Publish']}


def test_question_without_options_still_accepts_a_text_answer(api):
    card = question(api, text='Which month?', options=[])
    row, answer = finish(api, card, 'September')
    assert not QT.is_open(row)
    assert answer['other'] == 'September'
