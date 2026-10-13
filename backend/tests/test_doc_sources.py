"""The four external-index contracts: fixed revisions, recovery, scope parsing and retrieval."""
import base64
import hashlib

from backend.doc_sources import FetchFailure
from backend.tests.test_docs import api, call, make  # noqa: F401
from clients import github_scope


class GitHubReplies:
    def __init__(self, files):
        self.calls, self.trees, self.blobs = [], {}, {}
        self.private, self.fail_blob, self.on_blob = False, '', None
        self.set_files('a' * 40, files)

    def set_files(self, commit, files):
        self.head = commit
        tree = []
        for path, text in files.items():
            raw = text.encode()
            sha = hashlib.sha1(raw).hexdigest()
            self.blobs[sha] = raw
            tree.append({'path': path, 'type': 'blob', 'mode': '100644', 'sha': sha, 'size': len(raw)})
        self.trees[commit] = tree

    def get(self, path, repository=False):
        self.calls.append(path)
        if not path:
            return {'private': self.private, 'default_branch': 'trunk'}
        if path.startswith('/commits/'):
            return {'sha': self.head, 'commit': {'tree': {'sha': self.head}}}
        if path.startswith('/git/trees/'):
            sha = path.split('/git/trees/')[1].split('?')[0]
            return {'tree': self.trees[sha], 'truncated': False}
        if path.startswith('/git/blobs/'):
            sha = path.rsplit('/', 1)[1]
            if sha == self.fail_blob:
                raise FetchFailure('rate_limited')
            if self.on_blob:
                callback, self.on_blob = self.on_blob, None
                callback()
            return {'encoding': 'base64', 'content': base64.b64encode(self.blobs[sha]).decode()}
        if path.startswith('/git/matching-refs/'):
            return [{'ref': 'refs/heads/feature/docs'}] if '/heads/' in path else []
        raise AssertionError(path)


def setup_source(api, files, url='https://github.com/acme/handbook'):
    source = call(api, 'POST', 'doc-sources', {'url': url})['source']
    replies = GitHubReplies(files)
    worker = api.app.state.doc_sources
    worker.transport = lambda repo: replies
    return source, worker, replies


def refresh(api, source, worker):
    call(api, 'POST', 'doc-sources/' + source['id'] + '/refresh', {})
    worker.tick()


def test_refresh_uses_one_fixed_commit(api):
    source, worker, github = setup_source(api, {'README.md': '# Handbook\nOld facts.\n',
                                               'docs/nested/retries.md': '# Retries\nBackoff is cobalt.\n'})
    github.on_blob = lambda: github.set_files('b' * 40, {'README.md': '# Handbook\nNew facts.\n'})
    worker.tick()
    files = call(api, 'GET', 'doc-sources/' + source['id'] + '/documents')['documents']
    assert len(files) == 2 and {f['commit'] for f in files} == {'a' * 40}
    doc = call(api, 'GET', 'docs/external/' + files[1]['id'])['doc']
    assert doc['commit'] == 'a' * 40
    refresh(api, source, worker)
    assert len(call(api, 'GET', 'doc-sources/' + source['id'] + '/documents')['documents']) == 1
    body_reads = sum('/git/blobs/' in path for path in github.calls)
    refresh(api, source, worker)
    assert sum('/git/blobs/' in path for path in github.calls) == body_reads


def test_failed_refresh_keeps_the_last_good_copy(api):
    source, worker, github = setup_source(api, {'docs/a.md': '# Policy\nCobalt refunds.\n'})
    worker.tick()
    old = call(api, 'GET', 'docs/search', params={'q': 'cobalt'})['results'][0]
    github.set_files('b' * 40, {'docs/a.md': '# Policy\nViolet refunds.\n', 'docs/z.md': 'Later file.'})
    github.fail_blob = github.trees[github.head][1]['sha']
    refresh(api, source, worker)
    assert call(api, 'GET', 'docs/search', params={'q': 'violet'})['results'] == []
    assert call(api, 'GET', 'docs/external/' + old['id'])['doc']['commit'] == 'a' * 40
    assert call(api, 'GET', 'doc-sources')['sources'][0]['status'] == 'failed'
    github.fail_blob = ''
    refresh(api, source, worker)
    assert call(api, 'GET', 'docs/search', params={'q': 'violet'})['results'][0]['commit'] == 'b' * 40
    github.private = True
    refresh(api, source, worker)
    assert call(api, 'GET', 'docs/search', params={'q': 'violet'})['results'] == []
    call(api, 'GET', 'docs/external/' + old['id'], expected=404)


