import { chromium } from "playwright";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";

const base = "http://localhost:28000";
for (let attempt = 0; attempt < 90; attempt++) {
  try {
    if ((await fetch(base + "/health")).ok) break;
  } catch {}
  await new Promise(resolve => setTimeout(resolve, 1000));
}
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base + "/login");
  await page.locator('[name="username"]').fill("admin");
  await page.locator('[name="password"]').fill("verification-password");
  await Promise.all([page.waitForURL(base + "/"), page.locator('button[type="submit"]').click()]);
  const deadline = Date.now() + 300000;
  let invoice;
  while (Date.now() < deadline) {
    const docs = await (await context.request.get(base + "/api/v1/documents?source=mock1c")).json();
    const found = docs.items.find(d => d.filename === "invoice.txt");
    if (found) {
      invoice = await (await context.request.get(`${base}/api/v1/documents/${found.id}`)).json();
      if (invoice.result?.outcome === "error") throw new Error(invoice.result.error);
      if (invoice.result?.outcome === "recommendation") break;
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  assert.equal(invoice?.result?.outcome, "recommendation");
  assert.equal(invoice.links.length, 0);
  await page.goto(`${base}/review/${invoice.document.id}`);
  await page.screenshot({ path: "docs/reports/stage8-delivery-proposal-2026-11-04.png", fullPage: true });
  await page.getByRole("button", { name: "Подтвердить рекомендацию", exact: true }).click();
  let journal;
  for (let i = 0; i < 60; i++) {
    journal = await (await fetch("http://localhost:28001/journal")).json();
    if (journal.links.length === 1) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  assert.equal(journal.links.length, 1);
  await page.goto(`${base}/integration?document_id=${invoice.document.id}`);
  await page.screenshot({ path: "docs/reports/stage8-delivery-confirmed-2026-11-04.png", fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile("docs/reports/stage8-delivery-browser-2026-11-04.json", JSON.stringify({
    browser: browser.version(), source: "новый mock1c документ", extractor: "real regex, not LLM",
    document_id: invoice.document.id, result_id: invoice.result.id, confirmed_delivery: "passed", page_errors: errors,
  }, null, 2) + "\n");
} finally {
  await browser.close();
}
execFileSync("node", ["contract/accept-effect.mjs"], { stdio: "inherit", env: { ...process.env,
  ACCEPT_BASE_URL: base, APP_PASSWORD: "verification-password",
  ACCEPT_EFFECT_OUTPUT: "docs/reports/stage8-delivery-effect-2026-11-04" } });
