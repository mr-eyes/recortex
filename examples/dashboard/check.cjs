// Optional Firefox interaction check for the files built by preview.py.
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { firefox } = require('playwright-core');

async function checkAttention(page) {
  const attention = page.getByRole('region', {name: 'Needs your attention'});
  assert(await attention.isVisible());
  assert.match(await attention.innerText(), /Wait for the project lead/);
  assert.match(await attention.getByRole('link', {name: 'Read the handoff on GitHub'}).getAttribute('href'), /#issuecomment-9110$/);
  const task = attention.locator('a[href="#work-task-3"]');
  await task.click();
  assert(await page.locator('#work-task-3').isVisible());
  await page.goBack();
  const failure = attention.getByRole('link', {name: 'Read the blocking failure'});
  await failure.focus();
  await page.keyboard.press('Enter');
  await page.locator('#m-9113').waitFor();
  assert.match(await page.locator('#m-9113').innerText(), /missing|reference data directory/);
  await page.goBack();
  await attention.waitFor();
}

async function checkDecisionReview(page, file) {
  await page.goto(file('software'));
  const group = page.locator('#impact-9701');
  await group.locator(':scope > summary').focus();
  await page.keyboard.press('Enter');
  assert(await group.getByRole('heading', {name: 'Explicit connections'}).isVisible());
  assert(await group.getByRole('heading', {name: 'Suggested connections'}).isVisible());
  assert.equal(await group.getByText('Step 3 in task #15').count(), 0);
  const step = group.locator('.impact-item').filter({has: page.getByRole('heading', {name: 'Step 2 in task #15'})});
  await step.getByText('What am I missing?', {exact: true}).click();
  const prompt = step.locator('details[open]');
  assert.match(await prompt.locator('code').textContent(), /What am I missing.*impact-9701, item [a-f0-9]{64}/);
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      writeText: async text => { window.copiedQuestion = text; }
    }});
  });
  await prompt.getByRole('button', {name: 'Copy question'}).click();
  assert.equal(await page.evaluate(() => window.copiedQuestion), await prompt.locator('code').textContent());
  await group.locator('a[data-m="9703"]').click();
  await page.locator('#m-9703').waitFor();
  await page.goBack();
  assert(await step.isVisible());
  await group.getByRole('link', {name: 'See review history'}).click();
  const history = page.locator('#history-impact-9701');
  assert(await history.getByText('Suggested connections').isVisible());
  const item = history.locator('.impact-item').filter({has: page.getByRole('heading', {name: 'Review inputs', exact:true})});
  await item.getByRole('link', {name:'Review change'}).click();
  await page.getByRole('heading', {name:'Review inputs', exact:true}).waitFor();
  const evidence = await page.locator('#evidence').boundingBox();
  const judgment = await page.locator('#judgment').boundingBox();
  if (page.viewportSize().width >= 960) {
    assert(judgment.x >= evidence.x + evidence.width - 1 && judgment.y < evidence.y + evidence.height,
      'Desktop review should place evidence and judgment next to each other');
  } else {
    assert(judgment.y >= evidence.y + evidence.height - 1,
      'Narrow review should place the judgment below its evidence');
  }
  await page.getByText('Needs an update', {exact:false}).first().click();
  await page.getByRole('button', {name:'Preview review'}).click();
  await page.getByRole('heading', {name:'Before', exact:true}).waitFor();
  assert.match(await page.locator('#detail').textContent(), /comment:9701[\s\S]*comment:9703/);
  assert(await page.getByRole('button', {name:'Apply reviewed change'}).isHidden());
  await page.getByRole('button', {name:'Back to editing'}).click();
  await page.getByLabel('Reason or correction').fill('Keep the citation current.');
  await page.getByRole('button', {name:'Preview review'}).click();
  await page.getByRole('button', {name:'Save shared review'}).click();
  await page.getByRole('button', {name:'Apply reviewed change'}).click();
  await page.getByRole('heading', {name:'Change verified'}).waitFor();
  assert.match(await page.locator('#current').textContent(), /Applied and verified.*Keep the citation current/);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole('link', {name:'Back to History'}).click();
  assert(await history.getByText('Explicit connections').isVisible());
  const next = history.locator('.impact-item').filter({has: page.getByRole('heading', {name:'Step 2 in task #15'})});
  await next.getByRole('link', {name:'Review change'}).click();
  await page.getByLabel(/Unrelated/).check();
  await page.getByRole('button', {name:'Preview review'}).click();
  await page.getByRole('button', {name:'Save shared review'}).click();
  assert.match(await page.locator('#current').innerText(), /Unrelated/);
  assert(await page.getByRole('button', {name:'Apply reviewed change'}).isHidden());
}

