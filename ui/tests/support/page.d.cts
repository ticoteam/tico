// Types for support/page.cjs (the helper stays JavaScript: 187 scripts require it by its .cjs path).
export declare const bundled: boolean;
export declare const html: string;
export declare const harnesses: unknown;
export declare const beta: unknown;
export declare function uiFile(name: string): string;
// A route as the browser tests use it: only fulfil is needed here.
export interface FulfilRoute {
  fulfill(options: {status?: number; contentType?: string; body?: string | Buffer; path?: string}): Promise<void>;
}
export declare function isUiPath(pathname: string): boolean;
export declare function serveUi(route: FulfilRoute, pathname: string): Promise<void>;
