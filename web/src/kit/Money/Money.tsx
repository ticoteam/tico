import { fmtMoney } from '@/lib';

export type MoneyProps = {
  /** Dollars. */
  amount: number;
  /** Show cents only where they matter (a price, a refund); everywhere else it is whole dollars. */
  cents?: boolean;
  className?: string;
};

/** An amount of money, formatted by `@/lib` so every page writes it the same way. */
export function Money({ amount, cents, className }: MoneyProps) {
  return <span className={className}>{fmtMoney(amount, { cents })}</span>;
}