async function checkTextReview(page, file) {
  for (const name of ['software', 'research', 'manuscript']) {
    await page.goto(file(name) + '#history-impact-9701');
    const taskGoal = page.locator('#history-impact-9701 .impact-item').filter({has: page.getByRole('heading', {name:'Goal in task #15', exact:true})});
    assert(await taskGoal.getByText(/policy/i).first().isVisible());
    const row = page.locator('#history-impact-9701 .impact-item').filter({
      has:page.getByRole('heading', {name: name === 'software' ? 'Documentation · #16' : 'Methods · #16', exact:true})
    });
    await row.getByRole('link', {name:'Review change'}).click();
    await page.getByLabel(/Needs an update/).check();
    await page.getByRole('button', {name:'Preview review'}).click();
    if (name === 'manuscript') {
      await page.getByRole('heading', {name:'Request for the project chat'}).waitFor();
      assert.match(await page.locator('#detail').innerText(), /issues\/16#issuecomment-9701/);
    } else {
      await page.getByRole('heading', {name:'Before', exact:true}).waitFor();
      assert.match(await page.locator('#detail').innerText(), /9701[\s\S]*9703/);
    }
    await page.getByRole('button', {name:'Save shared review'}).click();
    const apply = page.getByRole('button', {name:'Apply reviewed change'});
    assert.equal(await apply.isVisible(), name !== 'manuscript');
    if (name !== 'manuscript') {
      await apply.click();
      await page.getByRole('heading', {name:'Change verified'}).waitFor();
    }
  }
}

async function checkHandoff(page) {
  const originalUrl = page.url();
  const row = page.locator('#now .next > li').filter({ hasText: 'Step 2: Rerun the conformance set' });
  assert(await row.getByText('Completed since handoff', { exact: true }).isVisible());
  assert(await row.getByText(/9 of 12 quoted-newline files passed/).isVisible());
  assert(await row.getByText('Next open step', { exact: true }).isVisible());
  const next = row.getByRole('link', { name: 'Investigate the three remaining quoted-newline failures' });
  assert(await next.isVisible());
  assert.equal(await next.getAttribute('href'), '#work-step-7-9112-1');

  const details = row.locator('details').filter({ has: page.locator('summary', { hasText: 'Read checkpoint' }) });
  const toggle = details.locator('summary');
  const source = details.locator('a[href$="#issuecomment-9111"]');
  assert.equal(await details.evaluate(element => element.open), false);
  await toggle.focus();
  await page.keyboard.press('Enter');
  assert(await source.isVisible());
  assert.match(await details.innerText(), /All 12 files ran and 9 passed/);
  assert.match(await details.innerText(), /three failures involve carriage returns inside quoted fields/);
  await toggle.click();
  assert.equal(await details.evaluate(element => element.open), false);
  await toggle.click();
  assert(await source.isVisible());
  assert.equal(page.url(), originalUrl);
  assert.equal(await page.locator('html').getAttribute('data-view'), 'now');
  await toggle.click();
}

async function checkReturnToSource(page, url, area, view) {
  for (const method of ['Close', 'Back']) {
    await page.goto(url + '#' + area);
    await page.locator('html[data-view="' + view + '"]').waitFor();
    const source = page.locator('#' + view + ' [data-m]:visible').last();
    await source.evaluate(link => link.scrollIntoView({ block: 'center' }));
    await source.focus();
    const before = await page.evaluate(() => scrollY);
    assert(before > 100, area + ' needs a source below the fold');
    const memory = await source.getAttribute('data-m');
    await page.keyboard.press('Enter');
    await page.waitForURL(location => location.hash === '#m-' + memory);
    if (method === 'Close') await page.locator('#close').click();
    else await page.goBack();
    await page.waitForURL(location => location.hash === '#' + area);
    await page.waitForFunction(link => document.activeElement === link, await source.elementHandle());
    const after = await page.evaluate(() => scrollY);
    assert(Math.abs(after - before) <= 3, method + ' lost the reading position in ' + area);
  }
}

async function checkProjectTopics(page, url, direct = false) {
  await page.goto(url + (direct ? '#threads' : '#history'));
  if (!direct) await page.locator('#history').getByRole('link', { name: 'Topic stories' }).click();
  const topics = page.locator('#threads nav.story-topics');
  const links = topics.locator('a[href^="#thread-"]');
  await links.first().waitFor();
  assert.equal(await links.first().getAttribute('href'), '#thread-14');
  assert.match(await links.first().innerText(), /backups/i);
  assert.equal(await topics.locator('a[href^="#sec-"]').count(), 0);
  assert.equal(await page.locator('#threads .thread:visible').count(), 0);

  await links.first().focus();
  await page.keyboard.press('Enter');
  const story = page.locator('#thread-14');
  await story.waitFor();
  assert(await topics.isHidden());
  assert.equal(await page.locator('#threads .thread:visible').count(), 1);
  assert.match(await story.locator('h3').first().innerText(), /1 open task/i);
  assert.match(await story.innerText(), /three field logger exports saved in nested folders/);
  await story.locator('a[data-m="9402"]:visible').first().click();
  const result = page.locator('#m-9402');
  await result.waitFor();
  assert.match(await result.innerText(), /checks\/restore-manifest\.tsv/);
  await page.goBack();
  await page.waitForURL(location => location.hash === '#thread-14');
  await page.goBack();
  await page.waitForURL(location => location.hash === '#threads');
  await topics.waitFor();
  assert.equal(await page.locator('#threads .thread:visible').count(), 0);
  await page.waitForFunction(link => document.activeElement === link, await links.first().elementHandle());
  await links.first().click();
  await story.getByRole('link', { name: 'All topic stories' }).click();
  await topics.waitFor();
  await page.waitForFunction(link => document.activeElement === link, await links.first().elementHandle());
}

async function checkCopyHandoff(page, file) {
  await page.goto(file('software'));
  const copy = page.getByRole('button', {name: 'Copy handoff', exact: true});
  const preview = page.locator('#handoff-preview');
  const content = page.locator('#handoff-text');
  const reading = await content.textContent();
  assert.match(reading, /Wait for the project lead before tagging the release/);
  assert.doesNotMatch(reading, /#issuecomment-/);
  assert.match(await content.getByText('Read the completion record', {exact: true}).getAttribute('href'), /#issuecomment-9111$/);
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      writeText: async text => { window.copiedHandoff = text; }
    }});
  });
  await copy.focus();
  await copy.press('Enter');
  await page.getByText('Handoff copied.', {exact: true}).waitFor();
  const brief = await page.evaluate(() => window.copiedHandoff);
  assert.match(brief, /Wait for the project lead before tagging the release/);
  assert.match(brief, /Completed since handoff[^\n]*9 of 12[^\n]*\nhttps:\/\/github\.com\/[^\n]*#issuecomment-9111/);
  assert.match(brief, /Open step[^\n]*three remaining[^\n]*\nhttps:\/\/github\.com\/[^\n]*#issuecomment-9112/);
  assert.equal(await preview.getAttribute('open'), null);
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error('unavailable'); }; });
  await copy.click();
  const manual = page.locator('#handoff-manual-copy');
  await manual.waitFor();
  assert.equal(await page.evaluate(() => window.getSelection().toString()), brief);
  assert(await manual.evaluate(node => node === document.activeElement));
  await preview.locator('summary').click();
  await preview.locator('summary').click();
  await content.waitFor();
  assert.equal(await content.textContent(), reading);
  await page.goto(file('sparse'));
  assert.equal(await copy.count(), 0);
}