def test_scope_parsing_preserves_slash_references():
    info = {'default_branch': 'trunk'}
    replies = GitHubReplies({})
    for url, expected in [
        ('https://github.com/acme/handbook.git/?utm_source=example', ('trunk', '')),
        ('https://github.com/acme/handbook/tree/feature/docs/docs/guide', ('feature/docs', 'docs/guide')),
        ('https://github.com/acme/handbook/blob/feature%2Fdocs/docs/a%20b.md', ('feature/docs', 'docs/a b.md')),
        ('https://github.com/acme/handbook/blob/' + 'a' * 40 + '/docs/a.md', ('a' * 40, 'docs/a.md')),
    ]:
        scope = github_scope.parse_url(url)
        assert github_scope.resolve(scope, info, replies.get) == expected
    try:
        github_scope.split_reference('feature/docs/a.md', ['feature', 'feature/docs'])
    except ValueError:
        pass
    else:
        raise AssertionError('Ambiguous references must be chosen explicitly')
    assert github_scope.split_reference('feature/docs/a.md', override='feature/docs') == ('feature/docs', 'a.md')


def test_search_finds_a_nested_passage_and_tools_read_it_as_untrusted(api):
    from backend.tests.test_mcp import call as tool
    from clients.docs_ask import parse_citations
    source, worker, github = setup_source(api, {'docs/nested/retries.md': '# Transport\n\n## Backoff\nUse cobalt jitter.\n'})
    make(api, 'Jitter overview', 'Jitter means varying retry timing.')
    make(api, 'Docs index', 'Use cobalt jitter.', path='_librarian/index.md')
    worker.tick()
    hits = call(api, 'GET', 'docs/search', params={'q': 'cobalt jitter', 'collection': 'all'})['results']
    hit = next(row for row in hits if row['type'] == 'external')
    assert hit['path'] == 'docs/nested/retries.md' and hit['section'] == 'Backoff'
    assert hit['trust'] == 'untrusted_external' and '?plain=1#L3-L4' in hit['citation_url']
    assert hits.index(hit) < next(i for i, row in enumerate(hits) if row.get('path') == '_librarian/index.md')
    from backend.tests.test_openapi_v2 import conforms
    from backend.openapi_v2 import generate
    schema = generate()
    assert conforms({'results': hits}, schema['components']['schemas']['DocsSearchResults'], schema) is None
    err, answer = tool(api, 'hub_context_show', {'id': hit['id'], 'revision': hit['commit']})
    assert not err and answer['doc']['trust'] == 'untrusted_external'
    assert 'cobalt jitter' in answer['doc']['content']
    fast = call(api, 'GET', 'context/search', params={'q': 'cobalt', 'source': 'docs'})['results'][0]
    assert fast['kind'] == 'external_document' and fast['trust'] == 'untrusted_external'
    assert parse_citations('[External · Transport — Backoff](' + hit['citation_url'] + ')')[0]['type'] == 'external'
    call(api, 'PATCH', 'doc-sources/' + source['id'], {'archived': True})
    assert not any(row['type'] == 'external' for row in call(api, 'GET', 'docs/search', params={'q': 'cobalt'})['results'])
    assert call(api, 'GET', 'docs/search', params={'q': 'cobalt', 'source_id': source['id'], 'collection': 'all'})['results'] == []
    call(api, 'GET', 'docs/external/' + hit['id'], params={'revision': hit['commit']}, expected=404)
