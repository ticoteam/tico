import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import { FoldButton } from '@/kit/SectionHead';
import { Table, Th } from '@/kit/Table';
import { List, RecordPanel, SidePanel } from '@/pagekinds';
import type { Product } from '@/api/schema';
import type { PRODUCT_FIELDS } from './actions';
import { Face, PanelLink, useDo } from './parts';
import { productsQuery } from './queries';
import './company.css';

/* Products (pages/10-records/records.js productsPage): a List whose one table names every product; a row opens its
   record in the panel (`?panel=<key>`), and each fact there is a quiet pick that saves at once. */

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
  const q = useQuery(productsQuery);
  const open = useDo('company.openPanel');
  const products = q.data?.products ?? [];
  const panel = search.panel;
  const shown = panel && panel !== 'new' ? products.find((p) => p.key === panel) : undefined;
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : products.length ? 'ready' : 'empty';
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
      actions={add}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No products yet', action: add }}
      sections={
        <Table label="Products">
          <thead>
            <tr>
              <th className="t-mark" aria-hidden="true" />
              <th className="t-title">Name</th>
              <Th>Kind</Th>
              <Th>Status</Th>
              <Th>Tax kind</Th>
              <Th>Agreement</Th>
              <th className="num t-who">Owner</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <tr key={p.id} className={[p.status === 'retired' && 'is-quiet', p.key === panel && 'is-selected'].filter(Boolean).join(' ') || undefined}>
                <td className="t-mark" />
                <td className="t-title">
                  <PanelLink panel={p.key} data-target={`product:${p.key}`}>
                    {p.name}
                  </PanelLink>
                </td>
                <td className="t-words">{word('kind', p.kind)}</td>
                <td className="t-words">{word('status', p.status)}</td>
                <td className="t-words">{word('tax_kind', p.tax_kind)}</td>
                <td className={p.agreements.length ? 't-words' : 't-words is-none'}>{p.agreements.length ? p.agreements.map((a) => a.name).join(', ') : 'None'}</td>
                <td className="num t-who">{p.owner_teammate && <Face id={p.owner_teammate} />}</td>
              </tr>
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
  const actors = useActors();
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
      facts={
        <div className="rec-flds">
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
          <RecSec name="Agreement" n={p.agreements.length}>
            <div className="chips">
              {p.agreements.map((a) => (
                <span key={a.id} className="chip-file">
                  <Icon name="file" />
                  <span>{a.file?.title ?? a.name}</span>
                </span>
              ))}
            </div>
          </RecSec>
          <RecSec name="Code" n={p.repositories.length}>
            {p.repositories.map((r) => (
              <div key={r} className="row lk">
                <Icon name="link" />
                <span className="row-title">{r}</span>
              </div>
            ))}
          </RecSec>
          <RecSec name="Depends on" n={p.depends_on.length}>
            {p.depends_on.map((d) => (
              <div key={d.id} className="row lk">
                <span className="org-mark" aria-hidden="true">
                  {d.name.slice(0, 2).toUpperCase()}
                </span>
                <span className="row-title">{d.name}</span>
              </div>
            ))}
          </RecSec>
          <RecSec name="Risks" n={p.risks.length}>
            {p.risks.map((r) => (
              <Link key={r.id} to={'/company/risk' as never} search={{ risk: r.id } as never} className="row lk">
                <Icon name="rules" />
                <span className="row-title">{r.label}</span>
              </Link>
            ))}
          </RecSec>
        </>
      }
    />
  );
}

/** A record's section: its name, a count, and its rows; folds. An empty one is left out. */
function RecSec({ name, n, children }: { name: string; n: number; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  if (!n) return null;
  return (
    <section className={open ? 'rail-sec' : 'rail-sec is-shut'} aria-label={name}>
      <div className="rec-head">
        <h3 className="sec-name">
          <FoldButton name={name} count={n} open={open} onToggle={() => setOpen(!open)} />
        </h3>
      </div>
      {open && children}
    </section>
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