async function checkOutputViews(page, file) {
  const switcher = page.getByRole('navigation', {name: 'Output views', exact: true});
  const sources = switcher.getByRole('link', {name: 'Current text and sources', exact: true});
  const reader = switcher.getByRole('link', {name: 'Read the manuscript', exact: true});
  const outputs = page.locator('a[data-nav="outputs"]');

  async function checkSelected(link, view) {
    await page.locator('html[data-view="' + view + '"]').waitFor();
    assert(await switcher.isVisible());
    assert.equal(await switcher.locator('[aria-current]:not([aria-current="false"])').count(), 1);
    assert.match(await link.getAttribute('aria-current') || '', /^(?:location|page|true)$/);
    assert.equal(await outputs.getAttribute('aria-current'), 'page');
    assert(await page.locator('#' + view).isVisible());
    assert(await page.locator(view === 'text' ? '#paper' : '#text').isHidden());
  }

  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({width, height: 850});
    await page.goto(file('manuscript'));
    await outputs.click();
    await checkSelected(sources, 'text');
    assert.equal(await sources.getAttribute('href'), '#outputs');
    assert.equal(await reader.getAttribute('href'), '#paper');
    const links = [sources, reader];
    const nodes = await Promise.all(links.map(link => link.elementHandle()));
    const before = await Promise.all(links.map(link => link.boundingBox()));
    async function checkPosition() {
      for (const [index, link] of links.entries()) {
        const box = await link.boundingBox();
        assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= width && box.y + box.height <= 850);
        assert(Math.abs(box.x - before[index].x) <= 1 && Math.abs(box.y - before[index].y) <= 1,
          'Output view link moved when switching views at ' + width + 'px');
      }
    }
    await reader.click();
    await checkSelected(reader, 'paper');
    assert(await nodes[1].evaluate(link => document.activeElement === link));
    await checkPosition();
    await sources.focus();
    await page.keyboard.press('Enter');
    await checkSelected(sources, 'text');
    assert(await nodes[0].evaluate(link => document.activeElement === link));
    await checkPosition();
    await page.goBack();
    await page.waitForURL(url => url.hash === '#paper');
    await checkSelected(reader, 'paper');
  }
  await page.setViewportSize({width: 1280, height: 850});
  for (const project of ['software', 'research', 'sparse']) {
    await page.goto(file(project) + '#outputs');
    assert.equal(await page.locator('a[href="#paper"]').count(), 0, project);
  }
  await page.goto(file('manuscript') + '#paper');
  await checkSelected(reader, 'paper');
}

