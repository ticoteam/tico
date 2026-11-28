// What the routes and the shell use before a page's code loads: the reads, the address checks and the menu's
// pipelines. No components here: the first load imports this file, and a page comes through exports.ts with its route.
export { inOrder, isFiltered, loadMore, pipelinesQuery, pipelineTasksQuery, shelfQuery, stageCount, stagePriorityQuery, taskHitsQuery, useStageSort, flowCountQuery, flowQuery, flowSnoozedAsksQuery, useFlowCountRefresh, tagsQuery, tagQuery } from './queries';
export { isNeedsYouRead, leaveNeedsYou, needsYouIds, needsYouLeft, needsYouRows, needsYouTasks, needsYouWith, needsYouWithout, settleNeedsYou, useNeedsYou, useNeedsYouDrawn, useNeedsYouRead, waitsOnMe, yoursFirst } from './needs';
export type { ApprovalItem } from './queries';
export type { Pipeline, Stage, TaskItem, Filters, Flow, FlowItem, FlowRequest, Sort } from './queries';
export { validateTasksSearch, validateRoadmapSearch, filtersOf, sortOf, LAST_PREF, VIEW_PREF } from './search';
export type { TasksSearch, RoadmapSearch, View } from './search';
export { firstPipeline } from './home';
export { roadmapPipeline } from './roadmap';

/** The tools of the list beside an open task (ListTools): a loader, not a component, so the first load carries only
    this line and the tools come in a chunk of their own (the search field, the filter and sort rows), without the board. */
export const loadListTools = () => import('./ListTools');
