// Add company: a server address, checked by the app, or Tico Cloud sign-in.
const local = window.ticoLocal;
const form = document.getElementById('form');
const input = document.getElementById('server');
const error = document.getElementById('error');
const add = document.getElementById('add');
const cancel = document.getElementById('cancel');

local.context().then((ctx) => {
  if (ctx.prefill) input.value = ctx.prefill;
  cancel.hidden = !ctx.canCancel;
  input.focus();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  error.hidden = true;
  add.disabled = true;
  try {
    const result = await local.add(input.value);
    if (!result.ok) {
      error.textContent = result.error;
      error.hidden = false;
      input.focus();
    }
  } finally {
    add.disabled = false;
  }
});
cancel.addEventListener('click', () => local.cancel());
document.getElementById('signin').addEventListener('click', () => local.signIn());
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !cancel.hidden) local.cancel();
});
