// Globals that the classic scripts under ui/ define at load time, and that no file here can see: they are
// assigned as `window.NAME = ...` (or `root.NAME = ...` in an IIFE), which tsc does not turn into declarations.
// Each one is declared `any` until the file that defines it is typed. Removing an entry means that file is typed.
// Browser and host hooks that a page may lack are optional on Window.
interface Window {
  TicoObservability?: {
    response(status: number): void;
    route(hash: string): void;
    start(): Promise<unknown>;
    run<T>(path: string, operation: () => Promise<T>): Promise<T>;
  };
  supportOn?: (known?: boolean) => boolean;
  talkToUs?: (report: { kind: string; title?: string; [key: string]: unknown }) => void;
  /** The desktop app's OK/Cancel dialog (app/src/bridge.rs DIALOG_SCRIPT): true only for OK. Called by askConfirm. */
  ticoConfirm?: (message: string) => Promise<boolean>;
  ticoNative?: any;
  ticoNativeFeatures?: any;
  ticoNativeLog?: any;
  ticoNativeVersion?: any;
  __callLeaveTimeoutMs?: number;
  threadHref: any;
  threadRowHref: any;
}

/** Vendored marked.min.js, loaded by a plain <script> in index.html. */
declare var marked: { parse(src: string, options?: object): string };
/** Vendored livekit-client.umd.min.js. */
declare var LivekitClient: LivekitNamespace;
/** Vendored docx-preview.min.js and jszip.min.js, loaded on first use by ui/app/file-review.ts. */
declare var docx: {renderAsync(data: Blob, body: HTMLElement, style: HTMLElement | null, options: Record<string, boolean>): Promise<unknown>} | undefined;
declare var JSZip: {loadAsync(data: Blob): Promise<{file(name: string): {async(type: 'string'): Promise<string>} | null}>} | undefined;

declare var TicoModelLogin: any;
declare var cloudAgentHire: any;
declare var cloudAgentRequest: any;
declare var cloudAgentsOpen: any;
declare var cloudAgentsSeat: any;
declare var cloudAgentsTab: any;
declare var growthStop: any;
declare var gsBoot: any;
declare var gsStartTour: any;
declare var hlBoot: any;
declare var hlComputers: any;
declare var hlMount: any;
declare var hlNav: any;
declare var hlStuckHtml: any;
declare var lensesStop: () => void;
declare var liveEvents: any;
declare var machineLoginPending: any;
declare var marketReload: any;
declare var marketStop: any;
declare var mountGranolaTool: any;
declare var obResizeWired: any;
declare var pageCard: any;
declare var pageChangelog: any;
declare var pageLenses: () => Promise<void>;
declare var pageMarket: any;
declare var pageStrategy: () => Promise<void>;
declare var renderChangelogNotice: any;
declare var reportThisHTML: any;
declare var runnerOutdatedBanner: any;
declare var runnerUpdateHtml: any;
declare var strategyHeadHtml: (tab: string) => string;
declare var strategyStop: () => void;
declare var supportOpen: () => Promise<void>;
declare var supportPoll: () => Promise<unknown>;
declare var supportStart: (message: string, kind?: string) => void;
declare var supportState: () => { enabled: boolean | null; unread: number };
declare var ticoAppName: any;
declare var ticoAppUpdate: any;
declare var ticoAssistantName: any;
declare var ticoBootSnap: any;
declare var ticoBootTiming: BootTimingReport | undefined;
declare var ticoIsCloud: any;
declare var ticoPageHidden: (() => { inCall: boolean }) | undefined;
declare var ticoSearch: any;
declare var ticoTheme: any;

// The page parts' stand-ins (backend/ui_bundle.py stubs), declared by the server before the shell's files; none when
// the UI is served as separate files (ui/app/parts.ts).
declare const UI_PART_STUBS: Set<unknown> | undefined;
