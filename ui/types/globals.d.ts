// Globals that the classic scripts under ui/ define at load time, and that no file here can see: they are
// assigned as `window.NAME = ...` (or `root.NAME = ...` in an IIFE), which tsc does not turn into declarations.
// Each one is declared `any` until the file that defines it is typed. Removing an entry means that file is typed.
// Browser and host hooks that a page may lack are optional on Window.
interface Window {
  TicoObservability?: {
    response(status: number): void;
    run<T>(path: string, operation: () => Promise<T>): Promise<T>;
  };
  supportOn?: (known?: boolean) => boolean;
  talkToUs?: (report: { kind: string; title?: string; [key: string]: unknown }) => void;
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
declare var LivekitClient: any;

declare var DocsSearch: any;
declare var TicoModelLogin: any;
declare var assistantChat: any;
declare var botFiles: any;
declare var cloudAgentHire: any;
declare var cloudAgentRequest: any;
declare var cloudAgentsOpen: any;
declare var cloudAgentsSeat: any;
declare var cloudAgentsTab: any;
declare var credentialCards: any;
declare var growthStop: any;
declare var gsBoot: any;
declare var gsStartTour: any;
declare var hlBoot: any;
declare var hlComputers: any;
declare var hlMount: any;
declare var hlNav: any;
declare var hlStuckHtml: any;
declare var lensesStop: any;
declare var liveEvents: any;
declare var machineLoginPending: any;
declare var marketReload: any;
declare var marketStop: any;
declare var mountDirectorySync: any;
declare var mountGithubConnect: any;
declare var mountGoogleConnect: any;
declare var mountGranolaTool: any;
declare var mountMeetingImporters: any;
declare var mountSlackChannels: any;
declare var mountSlackConnect: any;
declare var obResizeWired: any;
declare var openMeetingImporter: any;
declare var pageCard: any;
declare var pageChangelog: any;
declare var pageLenses: any;
declare var pageMarket: any;
declare var pageStrategy: any;
declare var renderChangelogNotice: any;
declare var reportThisHTML: any;
declare var runnerOutdatedBanner: any;
declare var runnerUpdateHtml: any;
declare var strategyHeadHtml: any;
declare var strategyStop: any;
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
declare var ticoPageHidden: any;
declare var ticoSearch: any;
declare var ticoTheme: any;
declare var toolIcons: any;
