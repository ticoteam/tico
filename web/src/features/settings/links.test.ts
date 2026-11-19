import { describe, expect, it } from 'vitest';
import { fixHref } from './fixes';
import { installCommands, runnerName } from './install';
import { historyFilter } from './queries';

describe('health fixes', () => {
  it("take the new app's address, keep outside pages, and drop a page with no new address", () => {
    expect(fixHref({ label: 'Open Computers', href: '#/settings', tab: 'devices' })).toBe('/settings/computers');
    expect(fixHref({ label: 'Open Usage', href: '#/usage' })).toBe('/company/usage');
    expect(fixHref({ label: 'Open task', href: '#/tasks/412' })).toBe('/task/412');
    expect(fixHref({ label: 'Open GitHub', href: '#/tools/github' })).toBe('/library/tools');
    expect(fixHref({ label: 'Docs', href: 'https://example.test/docs' })).toBe('https://example.test/docs');
    expect(fixHref({ label: 'Open Runs', href: '#/settings', tab: 'runs' })).toBeNull();
    expect(fixHref({ label: 'Open Meetings', href: '#/meetings' })).toBeNull();
    expect(fixHref({ label: 'Sign in' })).toBeNull();
  });
});

describe('add computer', () => {
  const config = { runner_url: 'https://tico.example.test', compose_project: 'Tico', runner_compat: { version: '0.6.20' } };

  it('writes the installer line with the code, the name and the release', () => {
    const [main, docker] = installCommands(config, 'https://ignored.test', 'server', 'AB-12cd34', 'Build box');
    expect(runnerName(config, 'AB-12cd34')).toBe('tico-ab12cd34');
    expect(main?.line).toBe(
      "curl -fsSL https://github.com/ticoteam/tico/releases/download/v0.6.20/install.sh -o install.sh && sh install.sh --runner --name tico-ab12cd34 --url https://tico.example.test --code 'AB-12cd34' --label \"Build box\"",
    );
    expect(docker?.line).toContain('ghcr.io/ticoteam/tico-runner:v0.6.20 join --url https://tico.example.test');
  });

  it('gives Windows its PowerShell line, and a quick start joins the server network', () => {
    const [win] = installCommands({}, 'https://here.test', 'windows', 'code1', "Ana's PC");
    expect(win?.line).toContain("-Url https://here.test -Code 'code1' -Label 'Ana''s PC'");
    const [local] = installCommands({ local: true }, 'http://127.0.0.1:8777', 'server', 'code1', 'This computer');
    expect(local?.line).toContain('--url http://server:8765 --server-network tico_default');
  });
});

describe('history filter', () => {
  it("is the server's bot or person filter", () => {
    expect(historyFilter('bot:builder')).toBe('?bot=builder');
    expect(historyFilter('human:p2')).toBe('?person=p2');
    expect(historyFilter(undefined)).toBe('');
  });
});
