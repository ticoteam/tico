const form = document.querySelector('form');
const address = document.querySelector('#address');
const error = document.querySelector('#error');
const button = form.querySelector('button[type="submit"]');
const cancel = document.querySelector('#cancel');
const signin = document.querySelector('#signin');
const signinButton = document.querySelector('#signin-button');
const signinError = document.querySelector('#signin-error');
const useAddress = document.querySelector('#use-address');
const invoke = window.__TAURI__.core.invoke;
// The address form; with Tico sign-in it stays behind "Use a server address".
function showAddress() {
  form.hidden = false;
  useAddress.hidden = true;
  address.focus();
}
// First run (or after removing the last organization): connect a server. With an organization open,
// this page adds another one, so it starts empty and can be closed. Where the app keeps several
// organizations, signing in to Tico is the default and a typed address the other way.
invoke('setup_info').then(info => {
  if (info && info.signIn) {
    signin.hidden = false;
    signinButton.focus();
  } else {
    showAddress();
  }
  if (!info || !info.hasOrgs) return invoke('server_address').then(value => { address.value = value; });
  document.title = 'Add organization';
  document.querySelector('#heading').hidden = false;
  cancel.hidden = false;
}).catch(() => {
  showAddress();
  invoke('server_address').then(value => { address.value = value; }).catch(() => {});
});
cancel.addEventListener('click', () => invoke('cancel_setup').catch(() => {}));
useAddress.addEventListener('click', showAddress);
signinButton.addEventListener('click', async () => {
  signinError.textContent = '';
  signinButton.disabled = true;
  try {
    await invoke('tico_signin');
  } catch (message) {
    signinError.textContent = String(message);
  } finally {
    signinButton.disabled = false;
  }
});
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
