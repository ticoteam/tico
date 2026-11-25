import { useActSheet } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import './livepage.css';

/* A page app's ask to write (data/pageApp.ts), drawn by Tico outside the frame: each line is what the server will run,
   in Tico's own words for what it names, never the page's. Nothing is written until Confirm; it runs as the viewer. */
export function AppActSheet() {
  const sheet = useActSheet();
  if (!sheet) return null;
  const n = sheet.lines.length;
  return (
    <Dialog
      open
      alert
      onOpenChange={(open) => {
        if (!open) void sheet.answer(false);
      }}
      title={`${sheet.page} asks to`}
      description="As you, once."
      actions={
        <>
          <Button onClick={() => sheet.answer(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => sheet.answer(true)}>
            {n === 1 ? 'Confirm' : `Confirm ${n}`}
          </Button>
        </>
      }
    >
      <ul className="act-lines">
        {sheet.lines.map((line, i) => (
          <li key={i}>
            <b>{line.label}</b>
            {line.detail && <p>{line.detail}</p>}
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
