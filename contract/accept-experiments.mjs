// Real Chromium acceptance. Credentials remain in .env/process memory, never in artifacts.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

process.loadEnvFile(".env");
const version = process.env.ACCEPT_CORPUS_VERSION ?? "196ecb8f-fe6e-41c6-8785-a4faf69a6b1f";
const base = process.env.ACCEPT_BASE_URL ?? "http://localhost:8000";
const output = process.env.ACCEPT_WEB_OUTPUT ?? "docs/reports/stage6-browser-2026-11-04.json";
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${base}/login`);
  await page.locator('[name="username"]').fill(process.env.APP_USER ?? "admin");
  assert(process.env.APP_PASSWORD, "APP_PASSWORD required in .env");
  await page.locator('[name="password"]').fill(process.env.APP_PASSWORD);
  await Promise.all([page.waitForURL(base + "/"), page.locator('button[type="submit"]').click()]);
  await page.goto(`${base}/experiments`);
  await page.locator('[name="name"]').fill("Браузерная приёмка этапа 6");
  await page.locator('[name="corpus_version_id"]').selectOption(version);
  await page.locator('[name="mode"]').selectOption("matrix");
  await Promise.all([
    page.waitForURL(/\/experiments\/[0-9a-f-]{36}$/),
    page.getByRole("button", { name: "Запустить", exact: true }).click(),
  ]);
  const id = page.url().split("/").at(-1);
  const first = await (await context.request.get(`${base}/api/v1/experiments/${id}`)).json();
  assert(["queued", "running"].includes(first.status), "Observed queued/running progress");
  await page.screenshot({ path: output.replace(/\.json$/, "-progress.png") });
  let saved;
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const response = await context.request.get(`${base}/api/v1/experiments/${id}`);
    assert.equal(response.status(), 200);
    saved = await response.json();
    if (saved.status === "error") throw new Error(saved.error);
    if (saved.status === "done") break;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  assert.equal(saved.status, "done", "Matrix completed");
  assert.equal(saved.runs.length, 8);
  assert.equal(saved.progress, saved.total);
  await page.goto(`${base}/experiments/${id}`);
  await page.getByRole("heading", { name: "Браузерная приёмка этапа 6", exact: true }).waitFor();
  await page.screenshot({ path: output.replace(/\.json$/, ".png") });
  const downloads = {};
  for (const format of ["csv", "md", "png"]) {
    const promise = page.waitForEvent("download");
    await page.getByRole("link", { name: `Скачать ${format.toUpperCase()}`, exact: true }).click();
    const download = await promise;
    assert.equal(await download.failure(), null);
    const bytes = await readFile(await download.path());
    assert(bytes.includes(Buffer.from(saved.dataset_hash)), "All exports carry the dataset hash");
    if (format === "png") assert(bytes.subarray(0, 4).equals(Buffer.from([137, 80, 78, 71])));
    downloads[format] = { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
  }
  await page.locator('[name="run_ids"]').nth(0).check();
  await page.locator('[name="run_ids"]').nth(1).check();
  await Promise.all([
    page.waitForURL(/\/experiments\/compare\?/),
    page.getByRole("button", { name: "Сравнить выбранные", exact: true }).click(),
  ]);
  assert((await page.locator("body").innerText()).includes("test"));
  const response = await context.request.get(`${base}/openapi.json`);
  const schema = await response.json();
  const operations = Object.fromEntries(Object.entries(schema.paths).filter(([key]) => key.includes("/api/v1/experiments")));
  assert.equal(Object.keys(operations).length, 3);
  assert.deepEqual(errors, []);
  const report = { browser: "Chromium headless", browser_version: browser.version(),
    series_id: id, observed_initial_status: first.status, final_status: saved.status,
    progress: saved.progress, total: saved.total, dataset_hash: saved.dataset_hash,
    downloads, comparison: "passed", page_errors: errors,
    operations: Object.fromEntries(Object.entries(operations).map(([key, value]) => [key,
      Object.fromEntries(Object.entries(value).map(([method, operation]) => [method, Object.keys(operation.responses)]))])) };
  await writeFile(path.resolve(output), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
