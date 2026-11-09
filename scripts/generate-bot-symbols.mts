#!/usr/bin/env node

// Regenerate the local bot SVGs and license-bearing sprite from Google's filled
// Material Symbols Rounded set, avoiding third-party font requests at runtime.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const revision = '27e9ef1dbeedc13d682fece4a58e1eda4cb0961a';
const icons = [
  'account_tree', 'analytics', 'balance', 'bug_report', 'calendar_add_on', 'campaign', 'concierge',
  'conversion_path', 'database_search', 'deployed_code_update', 'design_services', 'dns',
  'edit_document', 'event_available', 'finance_mode', 'flag_check', 'frame_inspect', 'group_add',
  'groups_3', 'handshake', 'handyman', 'home_work', 'inbox', 'lan', 'mail_shield', 'map',
  'mobile_code', 'movie_edit', 'partner_exchange', 'percent', 'person_add', 'person_check',
  'person_search', 'post', 'radar',
  'record_voice_over', 'reviews', 'robot_2', 'rocket_launch', 'route', 'schema',
  'sports_esports', 'stacked_email', 'strategy', 'support_agent', 'target', 'travel_explore',
];

const raw = `https://raw.githubusercontent.com/google/material-design-icons/${revision}`;
const get = async url => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.text();
};

const [license, downloaded] = await Promise.all([
  get(`${raw}/LICENSE`),
  Promise.all(icons.map(async icon => {
    const svg = await get(`${raw}/symbols/web/${icon}/materialsymbolsrounded/${icon}_24px.svg`);
    const viewBox = svg.match(/viewBox="([^"]+)"/)?.[1];
    const inner = svg.slice(svg.indexOf('<path'), svg.lastIndexOf('</svg>'));
    if (!viewBox || !inner.startsWith('<path')) throw new Error(`Could not parse ${icon}`);
    return {icon, svg, symbol: `  <symbol id="${icon}" viewBox="${viewBox}">${inner}</symbol>`};
  })),
]);

const output = `<svg xmlns="http://www.w3.org/2000/svg">
<metadata><![CDATA[
Google Material Symbols Rounded
Source revision: ${revision}
https://github.com/google/material-design-icons

${license.trim()}
]]></metadata>
${downloaded.map(item => item.symbol).join('\n')}
</svg>
`;

const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.resolve(here, '../ui/assets');
const target = path.join(assets, 'bot-symbols.svg');
const iconDir = path.join(assets, 'bot-symbols');
fs.mkdirSync(iconDir, {recursive: true});
fs.writeFileSync(target, output);
for (const item of downloaded) fs.writeFileSync(path.join(iconDir, `${item.icon}.svg`), item.svg);
console.log(`Wrote ${icons.length} symbols to ${target} and ${iconDir}`);
