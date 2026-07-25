/** Zero-dependency lint: syntax-check every .js source with `node --check`. */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function collect(dir) {
  const entries = fs.existsSync(dir) ? fs.readdirSync(dir, { withFileTypes: true }) : [];
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => path.join(dir, entry.name));
}

const files = ["bin", "lib", "scripts", "test"].flatMap((dir) =>
  collect(path.join(root, dir)),
);

let failed = false;
for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (result.status !== 0) {
    failed = true;
    process.stderr.write(result.stderr || `syntax check failed: ${file}\n`);
  }
}

if (failed) {
  process.exit(1);
}
console.log(`Syntax OK: ${files.length} files.`);