async function checkMapNavigation(page, file) {
  for (const width of [320, 1280]) {
    await page.setViewportSize({width, height: 850});
    for (const project of ['software', 'research', 'manuscript', 'sparse']) {
      await page.goto(file(project));
      await page.locator('html[data-view="now"]').waitFor();
      const nav = page.getByRole('navigation', {name: 'Project areas'});
      const links = nav.locator('a[data-nav]');
      const tab = nav.getByRole('link', {name: 'Project Map', exact: true, includeHidden: true});
      const map = page.locator('#project-map');
      assert.equal(await tab.count(), 1, project);
      assert.equal(await tab.getAttribute('data-nav'), 'project-map');
      assert(await tab.isVisible(), project + ' overview at ' + width);
      const bounds = await tab.boundingBox();
      assert(bounds.x >= 0 && bounds.x + bounds.width <= width, project + ' map tab extends beyond the viewport');
      if (width === 320) {
        const tools = page.getByRole('button', {name: 'Project tools', exact: true});
        assert.equal(await tools.getAttribute('aria-expanded'), 'false');
        for (const expanded of ['true', 'false']) {
          await tools.click();
          assert.equal(await tools.getAttribute('aria-expanded'), expanded);
          assert.equal(await nav.locator('a[data-nav]:visible').count(), await links.count());
        }
      }
      await tab.click();
      await map.getByRole('heading', {name: 'Project Map', exact: true}).waitFor();
      assert.equal(new URL(page.url()).hash, '#project-map');
      assert.equal(await tab.getAttribute('aria-current'), 'page');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
             project + ' map overflows at ' + width);
      if (project === 'sparse') assert(await map.getByText('No branches yet.', {exact: true}).isVisible());
      else assert(await map.locator('.branch-outline').isVisible());
    }
  }
}

