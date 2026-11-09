// Shared DOM helpers for classic (non-module) scripts. ui/index.html loads this file before the scripts that call it.
'use strict';

// The element a DOM event happened on, or null when its target is not an Element (a text node, the window or the document).
// Callers use `?.` on the result, so a non-Element target does nothing instead of throwing.
function eventElement(event: Event): Element | null {
  return event.target instanceof Element ? event.target : null;
}
