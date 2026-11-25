import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useMemo, useState } from 'react';
import { ApiError } from '@/api/client';
import type { PageShown } from '@/api/schema';
import { liveSrc, LIVE_SANDBOX } from '@/conversation';
import { configQuery } from '@/data';
import { frameDoc, markChanges, parsePage, pageVersionQuery } from '@/features/task/routing';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Document } from '@/pagekinds';
import { FileGet } from './FileViewer';
import { Face, when } from './parts';
import { PageActionDialog, type PageAction } from './PageActionDialog';
import { fileUrl, fileVersionsQuery, libraryPageQuery, pageHref, type FileVersion, type PagesSearch } from './queries';

const sandbox = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';

export function LibraryPage({ id, search }: { id: string; search: PagesSearch }) {
  const router = useRouter();
  const { data, error } = useSuspenseQuery(libraryPageQuery(id));
  const { page } = data;
  const config = useQuery(configQuery);
  const version = search.v ?? page.version;
  const current = version === page.version;
  const live = current && !page.archived ? liveSrc(page.src, config.data) : null;
  const app = 'app' in page && Boolean(page.app);
  const history = useQuery(fileVersionsQuery(id));
  const versions = history.data ?? [];
  const before = versions.filter((v) => v.version < version);
  const against = before.some((v) => v.version === search.vs) ? search.vs : undefined;
  const text = useQuery({
    ...pageVersionQuery(id, version),
    enabled: !!version && (!live || !!against),
  });
  const prior = useQuery({ ...pageVersionQuery(id, against ?? 0), enabled: !!against });
  const [action, setAction] = useState<PageAction | null>(null);
  const replace = (change: PagesSearch) => void router.navigate({ href: pageHref({ ...search, ...change }) });
  const leave = () => router.navigate({ href: pageHref({ ...search, file: undefined, v: undefined, vs: undefined }), replace: true });
  const navigateTo = (file: string) => router.navigate({ href: pageHref({ file }) });
  const latest = versions.find((v) => v.version === version);
  const { html, noChanges } = useMemo(() => {
    if (text.data === undefined || (against && prior.data === undefined)) return {};
    const doc = parsePage(text.data);
    const count = against && prior.data !== undefined ? markChanges(parsePage(prior.data), doc) : undefined;
    return { html: frameDoc(doc, count !== undefined), noChanges: count === 0 };
  }, [against, prior.data, text.data]);
  // Refused rereads can retain cached data in the suspense query.
  if (error instanceof ApiError && [401, 403, 404].includes(error.status)) throw error;

  return (
    <>
      <Document
        title={page.title}
        path={pageHref({ file: id })}
        className="lib lib-page"
        sub={<span>{latest ? when(latest.created_at) : when(page.updated_at)} · v{version}{page.archived ? ' · Archived' : ''}</span>}
        onBack={() => void leave()}
        actions={
          <>
            {!current && <Button data-phone onClick={() => replace({ v: undefined, vs: undefined })}>Latest</Button>}
            {against && <Button data-phone onClick={() => replace({ vs: undefined })}>Close changes</Button>}
            <FileGet url={fileUrl(id, version)} link="" />
          </>
        }
        more={
          <PageMenu
            data={data}
            versions={versions}
            version={version}
            compare={before.map((v) => v.version)}
            onOpen={setAction}
            onVersion={(v) => replace({ v: v === page.version ? undefined : v, vs: undefined })}
            onCompare={(v) => replace({ vs: v })}
          />
        }
        framed
        body={
          <div className="page-body-frame">
            {noChanges && <p className="page-no-change">No text changes</p>}
            {live && !against ? (
              <HostedPage key={`${id}@${version}`} src={live} title={page.title} app={app} />
            ) : text.isError || prior.isError ? (
              <Empty icon="alert" title="This version did not load">
                <Button onClick={() => Promise.all([text.isError && text.refetch(), prior.isError && prior.refetch()])}>Try again</Button>
              </Empty>
            ) : html ? (
              <iframe key={`${version}:${against ?? ''}`} srcDoc={html} sandbox={sandbox} referrerPolicy="no-referrer" title={page.title} />
            ) : (
              <Empty title="Loading page…" />
            )}
          </div>
        }
      />
      {action && <PageActionDialog key={action} action={action} data={data} onClose={() => setAction(null)} onLeave={leave} onDuplicate={navigateTo} />}
    </>
  );
}

function HostedPage({ src, title, app }: { src: string; title: string; app: boolean }) {
  // Renewing a signed address during a reread keeps the same page running.
  const [initialSrc] = useState(src);
  return <iframe src={initialSrc} sandbox={app ? LIVE_SANDBOX.replace(' allow-same-origin', '') : LIVE_SANDBOX} referrerPolicy="no-referrer" title={title} />;
}

function PageMenu({
  data, versions, version, compare, onOpen, onVersion, onCompare,
}: {
  data: PageShown;
  versions: FileVersion[];
  version: number;
  compare: number[];
  onOpen: (action: PageAction) => void;
  onVersion: (version: number) => void;
  onCompare: (version: number) => void;
}) {
  const { page } = data;
  return (
    <Menu trigger={<IconButton icon="more" label="Page actions" />}>
      {data.can_manage && <MenuItem label="Who can see it" onSelect={() => onOpen('access')} />}
      {versions.length > 1 && (
        <MenuSub label="Versions" icon="clock">
          {versions.map((v) => <MenuItem key={v.version} label={`v${v.version}`} meta={when(v.created_at)} lead={<Face actor={v.actor} />} on={v.version === version} onSelect={() => onVersion(v.version)} />)}
        </MenuSub>
      )}
      {compare.length === 1 && <MenuItem label="What’s changed" icon="refresh" onSelect={() => onCompare(compare[0]!)} />}
      {compare.length > 1 && (
        <MenuSub label="What’s changed" icon="refresh">
          {compare.map((v) => <MenuItem key={v} label={`Since v${v}`} onSelect={() => onCompare(v)} />)}
        </MenuSub>
      )}
      {!page.archived && <MenuItem label="Pin to a sidebar" onSelect={() => onOpen('pin')} />}
      {page.may_write && <MenuItem label="Duplicate" onSelect={() => onOpen('duplicate')} />}
      {(data.can_manage || page.may_write) && <MenuItem label={page.archived ? 'Restore' : 'Archive'} icon="archive" onSelect={() => onOpen('archive')} />}
      {data.can_delete && (
        <>
          <MenuRule />
          <MenuItem label="Delete page" onSelect={() => onOpen('delete')} />
        </>
      )}
    </Menu>
  );
}
