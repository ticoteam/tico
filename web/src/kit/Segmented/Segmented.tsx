import type { ComponentProps } from 'react';
import { useLayoutEffect, useRef, useState } from 'react';
import { Toggle } from '@base-ui/react/toggle';
import { ToggleGroup } from '@base-ui/react/toggle-group';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import './Segmented.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type SegmentedOption<V extends string = string> = {
  value: V;
  /** The words shown, or read aloud when the option is an icon. */
  label: string;
  icon?: IconName;
  disabled?: boolean;
};

export type SegmentedProps<V extends string = string> = {
  /** Names the group for a screen reader. */
  label: string;
  options: readonly SegmentedOption<V>[];
  value: V;
  onValueChange: (value: V) => void;
  disabled?: boolean;
  className?: string;
};

/** The room inside an element, without its padding. */
function room(el: HTMLElement) {
  const style = getComputedStyle(el);
  return el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
}

/**
 * A few choices side by side, one always on. Icons when every option has one, words otherwise. Words that do not fit
 * the room they are given (a phone's dialog) become a select, the kit's picker, until there is room for them again.
 */
export function Segmented<V extends string>({ label, options, value, onValueChange, disabled = false, className }: SegmentedProps<V>) {
  const icons = options.every((o) => o.icon);
  const group = useRef<HTMLDivElement>(null);
  const holder = useRef<HTMLElement | null>(null);
  /** The width the words need, once they have not fitted; 0 while they fit. */
  const [need, setNeed] = useState(0);
  const words = options.map((o) => o.label).join('\n');

  useLayoutEffect(() => {
    if (icons || typeof ResizeObserver === 'undefined') return;
    // the room is the nearest box around it: a span or a display:contents wrapper has no width of its own
    let around = group.current?.parentElement;
    while (around && /^(inline|contents)$/.test(getComputedStyle(around).display)) around = around.parentElement;
    if (around) holder.current = around;
    const parent = holder.current;
    if (!parent) return;
    const fit = () => {
      const seg = group.current;
      if (seg?.isConnected) {
        if (seg.scrollWidth > room(parent) + 1) setNeed(seg.scrollWidth);
      } else if (need && room(parent) >= need) setNeed(0);
    };
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(parent);
    return () => watch.disconnect();
  }, [icons, words, need]);

  if (need)
    return <Select label={label} labelHidden options={options} value={value} onValueChange={onValueChange} disabled={disabled} />;
  return (
    <ToggleGroup
      ref={group}
      className={[icons ? 'seg' : 'seg is-words', className].filter(Boolean).join(' ')}
      aria-label={label}
      value={[value]}
      disabled={disabled}
      onValueChange={(next) => {
        const picked = next[0];
        if (picked !== undefined) onValueChange(picked as V);
      }}
    >
      {options.map((o) => (
        <Toggle
          key={o.value}
          value={o.value}
          disabled={o.disabled}
          className={(state) => (state.pressed ? 'is-on' : '')}
          aria-label={icons ? o.label : undefined}
          title={icons ? o.label : undefined}
        >
          {icons && o.icon ? <Icon name={o.icon} /> : o.label}
        </Toggle>
      ))}
    </ToggleGroup>
  );
}
