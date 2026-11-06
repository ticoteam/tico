// Select a Settings group through the control visible at this viewport.
exports.selectSettingsGroup = async (page, group) => {
  await page.locator('.settings-layout').waitFor({state: 'visible'});
  const clearSearch = page.locator('[data-settings-search-close]');
  if (await clearSearch.isVisible()) await clearSearch.click();
  const picker = page.locator('#settings-group-pick');
  if (await picker.isVisible()) await picker.selectOption(group);
  else {
    const tab = page.locator(`#settings-tabs [data-settings-tab="${group}"]`);
    if (await tab.isVisible()) await tab.click();
    else {
      throw new Error(`Settings group controls are hidden at viewport ${await page.evaluate(() => innerWidth)}px`);
    }
  }
  await page.locator(`#settings-group-${group}`).waitFor({state: 'visible'});
};
