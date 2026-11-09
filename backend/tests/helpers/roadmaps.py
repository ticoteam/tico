"""Helpers that other tests share, moved out of test_roadmaps.py so they import no test file."""

from backend.tests.helpers.tasks_board import post


def public_setup(api, limit=10):
    post(api, 'tags', {'key': 'roadmap'})
    post(api, 'tags', {'key': 'internal'})
    post(
        api,
        'roadmaps/settings',
        {'labels': ['roadmap'], 'cors_origins': ['https://acme.example'], 'action_limit': limit},
    )
    return post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Internal title',
            'body': 'Internal summary',
            'type': 'Product roadmap',
            'labels': ['roadmap'],
            'visibility': 'public',
            'public_title': 'Roadmap item',
            'public_summary': 'What it does',
        },
    )


ORIGIN = {'Origin': 'https://acme.example'}
