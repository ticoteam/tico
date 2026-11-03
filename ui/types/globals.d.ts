// Window properties that classic scripts under ui/ attach at load time (observability.js, support.js). They are
// plain <script> globals with no imports, so this file declares them for the files that opt into type checks.
interface Window {
  TicoObservability?: {
    response(status: number): void;
    run<T>(path: string, operation: () => Promise<T>): Promise<T>;
  };
  supportOn?: (known?: boolean) => boolean;
  talkToUs?: (report: { kind: string; title?: string; [key: string]: unknown }) => void;
}
