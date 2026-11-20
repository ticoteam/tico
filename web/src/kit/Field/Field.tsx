import { useEffect, useEffectEvent, useRef, useState, type ComponentProps, type PointerEvent, type Ref } from 'react';
import { Field as BaseField } from '@base-ui/react/field';
import { Input } from '@base-ui/react/input';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';

type IconName = ComponentProps<typeof Icon>['name'];

export type FieldProps = {
  /** Shown over the field, or read aloud only when `labelHidden`. No helper text: a clear label is enough. */
  label: string;
  labelHidden?: boolean;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Saves the words on Enter or when the field is left (builder rules: text saves on Enter or blur). Enter in an `area`
      is a new line, so there it saves on leaving only. */
  onCommit?: (value: string) => void;
  placeholder?: string;
  /** At most four words; the field takes a heavier edge and a mark. */
  error?: string;
  disabled?: boolean;
  /** Several lines. */
  area?: boolean;
  /** Fill the width it is given. */
  wide?: boolean;
  lead?: IconName;
  name?: string;
  type?: 'text' | 'email' | 'url' | 'password' | 'tel' | 'number';
  autoFocus?: boolean;
  autoComplete?: string;
  ref?: Ref<HTMLInputElement>;
};

export function Field({
  label,
  labelHidden = false,
  error,
  disabled = false,
  area = false,
  wide = true,
  lead,
  name,
  type = 'text',
  ref,
  onValueChange,
  onCommit,
  ...control
}: FieldProps) {
  const own = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  if (import.meta.env.DEV && error && error.trim().split(/\s+/).length > 4) console.warn('A field error is four words at most:', error);
  const focusControl = (e: PointerEvent) => {
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    own.current?.focus();
  };
  const setRef = (el: HTMLInputElement | null) => {
    own.current = el;
    if (typeof ref === 'function') ref(el);
    else if (ref) ref.current = el;
  };
  return (
    <BaseField.Root className="f" invalid={error ? true : undefined} disabled={disabled} name={name}>
      {!labelHidden && <BaseField.Label className="f-label">{label}</BaseField.Label>}
      <div
        className={['field', wide && 'is-wide', area && 'is-area', error && 'is-error', disabled && 'is-disabled'].filter(Boolean).join(' ')}
        onPointerDown={focusControl}
      >
        {lead && <Icon name={lead} />}
        {area ? (
          <BaseField.Control
            {...control}
            aria-label={labelHidden ? label : undefined}
            onValueChange={onValueChange ? (v) => onValueChange(v) : undefined}
            onBlur={onCommit ? (e) => onCommit(e.currentTarget.value) : undefined}
            render={<textarea ref={(el) => void (own.current = el)} />}
          />
        ) : (
          <BaseField.Control
            {...control}
            ref={setRef}
            type={type}
            aria-label={labelHidden ? label : undefined}
            onValueChange={onValueChange ? (v) => onValueChange(v) : undefined}
            onBlur={onCommit ? (e) => onCommit(e.currentTarget.value) : undefined}
            onKeyDown={
              onCommit
                ? (e) => {
                    if (e.key === 'Enter') onCommit(e.currentTarget.value);
                  }
                : undefined
            }
          />
        )}
        {error && <Icon name="alert" />}
      </div>
      {error && (
        <BaseField.Error className="f-err" match render={<span />}>
          {error}
        </BaseField.Error>
      )}
    </BaseField.Root>
  );
}

export type SearchProps = {
  /** Read aloud; the well shows only the placeholder. */
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  /** Full width inside a menu or a sheet. */
  wide?: boolean;
  autoFocus?: boolean;
  ref?: Ref<HTMLInputElement>;
};

/** The search well: a magnifier, the words, and a clear button once there are words. */
export function Search({ label, value, onValueChange, placeholder, wide = false, autoFocus, ref }: SearchProps) {
  const own = useRef<HTMLInputElement | null>(null);
  // a press anywhere on the well but the words and Clear puts the cursor in it: the magnifier too, which is all a head
  // search squeezed to its icon shows
  const focusInput = (e: PointerEvent) => {
    if ((e.target as Element).closest('input, button')) return;
    e.preventDefault();
    own.current?.focus();
  };
  return (
    <div className={`search${wide ? ' search-wide' : ''}${value ? ' has-words' : ''}`} onPointerDown={focusInput}>
      <Icon name="search" />
      <Input
        ref={(el: HTMLInputElement | null) => {
          own.current = el;
          if (typeof ref === 'function') ref(el);
          else if (ref) ref.current = el;
        }}
        type="text"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onValueChange={(v) => onValueChange(v)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && value) {
            e.preventDefault();
            onValueChange('');
          }
        }}
      />
      {value && (
        <IconButton
          icon="close"
          label="Clear"
          onClick={() => {
            onValueChange('');
            own.current?.focus();
          }}
        />
      )}
    </div>
  );
}

export type SettledSearchProps = Omit<SearchProps, 'value' | 'onValueChange'> & {
  /** The words that count now (the address's `?q=`). */
  value: string;
  /** The trimmed words, once typing has paused for `wait` ms. */
  onSettle: (words: string) => void;
  wait?: number;
};

/** A search a step behind the typing: the address (and the server) hear the words once they settle. The field follows
    `value` when it changes from elsewhere (Back, Tico). In a page head it is the field itself, as every mock draws it
    (files.js, tools.js, list.js); on a phone the head gives it its own line. */
export function SettledSearch({ value, onSettle, wait = 250, ...rest }: SettledSearchProps) {
  const [words, setWords] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    if (value !== words.trim()) setWords(value);
  }
  const settle = useEffectEvent(onSettle);
  useEffect(() => {
    const next = words.trim();
    if (next === value) return;
    const t = setTimeout(() => settle(next), wait);
    return () => clearTimeout(t);
  }, [words, value, wait]);
  return <Search {...rest} value={words} onValueChange={setWords} />;
}