async function checkMap(page, url) {
  await page.goto(url + '#project-map');
  const map = page.locator('#project-map'), outline = map.locator('.branch-outline');
  await map.waitFor();
  await map.getByRole('button', {name:'Outline', exact:true}).click();
  assert.equal(await map.getByRole('button', {name:'Outline', exact:true}).getAttribute('aria-pressed'), 'true');
  await map.getByRole('button', {name:'Branch map', exact:true}).click();
  const search = map.getByLabel('Find a branch');
  const backups = outline.getByRole('link', {name: 'Logger export backups'});
  assert(await backups.isHidden());
  await search.fill('checksum');
  await backups.waitFor();
  assert.equal(await map.locator('#branch-matches').innerText(), '1 match');
  const operations = outline.getByRole('link', {name: 'Field operations'});
  await operations.focus();
  await page.keyboard.press('ArrowLeft');
  assert(await backups.isHidden());
  await page.keyboard.press('ArrowRight');
  await backups.waitFor();
  await page.keyboard.press('ArrowRight');
  assert(await backups.evaluate(link => link === document.activeElement));
  await page.keyboard.press('Enter');
  let detail = map.locator('.branch-detail.selected');
  await detail.getByRole('heading', {name: 'Logger export backups'}).waitFor();
  await page.waitForFunction(() => document.activeElement.matches('.branch-detail.selected'));
  const branch = new URL(page.url()).hash;
  await detail.locator('a[data-m="9402"]').click();
  await page.locator('#m-9402').waitFor();
  await page.goBack();
  await detail.getByRole('heading', {name: 'Logger export backups'}).waitFor();
  assert.equal(await search.inputValue(), 'checksum');
  await page.locator('a[data-nav=history]').click();
  await page.locator('a[data-nav=project-map]').click();
  await page.waitForURL(target => target.hash === branch);
  await map.getByText('Try renaming a sample branch').click();
  await map.getByLabel('New branch name').fill('Logger recovery drills');
  await map.getByRole('button', {name: 'Preview rename'}).click();
  assert.match(await map.locator('#branch-sample-status').innerText(), /Logger export backups.*Logger recovery drills/);
  await map.getByRole('button', {name: 'Apply to sample'}).click();
  await detail.getByRole('heading', {name: 'Logger recovery drills'}).waitFor();
  assert.equal(new URL(page.url()).hash, branch);
  await page.reload();
  await detail.getByRole('heading', {name: 'Logger export backups'}).waitFor();
  await page.waitForFunction(() => document.activeElement.matches('.branch-detail.selected'));
  await page.setViewportSize({width: 390, height: 820});
  await page.goto(url + branch);
  await detail.getByRole('heading', {name: 'Logger export backups'}).waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.setViewportSize({width: 1280, height: 850});
}

async function checkResponsiveWidth(page, file) {
  for (const [route, view] of [['now', 'now'], ['work', 'todo'], ['history', 'history'],
                               ['outputs', 'text'], ['project-map', 'project-map']]) {
    await page.goto(file('manuscript') + '#' + route);
    const panel = page.locator('#' + view);
    await panel.waitFor();
    await page.setViewportSize({width: 1280, height: 850});
    const laptop = await panel.boundingBox();
    await page.setViewportSize({width: 1920, height: 850});
    const desktop = await panel.evaluate(el => {
      const main = el.closest('main'), style = getComputedStyle(main);
      return {
        panel: el.getBoundingClientRect().width,
        available: main.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      };
    });
    assert(desktop.panel > laptop.width * 1.2, route + ' should expand beyond its laptop width');
    assert(desktop.panel > desktop.available * .75, route + ' should use most of the desktop workspace');
    await page.setViewportSize({width: 320, height: 800});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), route + ' overflows on a phone');
  }
  await page.goto(file('manuscript') + '#paper');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Manuscript overflows on a phone');
  await page.setViewportSize({width: 1920, height: 850});
  const prose = page.locator('#paper .ptx:visible').first();
  await prose.waitFor();
  assert(await prose.evaluate(el => el.clientWidth / parseFloat(getComputedStyle(el).fontSize) < 44),
    'Manuscript prose should keep a readable line length on a wide screen');
  await page.setViewportSize({width: 1280, height: 850});
}

