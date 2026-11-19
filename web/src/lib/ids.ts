/** A fresh random id: Idempotency-Keys, pending rows (`tmp:<id>`), the tab id. */
export function newId(): string {
  return crypto.randomUUID();
}
