/* ui/app/credentials.js — the old Credentials page's address: the credentials now live in Library › Tools
   (ui/app/redesign-library.js), which opens on them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

function pageCredentials() {
  rdToolsOpen(RD_TOOLS + '?show=credentials');
}
