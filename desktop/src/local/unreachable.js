// Cannot reach <company>: the server did not answer. Try again reloads its page.
const local = window.ticoLocal;
local.context().then((ctx) => {
  if (ctx.host) document.getElementById('title').textContent = `Cannot reach ${ctx.host}.`;
  document.getElementById('retry').focus();
});
document.getElementById('retry').addEventListener('click', () => local.retry());
