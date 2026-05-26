const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const ROOT = path.resolve(__dirname, "..");
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (_error) {
  ({ chromium } = require(path.join(ROOT, "tmp", "pw", "node_modules", "playwright")));
}

const PROJECT_ID = process.env.VIBEMOTION_QA_PROJECT || "test-b5b1e836";
const URL = process.env.VIBEMOTION_QA_URL || `http://127.0.0.1:8010/app/index.html?project=${PROJECT_ID}&fresh=1`;
const PROJECT_JSON = path.join(ROOT, "projects", PROJECT_ID, "project.json");
const BACKUP_JSON = path.join(ROOT, "qa_artifacts", `project-backup-interactions-${Date.now()}.json`);
const ARTIFACT_DIR = path.join(ROOT, "output", "playwright");
fs.mkdirSync(ARTIFACT_DIR, { recursive: true });

function findInstalledChromium() {
  const root = path.join(os.homedir(), "AppData", "Local", "ms-playwright");
  if (!fs.existsSync(root)) return null;
  const candidates = fs.readdirSync(root)
    .filter((name) => name.startsWith("chromium_headless_shell-"))
    .sort()
    .reverse();
  for (const name of candidates) {
    const exe = path.join(root, name, "chrome-headless-shell-win64", "chrome-headless-shell.exe");
    if (fs.existsSync(exe)) return exe;
  }
  return null;
}

async function launchChromium() {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXE || findInstalledChromium();
  return chromium.launch(executablePath ? { headless: true, executablePath } : { headless: true });
}

async function waitLoaded(page) {
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForFunction(() => typeof project !== "undefined" && project?.motions?.length > 0, null, { timeout: 15000 });
  await page.waitForSelector(".motion-item", { timeout: 10000 });
  await page.waitForTimeout(800);
}

async function pickLtxTarget(page) {
  return page.evaluate(() => {
    const motion = project?.motions?.find((item) => item?.source_type === "figma" && Array.isArray(item.figma_layers));
    const layer = motion?.figma_layers?.find((item) =>
      item?.visible !== false &&
      item?.kind === "image" &&
      item?.asset_path &&
      !String(item?.id || "").startsWith("__frame_choreo_")
    );
    if (!motion || !layer) throw new Error("No current Figma image layer available for LTX QA");
    return { motionId: String(motion.id), layerId: String(layer.id) };
  });
}

async function ltxFailureUx(browser) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await page.route("**/api/projects/*/motion/*/figma-layer/ltx", async (route) => {
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "QA synthetic LTX failure" }),
    });
  });
  await waitLoaded(page);
  const target = await pickLtxTarget(page);
  await page.evaluate(({ motionId, layerId }) => openLtxPromptEditor(motionId, layerId), target);
  await page.fill("#ltxPrompt", "qa failure prompt");
  await page.click("#generateLtxPromptBtn");
  await page.waitForFunction(() => !document.querySelector("#ltxPreviewShell").classList.contains("generating"), null, { timeout: 5000 });
  const result = await page.evaluate(() => ({
    generating: document.querySelector("#ltxPreviewShell").classList.contains("generating"),
    label: document.querySelector("#generateLtxPromptBtn").textContent.trim(),
    disabled: document.querySelector("#generateLtxPromptBtn").disabled,
    logText: document.querySelector("#jobStatus")?.innerText || "",
  }));
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-ltx-failure-state.png`, fullPage: false });
  await page.close();
  return { name: "ltx-failure-ux", result, errors };
}

async function renderFailureUx(browser) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await page.route("**/api/projects/*/render", async (route) => {
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "QA synthetic render failure" }),
    });
  });
  await waitLoaded(page);
  await page.click("#downloadBtn");
  await page.waitForFunction(() => !document.querySelector("#downloadBtn").classList.contains("rendering"), null, { timeout: 5000 });
  const result = await page.evaluate(() => ({
    label: document.querySelector("#downloadBtn").textContent.trim(),
    rendering: document.querySelector("#downloadBtn").classList.contains("rendering"),
    disabled: document.querySelector("#downloadBtn").classList.contains("disabled"),
    logText: document.querySelector("#jobStatus")?.innerText || "",
  }));
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-render-failure-state.png`, fullPage: false });
  await page.close();
  return { name: "render-failure-ux", result, errors };
}

