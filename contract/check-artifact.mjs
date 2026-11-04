// Compare a clean compile with the saved artifact, independent of git staging.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
const output = mkdtempSync(join(tmpdir(), "doclink-spec-"));
try {
  const result = spawnSync("npm", ["run", "compile", "--", "--output-dir", output], {
    cwd: root,
    stdio: "inherit",
  });
  if (result.status !== 0) {
    process.exitCode = result.status || 1;
  } else if (!readFileSync(join(output, "openapi.yaml")).equals(readFileSync(join(root, "openapi/openapi.yaml")))) {
    console.error("OpenAPI artifact is stale: run make spec");
    process.exitCode = 1;
  } else {
    console.log("OpenAPI artifact matches TypeSpec");
  }
} finally {
  rmSync(output, { recursive: true, force: true });
}
