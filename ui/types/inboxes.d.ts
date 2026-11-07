// Types for the inbox kinds' pages under Tools (ui/app/inboxes.js and one file a kind). Declarations only: no global is
// declared here, so the .js file that defines a global keeps the definition.

/** The signed-in person as the inbox pages read them: /api/me, plus the actor id a sign-in may carry. */
type InboxMe = SelfPerson & { actor?: string };

/** What an inbox kind's draw is given (inboxesCtx in ui/app/inboxes.js). */
interface InboxCtx {
  admin: boolean;
  me: InboxMe | null;
  /** The draw's number; a newer draw makes this one stale. */
  run: number;
  /** False once another draw began or the page was left. */
  live: () => boolean;
  /** One load per draw, shared by every kind: a later caller gets the first caller's promise. */
  once: <T>(name: string, load: () => T | PromiseLike<T>) => Promise<T>;
  refresh: () => void;
  go: (view: string) => void;
}
