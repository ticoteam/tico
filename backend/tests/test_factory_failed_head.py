"""The factory's own reading of a failed head: a QA check done with only comments fails the head it was filed for, so
the simulation neither counts a timed-out round there as cleared nor lets its builder take a later pass as approval."""
from evals.factory.world import World

HEAD = 'a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0'


class Board(World):
    """Only what failed_at reads: one ticket's checks, their steps and their review_head events."""
    def __init__(self, checks, filed):
        self._checks, self._filed = checks, filed

    def checks(self, task_id):
        return self._checks

    def check_step(self, check):
        return 'In QA'

    def sql(self, query, args=()):
        return [{'new': self._filed[args[0]]}] if 'review_head' in query and args[0] in self._filed else []


def test_a_check_done_with_only_comments_fails_the_head_it_was_filed_for():
    board = Board([{'id': 'qa1', 'status': 'done', 'verdict': None, 'verdict_head': None}], {'qa1': HEAD[:12] + ',x'})
    assert board.failed_at('t1', 'In QA', HEAD) is True
    assert board.failed_at('t1', 'In QA', 'f' * 40) is False


def test_a_pass_or_an_open_check_fails_nothing():
    board = Board([{'id': 'qa1', 'status': 'done', 'verdict': 'pass', 'verdict_head': HEAD},
                   {'id': 'qa2', 'status': 'doing', 'verdict': None, 'verdict_head': None}], {'qa2': HEAD})
    assert board.failed_at('t1', 'In QA', HEAD) is False
