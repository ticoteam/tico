import type { TaskFileView } from '@/api/schema';
import { useNavigate } from '@tanstack/react-router';
import { Thumbs } from '@/kit/Thumbs';
import { changeWords } from '@/lib';
import { mediaOf } from './model';

/** The task's pictures and videos first, at the top of the task, newest first: a press opens one a layer up, in the
    viewer a file from Details opens in, over the tab the person is on. Every other file stays where it was. */
export function TaskMedia({ files }: { files: TaskFileView[] | undefined }) {
  const navigate = useNavigate();
  const media = mediaOf(files);
  if (!media.length) return null;
  // in place: closing the file (Esc, its Close) replaces the address too, so one Back still closes the task. The tab's
  // own version and comparison (`v`, `vs`) stay for when it closes; the file opens at its newest.
  const open = (file: string) =>
    void navigate({ to: '.', search: changeWords(({ fv: _v, fvs: _vs, full: _p, ...rest }) => ({ ...rest, file })) as never, replace: true });
  return (
    <div className="tv-media" data-target="task-media">
      <Thumbs
        strip
        lazy
        label="Pictures and videos"
        items={media.map(({ file, version, video }) => ({
          id: file.id,
          name: file.title || file.name,
          video,
          src: video ? version.poster_url : (version.thumb_url ?? version.url),
          full: video ? null : version.url,
        }))}
        onOpen={(item) => open(item.id)}
      />
    </div>
  );
}
