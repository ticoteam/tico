import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type * as Actions from '@/actions';
import { Shelf } from './Rich';
import type { Attachment, ChatMessage } from './types';

const run = vi.hoisted(() => vi.fn());
vi.mock('@/actions', async (actual) => ({ ...(await actual<typeof Actions>()), run }));
afterEach(() => run.mockClear());

const shelf = (...attachments: Attachment[]) => render(<Shelf m={{ id: 'm1', body: '', refs: { attachments } } as ChatMessage} onPicture={() => {}} />);

describe('a message’s own media under its words', () => {
  it('shows a video’s poster and makes the player only on a tap, playing in place from its address', () => {
    const { container } = shelf({ id: 'v1', name: 'demo.mp4', content_type: 'video/mp4' });
    expect(container.querySelector('video')).toBeNull();
    const play = screen.getByRole('button', { name: 'demo.mp4' });
    expect(play.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/v1/poster');
    // no poster made (none in the image without ffmpeg): a plain card with its play mark, still a tap to play
    fireEvent.error(play.querySelector('img')!);
    expect(play.querySelector('img')).toBeNull();
    fireEvent.click(play);
    const video = container.querySelector('video')!;
    expect(video.getAttribute('src')).toBe('/api/v2/files/v1');
    expect(video.hasAttribute('playsinline')).toBe(true);
    // Expand pauses it and opens the file full screen, streamed from the same address
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => {});
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(pause).toHaveBeenCalled();
    expect(run).toHaveBeenCalledWith('files.preview', { file: 'v1', held: { url: '/api/v2/files/v1', mime: 'video/mp4', name: 'demo.mp4' } }, { via: 'click' });
    // a video the browser cannot play is offered as a download
    fireEvent.error(video);
    expect(container.querySelector('video')).toBeNull();
    expect(screen.getByRole('link', { name: /demo\.mp4/ }).hasAttribute('download')).toBe(true);
  });

  it('draws a sound as the browser’s own player with its name, never read ahead whole', () => {
    const { container } = shelf({ id: 's1', name: 'memo.mp3', content_type: 'audio/mpeg' });
    const audio = container.querySelector('audio')!;
    expect(audio.getAttribute('src')).toBe('/api/v2/files/s1');
    expect(audio.hasAttribute('controls')).toBe(true);
    expect(audio.getAttribute('preload')).toBe('metadata');
    expect(screen.getByText('memo.mp3')).toBeTruthy();
  });

  it('shows a PDF’s first page with its name, a plain card when there is none, and opens it full screen', () => {
    const { container } = shelf({ id: 'p1', name: 'lease.pdf', content_type: 'application/pdf', size: 2048 });
    const thumb = container.querySelector('.pcard img')!;
    expect(thumb.getAttribute('src')).toBe('/api/v2/files/p1/thumb');
    expect(screen.getByText('PDF')).toBeTruthy();
    fireEvent.error(thumb);
    expect(container.querySelector('.pcard img')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'lease.pdf' }));
    expect(run).toHaveBeenCalledWith(
      'files.preview',
      { file: 'p1', held: { url: '/api/v2/files/p1', mime: 'application/pdf', name: 'lease.pdf', size: 2048 } },
      { via: 'click' },
    );
  });
});
