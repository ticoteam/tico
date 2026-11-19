import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { Table, Th, Tr } from '@/kit/Table';
import { List, RecordPanel, Section, SidePanel } from '@/pagekinds';
import type { Product } from '@/api/schema';
import type { PRODUCT_FIELDS } from './actions';
import { Face, Go, PageOwner, PanelLink, useDo } from './parts';
import { productsQuery, repositoriesQuery, type ProductListField } from './queries';
import './company.css';

/* Products (pages/10-records/records.js productsPage): a List whose one table names every product; a row opens its
   record in the panel (`?panel=<key>`), and each fact there is a quiet pick that saves at once. The Dot on a product
   that waits on you is left out: the server says nothing of it per product. */

export interface ProductsSearch {
  panel?: string;
}

type Field = (typeof PRODUCT_FIELDS)[number];
const OPTIONS: Record<Exclude<Field, 'owner_teammate'>, { value: string; label: string }[]> = {
  kind: [
    { value: 'software', label: 'Software' },
    { value: 'service', label: 'Service' },
    { value: 'goods', label: 'Goods' },
  ],
  status: [
    { value: 'planned', label: 'Planned' },
    { value: 'beta', label: 'Beta' },
    { value: 'live', label: 'Live' },
    { value: 'retired', label: 'Retired' },
  ],
  tax_kind: [
    { value: 'software_service', label: 'Software' },
    { value: 'service', label: 'Service' },
    { value: 'goods', label: 'Goods' },
    { value: 'digital_goods', label: 'Digital goods' },
    { value: 'not_taxed', label: 'Not taxed' },
    { value: 'unknown', label: 'Unknown' },
  ],
};
const word = (f: keyof typeof OPTIONS, v: string | null) => (v ? (OPTIONS[f].find((o) => o.value === v)?.label ?? v) : '');

export function ProductsPage({ search }: { search: ProductsSearch }) {
  const { products } = useSuspenseQuery(productsQuery).data;
  const open = useDo('company.openPanel');
  const panel = search.panel;
  const shown = panel && panel !== 'new' ? products.find((p) => p.key === panel) : undefined;
  const add = (
    <Button variant={panel ? 'plain' : 'primary'} icon="plus" onClick={() => void open.run({ panel: 'new' })}>
      Add product
    </Button>
  );
  return (
    <List
      title="Products"
      path="/company/products"
      className="co"
      actions={
        <>
          <PageOwner page="products" />
          {add}
        </>
      }
      state={products.length ? 'ready' : 'empty'}
      empty={{ title: 'No products yet', action: add }}
      sections={
        <Table label="Products">
          <thead>
            <tr>
              <th className="t-mark" aria-hidden="true" />
              <th className="t-name">Name</th>
              <Th>Kind</Th>
              <Th>Status</Th>
              <Th>Tax kind</Th>
              <Th>Agreement</Th>
              <th className="num t-who">Owner</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <Tr key={p.id} kind={p.status === 'retired' ? 'quiet' : undefined} selected={p.key === panel} linked>
                <td className="t-mark" />
                <td className="t-name" data-opens>
                  <PanelLink panel={p.key} data-target={`product:${p.key}`}>
                    {p.name}
                  </PanelLink>
                </td>
                <td className="t-words">{word('kind', p.kind)}</td>
                <td className="t-words">{word('status', p.status)}</td>
                <td className="t-words">{word('tax_kind', p.tax_kind)}</td>
                <td className={p.agreements.length ? 't-words' : 't-words is-none'}>{p.agreements.length ? p.agreements.map((a) => a.name).join(', ') : 'None'}</td>
                <td className="num t-who">{p.owner_teammate && <Face id={p.owner_teammate} />}</td>
              </Tr>
            ))}
          </tbody>
        </Table>
      }
      panel={panel === 'new' ? <NewProduct /> : shown ? <ProductRecord p={shown} /> : null}
    />
  );
}

