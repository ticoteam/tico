import { Avatar, type AvatarKind } from '@/kit/Avatar';
import { Link } from '@/kit/Link';

export type OwnerButtonProps = {
  /** The page owner's name: the button reads "Chat with <name>". */
  name: string;
  kind: AvatarKind;
  /** Their hue (0-360). */
  hue?: number;
  /** Working now: the ink ring round the face. */
  live?: boolean;
  /** Where their chat opens beside the page (the page's address with `?chat=`). Or `onPress` instead. */
  href?: string;
  onPress?: () => void;
  /** Their chat is open beside the page. */
  on?: boolean;
  /** `<kind>:<id>` for Tico's flights. */
  target?: string;
};

/** The page owner's face in the head (shell.css `.pg-owner`): it opens their chat beside the page. No words. */
export function OwnerButton({ name, kind, hue, live, href, onPress, on = false, target }: OwnerButtonProps) {
  const label = `Chat with ${name}`;
  const face = <Avatar kind={kind} name={name} hue={hue} live={live} size={28} decorative />;
  const cls = on ? 'pg-owner is-on' : 'pg-owner';
  if (href !== undefined)
    return (
      <Link href={href} className={cls} aria-label={label} title={label} aria-current={on ? 'true' : undefined} data-target={target} onClick={onPress}>
        {face}
      </Link>
    );
  return (
    <button type="button" className={cls} aria-label={label} title={label} aria-pressed={on} data-target={target} onClick={onPress}>
      {face}
    </button>
  );
}
