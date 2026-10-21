// Imports the installed @liara/cli's own modules and prints JSON. Run via gen_catalog.py.
import { pathToFileURL } from 'node:url';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

const root = process.argv[2];
const lib = (p) => import(pathToFileURL(path.join(root, 'lib', p)).href);

const constants = await lib('constants.js');
const { default: supportedVersions } = await lib('utils/get-supported-versions.js');
const { getPort, getDefaultPort } = await lib('utils/get-port.js');
const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));

// liara.json per-platform option names, read from the CLI's type declarations.
const typesDir = path.join(root, 'lib', 'types');
const platformFields = {};
for (const f of readdirSync(typesDir).filter((n) => /^[a-z]+-plat\w*-config\.d\.ts$/.test(n))) {
  const name = f.split('-')[0];
  const body = readFileSync(path.join(typesDir, f), 'utf8');
  platformFields[name] = [...body.matchAll(/^\s+(\w+)\??:\s*([^;]+);/gm)].map((m) => ({
    name: m[1], type: m[2].trim(), optional: m[0].includes('?:'),
  }));
}
const topLevel = readFileSync(path.join(typesDir, 'liara-json.d.ts'), 'utf8');
const liaraJsonKeys = [...topLevel.matchAll(/^\s+'?([\w-]+)'?\??:/gm)].map((m) => m[1]);

const platforms = {};
for (const p of constants.AVAILABLE_PLATFORMS) {
  platforms[p] = {
    port: getPort(p) ?? getDefaultPort(p),
    versions: supportedVersions(p) ?? null,
    liara_json_options: platformFields[p] ?? [],
  };
}

const arrays = Object.fromEntries(
  Object.entries(constants).filter(([k, v]) => Array.isArray(v) && k !== 'AVAILABLE_PLATFORMS'),
);
console.log(JSON.stringify({
  cli_version: pkg.version,
  platforms,
  detect_only_platforms: ['wordpress'],
  liara_json_keys: liaraJsonKeys,
  constants: arrays,
  api: { regions: constants.REGIONS_API_URL, iran: constants.API_IR_URL, object_storage: constants.OBJECT_STORAGE_API_URL },
}, null, 2));
