// SPDX-License-Identifier: MIT
// Inherit only local model-provider configuration, never desktop tools or UI.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createRequire } = require('node:module');

function providerOverlay(yaml, text) {
  const rows = yaml.parse(text);
  const providers = {};
  function visit(entries) {
    for (const row of Array.isArray(entries) ? entries : []) {
      if (row.insert) visit(row.insert);
      if (row.disabled === true) continue;
      if ((row.id === 'llm-pi-ai' || row.name === '@deepseek-ai/dsh-llm-pi-ai') && row.config?.providers) {
        Object.assign(providers, row.config.providers);
      }
    }
  }
  visit(rows);
  return Object.keys(providers).length ? [{ id: 'llm-pi-ai', config: { providers } }] : [];
}

if (require.main === module) {
  try {
    const [cli, patch, configuredHome] = process.argv.slice(2);
    const yaml = createRequire(cli)('yaml');
    const home = configuredHome || process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
    const source = path.join(home, 'profiles', 'desktop', 'cordis.patch.yml');
    if (fs.existsSync(source)) {
      const overlay = providerOverlay(yaml, fs.readFileSync(source, 'utf8'));
      if (overlay.length) {
        const sdkSource = path.join(home, 'profiles', 'sdk', 'cordis.patch.yml');
        if (fs.existsSync(sdkSource)) {
          const sdk = providerOverlay(yaml, fs.readFileSync(sdkSource, 'utf8'));
          if (sdk.length) Object.assign(overlay[0].config.providers, sdk[0].config.providers);
        }
        // JSON is YAML: serialize data, never evaluate profile expressions.
        const generated = yaml.parse(fs.readFileSync(patch, 'utf8'));
        fs.writeFileSync(patch, JSON.stringify([...overlay, ...generated]), 'utf8');
      }
    }
  } catch {
    // Parsing diagnostics can contain credentials from the source line.
    console.error('Cannot read local DSH model-provider profile; check its YAML configuration.');
    process.exitCode = 1;
  }
}
module.exports = { providerOverlay };
