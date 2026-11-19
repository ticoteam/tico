import { ICONS } from './icons';

export type IconName = keyof typeof ICONS;

export type IconProps = {
  name: IconName;
  /** Only when the icon stands alone and means something; otherwise it is hidden from screen readers. */
  label?: string;
  className?: string;
};

/** The mock's stroke icon on its 24 grid. Size and stroke come from `.ico` and the part it sits in (parts.css). */
export function Icon({ name, label, className }: IconProps) {
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true as const };
  return (
    <svg
      className={className ? `ico ${className}` : 'ico'}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...a11y}
    >
      {ICONS[name].map(([Tag, attrs], i) => (
        <Tag key={i} {...attrs} />
      ))}
    </svg>
  );
}