async function check(folder) {
  const browser = await firefox.launch({ executablePath: process.env.PLAYWRIGHT_FIREFOX_EXECUTABLE });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 850 } });
    const errors = [], externalRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (/^https?:/.test(request.url())) externalRequests.push(request.url());
    });
    const file = name => pathToFileURL(path.resolve(folder, name + '.html')).href;
    await checkResponsiveWidth(page, file);
    await checkDecisionReview(page, file);
    await checkTextReview(page, file);
    await checkCopyHandoff(page, file);
    await checkMapNavigation(page, file);
    await page.goto(file('software'));
    assert.deepEqual(await page.locator('a[data-nav]').allTextContents(), ['Overview', 'Project Map', 'Work', 'History', 'Outputs']);
    await checkAttention(page);
    await checkHandoff(page);
    await page.locator('a[data-nav=work]').click();
    await page.locator('#work-step-7-9108-2').waitFor();
    await page.locator('a[data-nav=history]').click();
    await page.locator('#history-filter-label').click();
    await page.locator('#history-query').fill('splitter');
    await page.locator('#history-kind').selectOption('decision');
    const decision = page.locator('#history-9105 a[data-m="9105"]').first();
    await decision.focus();
    await page.keyboard.press('Enter');
    await page.locator('#m-9105').waitFor();
    await page.goBack();
    assert.equal(await page.locator('#history-query').inputValue(), 'splitter');
    assert.equal(await page.locator('#history-kind').inputValue(), 'decision');
    assert(await decision.evaluate(element => document.activeElement === element));
    await page.locator('#history-reset').click();
    await page.locator('#history-clock').selectOption('event');
    await page.locator('#history-list .history-row:visible').first().waitFor();

    await checkMap(page, file('research'));
    await checkProjectTopics(page, file('manuscript'));
    await checkOutputViews(page, file);
    assert.doesNotMatch(await page.locator('#paper').innerText(), /field logger backups|restore-manifest/i);
    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 850});
      const mapLink = page.locator('#paper header').getByRole('link', {name: 'Project Map', exact: true});
      assert(await mapLink.isVisible());
      await mapLink.click();
      await page.locator('html[data-view="project-map"]').waitFor();
      await page.goBack();
      await page.locator('html[data-view="paper"]').waitFor();
    }
    await page.setViewportSize({width: 1280, height: 850});
    await page.goto(file('sparse') + '#project-map');
    const emptyMap = page.locator('#project-map');
    assert.match(await emptyMap.innerText(), /No branches yet/);
    assert.match(await emptyMap.innerText(), /recortex:map.*goals/);
    await page.goto(file('sparse'));
    assert.equal(await page.getByRole('region', {name: 'Needs your attention'}).count(), 0);
    const start = page.locator('#now section').filter({has: page.getByRole('heading', {name: 'Start your project record'})});
    assert.match(await start.innerText(), /Catch up the record/);
    assert.equal(await start.getByRole('link', {name: 'Project Map'}).getAttribute('href'), '#project-map');
    assert.equal(await page.locator('a[data-nav=outputs]').count(), 0);
    assert(await page.getByText('No pause has been recorded yet.', { exact: false }).first().isVisible());
    await page.locator('#tourstart').click();
    for (const view of ['now', 'todo', 'history', 'ask']) {
      await page.locator('html[data-view="' + view + '"]').waitFor();
      await page.locator('#tournext').click();
    }
    assert(await page.locator('#tour').isHidden());

    await page.setViewportSize({ width: 1250, height: 420 });
    await checkReturnToSource(page, file('software'), 'work', 'todo');
    await checkReturnToSource(page, file('manuscript'), 'outputs', 'text');

    await page.setViewportSize({ width: 320, height: 750 });
    await checkDecisionReview(page, file);
    await page.goto(file('software'));
    await checkAttention(page);
    await checkHandoff(page);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('a[data-nav=history]').click();
    await page.locator('html[data-view=history]').waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await page.locator('#history-list .history-row:visible').first().isVisible());
    await checkProjectTopics(page, file('manuscript'), true);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    assert.deepEqual(externalRequests, []);

    const staticPage = await browser.newPage({ javaScriptEnabled: false });
    await staticPage.goto(file('software'));
    await staticPage.getByText('Read handoff text', {exact: true}).click();
    assert(await staticPage.locator('#handoff-text').isVisible());
    const checkpoint = staticPage.locator('#now .handoff-update details');
    await checkpoint.locator('summary').click();
    assert(await checkpoint.locator('a[href$="#issuecomment-9111"]').isVisible());
    await staticPage.goto(file('software') + '#history');
    assert(await staticPage.locator('#history-9105').isVisible());
    await staticPage.setViewportSize({width: 320, height: 750});
    await staticPage.goto(file('research') + '#project-map');
    const staticMap = staticPage.locator('#project-map');
    const leafLink = staticMap.locator('.branch-outline').getByRole('link', {name: 'Wet-corner plots'});
    const leafAddress = await leafLink.getAttribute('href');
    await leafLink.click();
    await staticPage.waitForURL(url => url.hash === leafAddress);
    assert(await staticMap.locator(leafAddress).getByRole('heading', {name: 'Wet-corner plots'}).isVisible());
    assert(await staticPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await staticPage.goto(file('manuscript') + '#threads');
    await staticPage.locator('#threads nav.story-topics a[href="#thread-14"]').click();
    await staticPage.waitForURL(location => location.hash === '#thread-14');
    assert(await staticPage.locator('#thread-14 a[data-m="9402"]').first().isVisible());
  } finally {
    await browser.close();
  }
}

