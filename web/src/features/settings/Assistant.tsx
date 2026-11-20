/* Your assistant (Settings > Account): what you call it, its Dot's color and its sprite. Yours alone (tico/look.ts):
   nobody else's screens change. Each choice is a still Dot wearing it, so you pick what you will see. */
import { Field } from '@/kit/Field';
import { Facts } from '@/kit/LabelValue';
import { Section } from '@/pagekinds';
import { Dot } from '@/tico/dot';
import { COLOR_NAME, DEFAULT_NAME, DOT_COLORS, lookAttrs, useLook, type DotColor, type DotSprite, type Look } from '@/tico/look';
import { Line, act } from './parts';

const SWATCH = 24;

function Choice({ label, on, look, onPick }: { label: string; on: boolean; look: Look; onPick: () => void }) {
  return (
    <button type="button" className="set-look" aria-label={label} title={label} aria-pressed={on} onClick={onPick} {...lookAttrs(look)}>
      <Dot size={SWATCH} still />
    </button>
  );
}

export function AssistantSection() {
  const look = useLook();
  const color: DotColor = look.color ?? 'jade';
  const sprite: DotSprite | null = look.sprite ?? null;
  const name = look.name ?? '';
  return (
    <Section name="Your assistant" target="setting:assistant">
      <Facts label="Your assistant">
        <Line label="Name" target="setting:assistant_name">
          <Field
            // a new name from elsewhere (another tab, Tico) shows here
            key={name}
            label="Your assistant's name"
            labelHidden
            defaultValue={name}
            placeholder={DEFAULT_NAME}
            onCommit={(v) => v.trim() !== name && void act('settings.setAssistantLook', { name: v.trim() || null })}
          />
        </Line>
        <Line label="Color" target="setting:assistant_color">
          <span className="set-looks" role="group" aria-label="Color">
            {DOT_COLORS.map((c) => (
              <Choice key={c} label={COLOR_NAME[c]} on={c === color} look={{ color: c, sprite }} onPick={() => void act('settings.setAssistantLook', { color: c })} />
            ))}
          </span>
        </Line>
        <Line label="Sprite" target="setting:assistant_sprite">
          <span className="set-looks" role="group" aria-label="Sprite">
            <Choice label="None" on={!sprite} look={{ color, sprite: null }} onPick={() => void act('settings.setAssistantLook', { sprite: null })} />
            <Choice label="Pixel face" on={sprite === 'pixel'} look={{ color, sprite: 'pixel' }} onPick={() => void act('settings.setAssistantLook', { sprite: 'pixel' })} />
          </span>
        </Line>
      </Facts>
    </Section>
  );
}
