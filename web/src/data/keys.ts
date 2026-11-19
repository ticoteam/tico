// Query keys, shared by data/ and live/ so a live patch can find every query that holds a row.
// A key is [root, ...parts]; features build keys only in their queries.ts, from these.
export const keys = {
  me: () => ['me'] as const,
  directory: () => ['directory'] as const,
  tasks: {
    list: (params: unknown) => ['tasks', 'list', params] as const,
    detail: (id: string) => ['tasks', 'detail', id] as const,
  },
  thread: {
    timeline: (id: string) => ['thread', id, 'timeline'] as const,
  },
} as const;
