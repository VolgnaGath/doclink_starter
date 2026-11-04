// Isolated live HTTP/Chromium acceptance. Never loads the main .env.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";

const base = "http://localhost:18000";
const mock = "http://localhost:18001";
const output = "docs/reports/stage7-browser-2026-11-04";
const browser = await chromium.launch({ headless: true });
const pause = () => new Promise(resolve => setTimeout(resolve, 1000));
async function until(fn, label) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      if (!/ECONNRESET|ECONNREFUSED|socket hang up|fetch failed/.test(error.message)) throw error;
    }
    await pause();
  }
  throw new Error(`Timeout: ${label}`);
}
const make = target => execFileSync("make", [target], { stdio: "inherit" });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await until(async () => (await fetch(base + "/health")).ok, "API startup");
  await page.goto(base + "/login");
  await page.locator('[name="username"]').fill("admin");
  await page.locator('[name="password"]').fill("verification-password");
  await Promise.all([page.waitForURL(base + "/"), page.locator('button[type="submit"]').click()]);
  const get = async path => {
    const response = await context.request.get(base + path);
    assert.equal(response.status(), 200);
    return response.json();
  };
  const before = await until(async () => {
    const docs = await get("/api/v1/documents?source=mock1c");
    if (docs.total !== 2) return false;
    const incoming = docs.items.find(d => d.filename === "invoice.txt");
    const detail = await get(`/api/v1/documents/${incoming.id}`);
    if (detail.result?.outcome === "error") throw new Error(detail.result.error);
    return detail.result?.outcome === "recommendation" ? detail : false;
  }, "automatic import/extract/embed/link");
  assert.equal(before.links.length, 0);
  const id = before.document.id;
  const journal = async () => (await fetch(mock + "/journal")).json();
  assert.equal((await journal()).events.length, 0);
  await page.goto(`${base}/review/${id}`);
  await page.getByRole("button", { name: "Подтвердить рекомендацию", exact: true }).click();
  await until(async () => (await journal()).links.length === 1, "create receipt");
  await page.goto(`${base}/integration?document_id=${id}`);
  await page.screenshot({ path: output + "-confirmed.png" });
  make("accept-integration-disconnect");
  await page.goto(`${base}/review/${id}`);
  await page.getByRole("button", { name: "Отменить связь", exact: true }).click();
  await until(async () => {
    const response = await context.request.get(`${base}/integration?document_id=${id}`);
    return (await response.text()).includes("ConnectError");
  }, "observed delivery outage");
  const removed = await get(`/api/v1/documents/${id}`);
  assert(removed.links[0].removed_at, "Local decision survives network failure");
  make("accept-integration-connect");
  await until(async () => {
    const saved = await journal();
    return saved.events.length === 2 && saved.links.length === 0;
  }, "remove receipt after outage");
  make("accept-integration-restart");
  await page.goto(base + "/integration");
  await page.getByRole("button", { name: "Запросить опрос worker", exact: true }).click();
  await pause();
  await pause();
  const final = await get(`/api/v1/documents/${id}`);
  assert.equal(final.result.id, before.result.id);
  assert.equal((await get("/api/v1/documents?source=mock1c")).total, 2);
  const finalJournal = await journal();
  assert.equal(finalJournal.events.length, 2);
  assert.equal(finalJournal.links.length, 0);
  await page.goto(base + "/integration");
  await page.screenshot({ path: output + ".png" });
  assert.deepEqual(errors, []);
  const report = { browser: browser.version(), extractor: "real regex (not LLM)",
    embedder: "real BGE-M3 CPU", isolated: true, document_id: id, result_id: final.result.id,
    automatic_pipeline: "passed", confirmation: "passed", outage_local_removal: "passed",
    reconnect_delivery: "passed", restart_repeat_no_duplicates: "passed",
    journal: finalJournal, page_errors: errors };
  await writeFile(output + ".json", JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
