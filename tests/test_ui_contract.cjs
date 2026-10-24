// SPDX-License-Identifier: MIT
// Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
// Static contract checks for the page: assets exist, no request bypasses the
// base-path helper, and the page shows the harness' own balance instead of a
// Codex quota.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const web = path.join(root, 'web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');

// 1. Every local asset referenced by the page exists.
const assets = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]);
for (const asset of assets) {
  if (/^https?:/i.test(asset) || asset.startsWith('//')) continue;
  assert.ok(!asset.startsWith('/'), `absolute asset path ${asset} breaks a /dsh/ mount`);
  const file = path.join(web, asset.split('?')[0]);
  assert.ok(fs.existsSync(file), `missing asset ${asset}`);
}

// 2. The boot marker and the base-path helper come first.
assert.match(html, /<!-- bridge-boot -->/);
assert.ok(html.indexOf('bridge-client.js') < html.indexOf('app.js'), 'bridge-client.js must load before app.js');
assert.match(html, /DeepSeek Bridge/);
assert.match(html, /DeepSeek Harness/);

// 3. Every fetch in page scripts goes through the base-path helper.
const scripts = ['app.js', 'local-composer.js', 'interactions.js', 'voice-ui.js'].map(name => ({
  name,
  source: fs.readFileSync(path.join(web, name), 'utf8')
}));
for (const {name, source} of scripts) {
  const plain = source.match(/(?<![A-Za-z0-9_.])fetch\s*\(/g) || [];
  assert.equal(plain.length, 0, `${name} calls fetch() directly instead of bridgeFetch()`);
  const absolute = source.match(/(?<![A-Za-z0-9_.])fetch\s*\(\s*`?\/api/g) || [];
  assert.equal(absolute.length, 0, `${name} hard-codes /api without the base path`);
  assert.ok(source.includes('bridgeFetch') || name === 'bridge-client.js', `${name} does not use bridgeFetch`);
}

// 4. Balance, not quota: the old Codex surfaces are gone.
const allSource = ['index.html', 'app.js', 'interactions.js', 'local-composer.js', 'styles.css', 'balance.css']
  .map(name => fs.readFileSync(path.join(web, name), 'utf8'))
  .join('\n');
assert.doesNotMatch(allSource, /reset-credit|resetCredits|weeklyRemainingPercent|data-quota/i);
assert.match(allSource, /data-balance-primary/);
assert.match(allSource, /\/api\/balance/);

// 5. The logo menu exists, is keyboard reachable, and offers no dead jump.
assert.match(html, /id="provider-toggle"[^>]*aria-haspopup="menu"/);
assert.match(html, /id="provider-menu"[^>]*role="menu"/);
assert.match(html, /role="menuitem"[^>]*disabled[^>]*aria-disabled="true"/);
assert.match(html, /Codex/);

// 6. Optional voice: assets exist, the toggle is wired, no provider key ships.
assert.ok(fs.existsSync(path.join(web, 'voice-ui.js')), 'voice-ui.js missing');
assert.ok(fs.existsSync(path.join(web, 'voice.css')), 'voice.css missing');
assert.match(html, /id="voice-toggle"/);
assert.match(html, /id="settings-dialog"/);
for (const file of ['voice-ui.js', 'app.js']) {
  const source = fs.readFileSync(path.join(web, file), 'utf8');
  assert.doesNotMatch(source, /VOLC_API_KEY|X-Api-Key/, `${file} must not carry provider credentials`);
}

// 7. No remote script injection: the page loads only local scripts.
for (const asset of assets) {
  assert.ok(!/^https?:/i.test(asset), `remote asset ${asset} is not allowed`);
}

console.log('UI contract passed: assets, base path, balance, provider menu, voice');

// 8. PWA shell: manifest, icons, theme colour, and a cache policy that never
// touches API responses or page navigations.
const manifestPath = path.join(web, 'manifest.webmanifest');
assert.ok(fs.existsSync(manifestPath), 'manifest.webmanifest missing');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
assert.equal(manifest.display, 'standalone');
assert.equal(manifest.start_url, './');
assert.ok(Array.isArray(manifest.icons) && manifest.icons.length >= 2, 'manifest icons missing');
for (const icon of manifest.icons) {
  assert.ok(fs.existsSync(path.join(web, icon.src)), `missing manifest icon ${icon.src}`);
}
assert.match(html, /rel="manifest"/);
assert.match(html, /name="theme-color" content="#4d6bfe"/);

const sw = fs.readFileSync(path.join(web, 'sw.js'), 'utf8');
assert.match(sw, /'\/api\/'/, 'service worker must skip API requests');
assert.match(sw, /request\.mode === 'navigate'/, 'service worker must not cache navigations');

// 9. The palette is DeepSeek blue, not the upstream Codex green.
const palette = ['styles.css', 'balance.css', 'voice.css', 'provider-menu.css', 'local-polish.css']
  .map(name => fs.readFileSync(path.join(web, name), 'utf8'))
  .join('\n');
for (const green of ['#57d99a', '#248a67', '#64d9a4', '#7de2ae', '#173d2e', '#2f7f5c']) {
  assert.ok(!palette.toLowerCase().includes(green), `leftover Codex green ${green}`);
}
assert.match(palette, /#4d6bfe/i);

console.log('PWA and palette checks passed');

