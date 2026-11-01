"""Exact text picks settle structured cards; notes keep their options open."""
import pytest

from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401


def question(api, *, questions=None):
    _, _, attempt = setup_attempt(api)
    task = post(api, 'tasks', {'owner_id': 'bot:ops', 'title': 'Choose a draft', 'body': 'Synthetic draft'})
    ask = {'who': 'ana', 'questions': questions or [
        {'id': 'decision', 'header': '', 'question': 'Which draft?',
         'options': [{'label': 'Blue', 'then': 'Keep the blue draft'},
                     {'label': 'Red', 'then': 'Keep the red draft'}]}]}
    out = post(api, f"tasks/{task['id']}/comments", {'text': 'Which draft?', 'ask': ask}, attempt['token'])
    return task['id'], out['comment']['id']


def test_exact_label_matches_card_and_repeated_delivery_is_idempotent(api):
    tid, mid = question(api)
    out = post(api, f'messages/{mid}/answer', {'text': 'Blue'})
    assert out['answer']['answers'] == {'decision': ['Blue']}
    assert out['answer']['by'] == 'human:ana'
    before = get(api, f'tasks/{tid}/answers')['answers']
    assert len(before) == 1
    assert post(api, f'messages/{mid}/answer', {'text': 'Blue'})['answer'] == out['answer']
    post(api, f'tasks/{tid}/answers', {'target': {'comment': mid}, 'answers': {'decision': ['Blue']}})
    assert get(api, f'tasks/{tid}/answers')['answers'] == before
    assert get(api, f'tasks/{tid}')['task']['open_asks'] == 0
    with api.app.state.store.read() as c:
        assert H.answers_to(c, [mid])


@pytest.mark.parametrize('text', ['Maybe Blue', 'blue', 'Blue and Red'])
def test_nonmatching_reply_stays_as_note_and_options_remain_open(api, text):
    tid, mid = question(api)
    out = post(api, f'messages/{mid}/answer', {'text': text})
    assert out['decision_open'] and 'still open' in out['feedback']
    assert get(api, f'tasks/{tid}')['task']['open_asks'] == 1
    assert get(api, f'tasks/{tid}/answers')['answers'] == []
    comments = get(api, f'tasks/{tid}/comments')['comments']
    assert any(m['body'] == text and m['kind'] == 'say' for m in comments)
    with api.app.state.store.read() as c:
        assert not H.answers_to(c, [mid])
        assert H.message(c, mid)['refs']['questions'][0]['options'][0]['label'] == 'Blue'
    post(api, f'tasks/{tid}/answers', {'target': {'comment': mid}, 'answers': {'decision': ['Red']}})
    assert get(api, f'tasks/{tid}')['task']['open_asks'] == 0


def test_multiple_questions_are_not_guessed_and_other_answerer_is_refused(api):
    questions = [{'id': q, 'header': '', 'question': 'Which draft?',
                  'options': [{'label': 'Blue', 'then': 'Keep blue'}, {'label': 'Red', 'then': 'Keep red'}]}
                 for q in ('first', 'second')]
    tid, mid = question(api, questions=questions)
    assert post(api, f'messages/{mid}/answer', {'text': 'Blue'})['decision_open']
    post(api, f'messages/{mid}/answer', {'text': 'Blue'}, 'ben-test', expected=403)
    assert get(api, f'tasks/{tid}')['task']['open_asks'] == 1


def test_publish_label_settles_question_without_approving_a_send(api):
    tid, mid = question(api, questions=[
        {'id': 'decision', 'header': '', 'question': 'Publish the draft?',
         'options': [{'label': 'Publish', 'then': 'Select the prepared draft'},
                     {'label': 'Wait', 'then': 'Keep the draft'}]}])
    _, _, attempt = setup_attempt(api, bot='cpo')
    post(api, 'approvals', {'kind': 'send', 'payload': {
        'to': 'colleague@acme.example', 'cc': [], 'subject': 'Synthetic draft',
        'body_sha256': 'a' * 64, 'mailbox': 'work'}}, attempt['token'])
    with api.app.state.store.read() as c:
        approvals = [tuple(r) for r in c.execute('SELECT * FROM approvals')]
    post(api, f'messages/{mid}/answer', {'text': 'Publish'})
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM approvals')] == approvals
    assert get(api, f'tasks/{tid}/answers')['answers'][0]['answers'] == {'decision': ['Publish']}


def test_question_without_options_still_accepts_a_text_answer(api):
    tid, mid = question(api, questions=[{'id': 'detail', 'header': '', 'question': 'Which month?',
                                      'options': [], 'other': True}])
    reply = post(api, f'messages/{mid}/answer', {'text': 'September'})
    assert reply['kind'] == 'answer'
    assert not reply.get('decision_open')
    assert get(api, f'tasks/{tid}')['task']['open_asks'] == 0
