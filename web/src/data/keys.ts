// Query keys, shared by data/ and live/ so a live patch can find every query that holds a row.
// A key is [root, ...parts]; features build keys only in their queries.ts, from these.
export const keys = {
  me: () => ['me'] as const,
  directory: () => ['directory'] as const,
  tasks: {
    list: (params: unknown) => ['tasks', 'list', params] as const,
    detail: (id: string) => ['tasks', 'detail', id] as const,
  },
  /** Where one email send is (its state, `send_after`, `finished_at`, `detail`): its tasks' rows on the stream carry it. */
  emailSend: (id: string) => ['email-send-state', id] as const,
  thread: {
    timeline: (id: string) => ['thread', id, 'timeline'] as const,
  },
  /** The Threads page: the list (one cache for every tab that shows it), one thread's view, Catch up. */
  threads: {
    list: () => ['threads', 'list'] as const,
    view: (id: string) => ['threads', 'view', id] as const,
    catchUp: () => ['threads', 'catch-up'] as const,
  },
} as const;