/** One product: its facts save at once; then what it is bound by, its code and what it depends on. */
function ProductRecord({ p }: { p: Product }) {
  const close = useDo('company.closePanel');
  const update = useDo('products.update');
  const rename = useDo('products.rename');
  const remove = useDo('products.removeFrom');
  const [naming, setNaming] = useState<string | null>(null);
  const actors = useActors();
  const off = (field: ProductListField, value: string, name: string) => (
    <IconButton icon="close" label={`Remove ${name}`} size="sm" quiet onClick={() => void remove.run({ product: p.key, field, value })} />
  );
  const saveName = async () => {
    const name = naming?.trim();
    setNaming(null);
    if (name && name !== p.name) await rename.run({ product: p.key, name });
  };
  const owners = actors.map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined }));
  const set = (field: Field) => (value: string) => void update.run({ product: p.key, field, value });
  const pick = (field: keyof typeof OPTIONS, label: string) => (
    <div className="fact fld">
      <span>{label}</span>
      <span className="fact-v">
        <Select label={label} labelHidden look="quiet" placeholder="None" options={OPTIONS[field]} value={p[field]} onValueChange={set(field)} />
      </span>
    </div>
  );
  return (
    <RecordPanel
      title={p.name}
      target={`product:${p.key}`}
      onClose={() => void close.run({})}
      actions={
        <Menu trigger={<IconButton icon="more" label="More" quiet />}>
          <MenuItem label="Rename" onSelect={() => setNaming(p.name)} />
        </Menu>
      }
      facts={
        <div className="rec-flds">
          {naming != null && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void saveName();
              }}
            >
              <Field label="Name" labelHidden value={naming} onValueChange={setNaming} autoFocus />
            </form>
          )}
          {pick('kind', 'Kind')}
          {pick('status', 'Status')}
          {pick('tax_kind', 'Tax kind')}
          <div className="fact fld">
            <span>Owner</span>
            <span className="fact-v">
              <Select label="Owner" labelHidden look="quiet" placeholder="None" options={owners} value={p.owner_teammate} onValueChange={set('owner_teammate')} />
            </span>
          </div>
        </div>
      }
      sections={
        <>
          <RecSec name="Agreement" n={p.agreements.length} add={{ product: p.key, field: 'agreements', label: 'Add an agreement' }}>
            <div className="chips">
              {p.agreements.map((a) => (
                <span key={a.id} className="chip-file">
                  {a.file ? (
                    <a href={a.file.page ? `/library/${encodeURIComponent(a.file.id)}` : `/api/v2/files/${encodeURIComponent(a.file.id)}`} {...(!a.file.page && { target: '_blank', rel: 'noopener' })}>
                      <Icon name="file" />
                      <span>{a.file.title || a.name}</span>
                    </a>
                  ) : (
                    <>
                      <Icon name="file" />
                      <span>{a.name}</span>
                    </>
                  )}
                  {off('agreements', a.id, a.name)}
                </span>
              ))}
            </div>
          </RecSec>
          <RecSec name="Code" n={p.repositories.length} add={{ product: p.key, field: 'repositories', label: 'Add a repository', have: p.repositories }}>
            {p.repositories.map((r) => (
              <div key={r} className="row lk">
                <Icon name="link" />
                <a className="row-title" href={`https://github.com/${r}`} target="_blank" rel="noopener noreferrer">
                  {r}
                </a>
                {off('repositories', r, r)}
              </div>
            ))}
          </RecSec>
          <RecSec name="Depends on" n={p.depends_on.length} add={{ product: p.key, field: 'depends_on', label: 'Add a vendor' }}>
            {p.depends_on.map((d) => (
              <div key={d.id} className="row lk">
                <span className="org-mark" aria-hidden="true">
                  {d.name.slice(0, 2).toUpperCase()}
                </span>
                <Go to="/library/contacts" search={{ contact: d.id }} className="row-title">
                  {d.name}
                </Go>
                {off('depends_on', d.id, d.name)}
              </div>
            ))}
          </RecSec>
        </>
      }
    />
  );
}

type Adds = { product: string; field: ProductListField; label: string; have?: readonly string[] };

/** The quiet + of a list: a field under its name that adds one in place (a pick of the team's repositories for Code,
    a name otherwise; a new name makes the record). */
function AddTo({ add, onDone }: { add: Adds; onDone: () => void }) {
  const run = useDo('products.addTo');
  const repos = useQuery({ ...repositoriesQuery, enabled: add.field === 'repositories' });
  const [value, setValue] = useState('');
  const free = (repos.data ?? []).filter((r) => !add.have?.some((h) => h.toLowerCase() === r.toLowerCase()));
  const save = async () => {
    if (value.trim() && (await run.run({ product: add.product, field: add.field, value: value.trim() }))) onDone();
  };
  const label = add.field === 'repositories' ? 'Repository' : add.field === 'agreements' ? 'Agreement' : 'Vendor';
  return (
    <form
      className="went"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      {add.field === 'repositories' && free.length ? (
        <Select label={label} labelHidden placeholder={label} options={free.map((r) => ({ value: r, label: r }))} value={value || null} onValueChange={setValue} />
      ) : (
        <Field label={label} labelHidden placeholder={add.field === 'repositories' ? 'owner/name' : label} value={value} onValueChange={setValue} autoFocus wide={false} />
      )}
      <Button type="submit" variant="primary" busy={run.pending}>
        Add
      </Button>
      <Button variant="quiet" onClick={onDone}>
        Cancel
      </Button>
    </form>
  );
}

/** A record's section (the kit's `Section`, which folds): its rows and the quiet + that adds one in place. */
function RecSec({ name, n, add, children }: { name: string; n: number; add: Adds; children: ReactNode }) {
  const [adding, setAdding] = useState(false);
  return (
    <Section name={name} count={n} actions={<IconButton icon="plus" label={add.label} size="sm" quiet onClick={() => setAdding(true)} />}>
      {adding && <AddTo add={add} onDone={() => setAdding(false)} />}
      {children}
    </Section>
  );
}

function NewProduct() {
  const create = useDo('products.create');
  const close = useDo('company.closePanel');
  const [name, setName] = useState('');
  const save = async () => {
    if (name.trim() && (await create.run({ name: name.trim() }))) void close.run({});
  };
  return (
    <SidePanel
      title="New product"
      target="panel:product-new"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={create.pending} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={() => void close.run({})}>
            Cancel
          </Button>
        </>
      }
    >
      <form
        className="rail-sec"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Name" value={name} onValueChange={setName} autoFocus />
      </form>
    </SidePanel>
  );
}
