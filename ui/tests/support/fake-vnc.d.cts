// Types for support/fake-vnc.cjs (the helper stays JavaScript: computer-screen.cts requires it by its .cjs path).
import type {Page, WebSocketRoute} from 'playwright';

export declare function fakeVnc(ws: WebSocketRoute, name?: string, heard?: number[] | null): void;
export declare function exposeRoutedSockets(page: Page): Promise<void>;
