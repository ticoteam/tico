const form = document.querySelector('form');
const address = document.querySelector('#address');
const error = document.querySelector('#error');
const button = form.querySelector('button[type="submit"]');
const cancel = document.querySelector('#cancel');
const invoke = window.__TAURI__.core.invoke;
// First run (or after removing the last organization): connect a server. With an organization open,
// this page adds another one, so it starts empty and can be closed.
invoke('setup_info').then(info => {
  if (!info || !info.hasOrgs) return invoke('server_address').then(value => { address.value = value; });
  document.title = 'Add organization';
  document.querySelector('#heading').hidden = false;
  cancel.hidden = false;
}).catch(() => invoke('server_address').then(value => { address.value = value; }).catch(() => {}));
cancel.addEventListener('click', () => invoke('cancel_setup').catch(() => {}));
form.addEventListener('submit', async event => {
  event.preventDefault();
  error.textContent = '';
  button.disabled = true;
  button.textContent = 'Connecting…';
  try {
    await invoke('connect_server', { address: address.value });
  } catch (message) {
    error.textContent = String(message);
  } finally {
    button.disabled = false;
    button.textContent = 'Connect';
  }
});
