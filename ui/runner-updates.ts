/* How a computer stands against the server's release (backend/runner_versions.py): its version, whether it is
   up to date, updating, behind or too old to work with (its bots are paused), and the last update error.
   Health and Settings > Computers both draw it. An outdated computer (behind the server's release, or on no
   release at all) carries an Outdated badge whatever its state says. */
'use strict';
// `version`: what the computer itself reported; with one, "Release not reported" would contradict it, so an unknown state
// that is not outdated says nothing.
window.runnerUpdateHtml = function runnerUpdateHtml(update, version) {
  if (!update || !update.state || (update.state === 'unknown' && version && !update.outdated)) return '';
  const tone = {current: 'ok', updating: 'waiting', needs_update: 'waiting', incompatible: 'fail'}[update.state] || '';
  const detail = [update.release ? `Tico ${update.release}` : '', update.commit ? update.commit.slice(0, 7) : '', update.kind || '',
    update.update_state === 'pinned' ? 'pinned' : '']
    .filter(Boolean).map(esc).join(' · ');
  const target = update.state === 'needs_update' && update.target ? ` <span class="muted">to ${esc(update.target)}</span>` : '';
  const why = update.state === 'incompatible'
    ? `<div class="err" data-runner-paused>Needs Tico ${esc(update.min_runner)} or later; it takes no work until it updates.</div>` : '';
  const outdated = update.outdated && update.state !== 'incompatible'
    ? ` <span class="pill fail" data-runner-outdated title="${esc(`This server runs Tico ${update.server_release || ''}`)}">Outdated</span>` : '';
  const error = update.error ? `<div class="err" data-runner-error>Last update error: ${esc(update.error)}</div>` : '';
  return `<div class="runner-update" data-runner-update="${esc(update.state)}"><span class="pill ${tone}">${esc(update.label)}</span>`
    + `${outdated}${target} <span class="muted">${detail}</span>${why}${error}</div>`;
};

// The Computers page's banner: the online computers on older code than the server, and whose they are to update.
window.runnerOutdatedBanner = function runnerOutdatedBanner(machines, me, name, online) {
  const stale = (machines || []).filter(machine => machine.update?.outdated && online(machine));
  if (!stale.length) return '';
  const server = stale[0].update.server_release;
  const mine = stale.filter(machine => machine.owner_id === me);
  const list = stale.map(machine => `${esc(machine.label)} (${esc(machine.update.release || 'release unknown')}${machine.owner_id === me ? '' : machine.owner_id ? `, ${esc(name(machine.owner_id))}'s` : ', no owner'})`).join(', ');
  const ask = mine.length ? ` ${mine.length === 1 ? 'Yours updates' : 'Yours update'} by itself between turns; when it stays behind, its row says why.` : ' Their owners update them.';
  return `<div class="machine-outdated-banner" role="status" data-runner-outdated-banner>${stale.length === 1 ? 'A computer runs' : `${stale.length} computers run`} older code than this server (Tico ${esc(server)}): ${list}.${ask}</div>`;
};
