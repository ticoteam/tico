import { useQuery } from '@tanstack/react-query';
import { flowGroups, flowQuery, pipelinesQuery, type FlowItem } from '@/features/tasks/routing';

/** The tasks that wait on the person in the order Flow lists them, section by section: Flow's previous and next. The
    same read as Flow's list (GET /api/v2/needs-you, one of the slowest), so a task opened from Flow does not read it a
    second time. */
export function useFlowRows(enabled = true): { rows: FlowItem[]; read: boolean } {
  const flow = useQuery({ ...flowQuery, enabled });
  const pipes = useQuery({ ...pipelinesQuery, enabled });
  const rows = flow.data ? flowGroups(flow.data.items, pipes.data ?? []).flatMap((g) => g.items) : [];
  return { rows, read: flow.isSuccess };
}

/** Where Flow goes once `id` is off the person's hands: the row after it, else the one before, else the first. Read
    before the answer is sent, since the answered row leaves the list at once. */
export function nextAfter(rows: readonly FlowItem[], id: string): FlowItem | null {
  const i = rows.findIndex((w) => w.id === id);
  return (i >= 0 ? (rows[i + 1] ?? rows[i - 1]) : rows[0]) ?? null;
}
