/** A page that is not built yet: its title, then the mock's empty state. Page builders replace it. */
export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <header className="page-head">
        <h1>{title}</h1>
      </header>
      <div className="empty">
        <div className="empty-art" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </div>
        <h2>Not built yet</h2>
      </div>
    </>
  );
}