async function checkEditor() {
  const browser = await firefox.launch({executablePath: process.env.PLAYWRIGHT_FIREFOX_EXECUTABLE});
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.DASHBOARD_TEST_ADDRESS);
    await page.waitForURL(url => url.pathname.endsWith('/snapshot'));
    await page.locator('[data-nav="project-map"]').click();
    await page.getByRole('link', {name: 'Edit project map'}).click();
    const form = page.locator('#map-change');
    await form.waitFor();
    async function save() {
      await form.getByRole('button', {name: 'Preview change'}).click();
      await page.locator('#preview').waitFor();
      await page.getByRole('button', {name: 'Apply to shared map'}).click();
      await page.waitForFunction(() => document.querySelector('#preview').hidden &&
        document.querySelector('#status').textContent.includes('saved in hub comment'));
    }
    const action = page.locator('#action'), branch = form.getByLabel('Branch', {exact: true});
    const title = form.getByLabel('Branch name', {exact: true});
    await title.fill('Parser'); await save();
    await title.fill('Validation'); await save();
    await action.selectOption('rename'); await branch.selectOption({label: 'Parser'});
    await title.fill('Reader'); await save();
    await action.selectOption('update'); await form.getByLabel('Description').fill('Read fields.'); await save();
    await action.selectOption('move'); await form.getByLabel('Parent').selectOption({label: 'Validation'}); await save();
    await action.selectOption('split');
    await form.locator('fieldset').nth(0).getByLabel('Name').fill('Inputs');
    await form.locator('fieldset').nth(1).getByLabel('Name').fill('Outputs'); await save();
    await action.selectOption('merge'); await branch.selectOption({label: 'Inputs · Reader'});
    await form.getByLabel('Merge into', {exact: true}).selectOption({label: 'Outputs · Reader'}); await save();
    await action.selectOption('delete'); await branch.selectOption({label: 'Reader · Validation'});
    await form.getByLabel('Descendants').selectOption('subtree'); await save();
    await action.selectOption('restore');
    await form.getByLabel('Removed branch').selectOption({label: 'Reader · Validation'}); await save();
    await page.getByRole('link', {name: 'Back to Project Map'}).click();
    await page.getByRole('heading', {name: 'Reader', exact: true}).waitFor();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}

async function checkLiveImpact() {
  const browser = await firefox.launch({executablePath: process.env.PLAYWRIGHT_FIREFOX_EXECUTABLE});
  try {
    const page = await browser.newPage({viewport:{width:390, height:820}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.DASHBOARD_TEST_ADDRESS);
    await page.waitForURL(url => url.pathname.endsWith('/snapshot'));
    await page.locator('[data-nav="now"]').click();
    const group = page.locator('#impact-9701');
    await group.locator(':scope > summary').click();
    const row = group.locator('.impact-item').filter({has:page.getByRole('heading', {name:'Review inputs', exact:true})});
    await row.getByRole('link', {name:'Review change'}).click();
    await page.getByLabel(/Needs an update/).check();
    await page.getByLabel('Reason or correction').fill('Keep the reviewed map citation current.');
    await page.getByRole('button', {name:'Preview review'}).click();
    await page.getByRole('button', {name:'Save shared review'}).click();
    const apply = page.getByRole('button', {name:'Apply reviewed change'});
    await apply.waitFor();
    assert.match(await page.locator('#current').textContent(), /Needs an update/);
    await page.reload();
    await page.getByRole('button', {name:'Reopen accepted change'}).click();
    await apply.click();
    await page.getByRole('heading', {name:'Change verified'}).waitFor();
    await page.getByRole('link', {name:'Back to decision in Overview'}).click();
    await page.locator('#impact-9701').getByRole('link', {name:'See review history'}).click();
    const log = page.locator('#history-impact-9701').getByText('Review history', {exact:true});
    await log.click();
    await page.locator('#history-impact-9701').getByText('Update verified', {exact:false}).last().waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

if (process.argv.length !== 3) {
  process.stderr.write('Usage: node check.cjs DIRECTORY_FROM_PREVIEW\n');
  process.exitCode = 2;
} else {
  const runners = {'--editor-fixture': checkEditor, '--impact-fixture': checkLiveImpact};
  const run = runners[process.argv[2]] ? runners[process.argv[2]]() : check(process.argv[2]);
  run.then(() => console.log('Dashboard browser checks passed.')).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
