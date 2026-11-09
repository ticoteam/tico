// Types for support/pipeline-fixture.cjs (the helper stays JavaScript: the pipeline specs require it by its .cjs path).
import type {Page} from 'playwright';

export interface PipelineInstallOptions {
  refuseApprove?: boolean;
  manager?: boolean;
  refuseMove?: boolean;
  late?: Record<string, unknown>[];
  ownerOnly?: boolean;
  member?: boolean;
  claimedBy?: string | null;
  noRequirements?: boolean;
  validate?: boolean;
  bare?: boolean;
  text?: boolean;
  held?: boolean;
  plain?: boolean;
  twoCards?: boolean;
  unopenable?: boolean;
}

// What the fake pipeline server recorded. Lists grow as the page reads and writes.
export interface PipelineState {
  posts: Record<string, unknown>[];
  queueReads: number;
  refuseApprove: boolean;
  refuseMove: boolean;
  moves: Record<string, unknown>[];
  prefs: Record<string, unknown>;
  attachments: Record<string, unknown>[];
  managerReads: number;
  claims: Record<string, unknown>[];
  claimedBy: string | null;
  removed: Set<string>;
  lastDone: unknown;
  affectedReads: unknown[];
  requirementReads: Record<string, number>;
  attachmentReads: number;
  validate: boolean;
  bare: boolean;
  text: boolean;
  held: boolean;
  plain: boolean;
  twoCards: boolean;
  unopenable: boolean;
  stage: Record<string, string>;
  moved: Record<string, string>;
  lateReads: number;
  lateAsks: (string | null)[];
}

export declare const type: Record<string, unknown>;
export declare function tasks(): Record<string, unknown>[];
export declare function install(page: Page, options?: PipelineInstallOptions): Promise<PipelineState>;
