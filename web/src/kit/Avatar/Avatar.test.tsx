import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Avatar, Faces } from './Avatar';

describe('Avatar', () => {
  it('draws a person as a circle and an agent as a rounded square', () => {
    render(
      <>
        <Avatar kind="person" name="Dana Whit" hue={28} />
        <Avatar kind="agent" name="Strategy Owner" hue={84} size={28} />
      </>,
    );
    const person = screen.getByRole('img', { name: 'Dana Whit' });
    expect(person.className).toBe('av av-22 is-person');
    expect(person.textContent).toBe('DW');
    const agent = screen.getByRole('img', { name: 'Strategy Owner' });
    expect(agent.className).toBe('av av-28 is-agent');
  });

  it('shows no letters at 16 and says when an agent is working', () => {
    render(<Avatar kind="agent" name="Builder" size={16} live />);
    const av = screen.getByRole('img', { name: 'Builder, working now' });
    expect(av.classList.contains('is-live')).toBe(true);
    expect(av.textContent).toBe('');
  });

  it('stacks faces and counts the rest', () => {
    const people = ['Ann Lee', 'Bo Park', 'Cy Diaz', 'Di Moss', 'Ed Fox', 'Flo Hart'].map((name) => ({
      kind: 'person' as const,
      name,
    }));
    render(<Faces people={people} max={4} />);
    expect(screen.getAllByRole('img')).toHaveLength(3);
    expect(screen.getByLabelText('3 more').textContent).toBe('+3');
  });
});
