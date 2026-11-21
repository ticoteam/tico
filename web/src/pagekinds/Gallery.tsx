import type { CSSProperties, ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Board, BoardColumn } from '@/pagekinds/Board';
import { Calendar } from '@/pagekinds/Calendar';
import { CenteredCard } from '@/pagekinds/CenteredCard';
import { Dashboard } from '@/pagekinds/Dashboard';
import { Document } from '@/pagekinds/Document';
import { List } from '@/pagekinds/List';
import { ItemHead, ListAndItem } from '@/pagekinds/ListAndItem';
import { Record } from '@/pagekinds/Record';
import { Settings } from '@/pagekinds/Settings';
import { FiguresStrip, Section } from '@/pagekinds/_parts';
import '@/pagekinds/pagekinds.css';

/** A grey block standing in for a slot's content. */
function Ph({ h, n = 1 }: { h?: number; n?: number }) {
  const style = h === undefined ? undefined : ({ '--h': `${h}px` } as CSSProperties);
  return Array.from({ length: n }, (_, i) => <span key={i} className="pk-ph" style={style} />);
}

function Frame({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section aria-label={name}>
      <h2>{name}</h2>
      <div className="pk-gallery-frame">{children}</div>
    </section>
  );
}

const TABS = [
  { label: 'One', to: '/dev/pagekinds' },
  { label: 'Two', to: '/dev/pagekinds/two' },
];
const FIGS = ['One', 'Two', 'Three', 'Four'].map((label) => ({ label, value: '0' }));

/** Dev only: the nine kinds with grey blocks in every slot, to check edges, heights and the phone collapse. */
export function Gallery() {
  return (
    <div className="pk-gallery">
      <Frame name="List">
        <List
          title="List"
          path="/dev/list"
          tabs={TABS}
          actions={<Button variant="primary">New</Button>}
          more={() => {}}
          figures={<FiguresStrip figures={FIGS} />}
          sections={
            <>
              <Section name="First" count={3}>
                <Ph n={3} />
              </Section>
              <Section name="Second" count={2}>
                <Ph n={2} />
              </Section>
            </>
          }
        />
      </Frame>
      <Frame name="Board">
        <Board
          title="Board"
          path="/dev/board"
          columns={['One', 'Two', 'Three'].map((s) => (
            <BoardColumn key={s} name={s} count={2}>
              <Ph h={72} n={2} />
            </BoardColumn>
          ))}
          folded={<BoardColumn name="Done" count={9} folded onToggle={() => {}} />}
        />
      </Frame>
      <Frame name="List and item">
        <ListAndItem
          title="List and item"
          path="/dev/split"
          tabs={TABS}
          list={<Ph n={8} />}
          itemHead={<ItemHead title="Item" more={() => {}} />}
          item={<Ph h={120} n={3} />}
          foot={<Ph h={44} />}
          open
        />
      </Frame>
      <Frame name="Record">
        <Record
          title="Record"
          path="/dev/record"
          tabs={TABS}
          facts={<Ph n={4} />}
          side={<Ph n={3} />}
          sections={
            <Section name="Rows" count={2}>
              <Ph n={2} />
            </Section>
          }
        />
      </Frame>
      <Frame name="Document">
        <Document
          title="Document"
          path="/dev/document"
          tabs={TABS}
          body={<Ph h={18} n={8} />}
          side={<Ph n={5} />}
          foot={<Button variant="primary">Approve</Button>}
        />
      </Frame>
      <Frame name="Dashboard">
        <Dashboard
          title="Dashboard"
          path="/dev/dashboard"
          tabs={TABS}
          figures={<FiguresStrip figures={FIGS} />}
          chart={<Ph h={160} />}
          widgets={
            <>
              <Section name="Left">
                <Ph n={3} />
              </Section>
              <Section name="Right">
                <Ph n={3} />
              </Section>
            </>
          }
        />
      </Frame>
      <Frame name="Settings">
        <Settings title="Settings" path="/dev/settings" nav={<Ph n={6} />} body={<Ph n={8} />} open />
      </Frame>
      <Frame name="Calendar">
        <Calendar title="Calendar" path="/dev/calendar" grid={<Ph h={360} />} agenda={<Ph n={5} />} />
      </Frame>
      <Frame name="Centered card">
        <CenteredCard title="Centered card" path="/dev/centered" text="One muted line." actions={<Button variant="primary">Continue</Button>} />
      </Frame>
      <Frame name="Loading">
        <List title="Loading" path="/dev/loading" state="loading" sections={null} />
      </Frame>
      <Frame name="Empty">
        <List title="Empty" path="/dev/empty" state="empty" empty={{ title: 'Nothing here yet' }} sections={null} />
      </Frame>
      <Frame name="Fault">
        <List title="Fault" path="/dev/fault" state="fault" onRetry={() => {}} sections={null} />
      </Frame>
      <Frame name="No access">
        <List title="No access" path="/dev/noaccess" state="no-access" sections={null} />
      </Frame>
    </div>
  );
}