async function timelineDragAndTrim(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const apiCalls = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().includes(`/api/projects/${PROJECT_ID}/motion/`)) {
      apiCalls.push(request.url());
    }
  });
  await waitLoaded(page);
  const before = await page.evaluate(() => {
    const item = document.querySelector(".motion-item");
    const rect = item.getBoundingClientRect();
    const motion = project.motions[0];
    return { rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, viewportWidth: window.innerWidth, start: motion.start, duration: motion.duration };
  });
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-timeline-before.png`, fullPage: false });

  const dragStartX = Math.max(before.rect.x + 12, Math.min(before.rect.x + before.rect.width / 2, before.viewportWidth - 24));
  const dragY = before.rect.y + before.rect.height / 2;
  await page.mouse.move(dragStartX, dragY);
  await page.mouse.down();
  await page.mouse.move(dragStartX - 35, dragY, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(1300);

  const afterMove = await page.evaluate(() => {
    const motion = project.motions[0];
    return { start: motion.start, duration: motion.duration };
  });

  const trimRect = await page.locator(".motion-item").boundingBox();
  const trimStartX = Math.max(trimRect.x + 4, Math.min(trimRect.x + 4, before.viewportWidth - 24));
  const trimY = trimRect.y + trimRect.height / 2;
  await page.mouse.move(trimStartX, trimY);
  await page.mouse.down();
  await page.mouse.move(trimStartX + 45, trimY, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(1300);

  const afterTrim = await page.evaluate(() => {
    const item = document.querySelector(".motion-item");
    const rect = item.getBoundingClientRect();
    const motion = project.motions[0];
    return {
      start: motion.start,
      duration: motion.duration,
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      selected: document.querySelector(".motion-item.selected") !== null,
    };
  });
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-timeline-after-trim.png`, fullPage: false });
  await page.close();
  return { name: "timeline-drag-trim", before, afterMove, afterTrim, apiCalls };
}

async function main() {
  if (!fs.existsSync(PROJECT_JSON)) {
    throw new Error(`QA project not found: ${PROJECT_JSON}`);
  }
  fs.copyFileSync(PROJECT_JSON, BACKUP_JSON);
  const browser = await launchChromium();
  const results = [];
  try {
    results.push(await ltxFailureUx(browser));
    results.push(await renderFailureUx(browser));
    results.push(await timelineDragAndTrim(browser));
  } finally {
    await browser.close();
    fs.copyFileSync(BACKUP_JSON, PROJECT_JSON);
  }

  const failed = [];
  const ltx = results.find((item) => item.name === "ltx-failure-ux");
  if (ltx.result.generating) failed.push("LTX failure left spinner active");
  if (ltx.result.label !== "Generate preview") failed.push("LTX failure did not restore button label");
  if (ltx.result.disabled) failed.push("LTX failure left generate button disabled");
  if (!ltx.result.logText.includes("LTX generation failed to start")) failed.push("LTX failure was not logged");

  const render = results.find((item) => item.name === "render-failure-ux");
  if (render.result.rendering) failed.push("Render failure left download button rendering");
  if (render.result.label !== "Download") failed.push("Render failure did not restore download label");
  if (!render.result.logText.includes("Download render failed")) failed.push("Render failure was not logged");

  const timeline = results.find((item) => item.name === "timeline-drag-trim");
  if (!timeline.apiCalls.length) failed.push("Timeline drag/trim did not call motion update API");
  if (timeline.afterMove.start === timeline.before.start) failed.push("Timeline drag did not move the motion start");
  if (timeline.afterTrim.duration >= timeline.afterMove.duration) failed.push("Timeline trim-right did not reduce duration");
  if (!timeline.afterTrim.selected) failed.push("Timeline item lost selected state after trim");

  const consoleErrors = results.flatMap((item) => item.errors || []);
  console.log(JSON.stringify({ failed, consoleErrors, backup: BACKUP_JSON, results }, null, 2));
  if (failed.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
