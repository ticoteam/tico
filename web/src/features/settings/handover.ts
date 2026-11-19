/* `run` answers {ok, did, error} and never what the action made. A secret shown once (a new token, a computer's code)
   and a sign-in just started are handed from the action to the control that ran it here, taken once. */
const box = new Map<string, unknown>();

export function handOver(key: string, value: unknown): void {
  box.set(key, value);
}

export function take<T>(key: string): T | undefined {
  const v = box.get(key) as T | undefined;
  box.delete(key);
  return v;
}
