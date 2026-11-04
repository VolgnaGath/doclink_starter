// Browser verification only. These scenario inputs are not human measurements.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

if (!process.env.ACCEPT_BASE_URL) process.loadEnvFile(".env");
const base = process.env.ACCEPT_BASE_URL ?? "http://localhost:8000";
const run = process.env.ACCEPT_EFFECT_RUN ?? "37a41963-0dce-4fbb-be64-94858a6f4a7a";
const output = process.env.ACCEPT_EFFECT_OUTPUT ?? "docs/reports/stage8-effect-browser-2026-11-04";
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base + "/login");
  await page.locator('[name="username"]').fill(process.env.APP_USER ?? "admin");
  await page.locator('[name="password"]').fill(process.env.APP_PASSWORD);
  await Promise.all([page.waitForURL(base + "/"), page.locator('button[type="submit"]').click()]);
  await page.goto(`${base}/effect?run_id=${run}`);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  await page.locator("#effect-result").waitFor();
  assert((await page.locator("#effect-result").innerText()).includes("нет данных"));
  await page.screenshot({ path: output + "-missing.png", fullPage: true });
  const assumptions = { recommendation_seconds: "60", review_seconds: "300", none_seconds: "420",
    error_seconds: "600", hourly_rate_rub: "600", extra_variable_rub: "0",
    infrastructure_rub: "1000", other_fixed_rub: "0", contract_labor_rub: "0",
    implementation_rub: "10000", v_prefill: "1000", v_decode: "30" };
  for (const [field, value] of Object.entries(assumptions)) {
    await page.locator(`[name="${field}"]`).fill(value);
    await page.locator(`[name="source_${field}"]`).fill("Демонстрационное допущение приёмки, не измерение");
  }
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  await page.locator("#effect-result").waitFor();
  const hash = await page.locator("#calculation-hash").innerText();
  const downloads = {};
  let cloud;
  for (const [label, format] of [["Скачать JSON", "json"], ["Скачать Markdown", "md"]]) {
    const pending = page.waitForEvent("download");
    await page.getByRole("button", { name: label, exact: true }).click();
    const download = await pending;
    const bytes = await readFile(await download.path());
    assert(bytes.includes(Buffer.from(hash)));
    if (format === "json") cloud = JSON.parse(bytes);
    await writeFile(output + "." + format, bytes);
    downloads[format] = bytes.length;
  }
  await page.screenshot({ path: output + "-cloud.png", fullPage: true });
  await page.locator('[name="mode"]').selectOption("server");
  for (const field of ["incoming_price_rub", "contract_price_rub"]) {
    await page.locator(`[name="${field}"]`).fill("0");
    await page.locator(`[name="source_${field}"]`).fill("Серверный сценарий: цена включена в инфраструктуру (допущение)");
  }
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  await page.locator("#effect-result").waitFor();
  assert.notEqual(await page.locator("#calculation-hash").innerText(), hash);
  await page.screenshot({ path: output + "-server.png", fullPage: true });
  assert.deepEqual(errors, []);
  const report = { browser: browser.version(), run_id: run, calculation_hash: hash,
    missing_data: "passed", cloud: "passed", server: "passed", downloads,
    result: cloud.result, page_errors: errors, label: "Только сценарные допущения, не замер человека" };
  await writeFile(output + "-audit.json", JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
