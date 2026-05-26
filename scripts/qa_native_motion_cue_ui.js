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

const PROJECT_ID = process.env.VIBEMOTION_QA_PROJECT || "testnew-aa99b3b2";
const URL = process.env.VIBEMOTION_QA_URL || `http://127.0.0.1:8010/app/index.html?project=${PROJECT_ID}&fresh=1`;
const ARTIFACT_DIR = process.env.VIBEMOTION_QA_ARTIFACT_DIR || path.join(ROOT, "output", "playwright", `native-motion-cue-${Date.now()}`);
fs.mkdirSync(ARTIFACT_DIR, { recursive: true });

function findInstalledChromium() {
  const root = path.join(os.homedir(), "AppData", "Local", "ms-playwright");
  if (fs.existsSync(root)) {
    const candidates = fs.readdirSync(root)
      .filter((name) => name.startsWith("chromium_headless_shell-"))
      .sort()
      .reverse();
    for (const name of candidates) {
      const exe = path.join(root, name, "chrome-headless-shell-win64", "chrome-headless-shell.exe");
      if (fs.existsSync(exe)) return exe;
    }
  }
  const chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe";
  return fs.existsSync(chrome) ? chrome : null;
}

async function launchChromium() {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXE || findInstalledChromium();
  return chromium.launch(executablePath ? { headless: true, executablePath } : { headless: true });
}

async function waitLoaded(page) {
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForFunction(() => typeof project !== "undefined" && project?.source_video, null, { timeout: 15000 });
  await page.waitForSelector("#nativeMotionCueBtn", { timeout: 10000 });
  await page.waitForFunction(() => {
    const label = document.querySelector("#currentProjectLabel")?.textContent?.trim() || "";
    const button = document.querySelector("#nativeMotionCueBtn");
    return label && label !== "No project" && button && !button.disabled;
  }, null, { timeout: 15000 });
  await page.waitForTimeout(500);
}

async function main() {
  const browser = await launchChromium();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const consoleErrors = [];
  const previewResponses = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", async (response) => {
    if (!response.url().includes("/native-motion-cue/preview") || response.status() !== 200) return;
    try {
      previewResponses.push(await response.json());
    } catch (_error) {
      previewResponses.push({ parse_error: true });
    }
  });

  try {
    await waitLoaded(page);
    const before = await page.evaluate(() => ({
      projectId: project?.project_id,
      sourceVideo: project?.source_video,
      motionCount: Array.isArray(project?.motions) ? project.motions.length : 0,
      buttonDisabled: document.querySelector("#nativeMotionCueBtn")?.disabled,
    }));
    await page.screenshot({ path: `${ARTIFACT_DIR}/01-before.png`, fullPage: false });
    await page.click("#nativeMotionCueBtn");
    await page.waitForSelector("#nativeMotionCueModal.show", { timeout: 5000 });
    await page.fill("#nativeMotionCuePrompt", 'Add a premium callout here: "NEW PRODUCT" near the hand and make it pop in smoothly.');
    await page.selectOption("#nativeMotionCueDuration", "4");
    await page.click("#generateNativeMotionCueBtn");
    await page.waitForFunction(() => {
      const shell = document.querySelector("#nativeMotionCuePreviewShell");
      const video = document.querySelector("#nativeMotionCuePreviewVideo");
      return shell && video && !shell.classList.contains("generating") && video.classList.contains("show") && Boolean(video.getAttribute("src"));
    }, null, { timeout: 120000 });
    await page.waitForFunction(() => !document.querySelector("#redoNativeMotionCueBtn")?.disabled, null, { timeout: 5000 });
    await page.screenshot({ path: `${ARTIFACT_DIR}/02-preview.png`, fullPage: false });

    await page.click("#redoNativeMotionCueBtn");
    await page.waitForFunction(() => {
      const shell = document.querySelector("#nativeMotionCuePreviewShell");
      return shell && !shell.classList.contains("generating");
    }, null, { timeout: 120000 });
    await page.waitForFunction(() => document.querySelector("#nativeMotionCuePreviewVideo")?.classList.contains("show"), null, { timeout: 5000 });
    await page.screenshot({ path: `${ARTIFACT_DIR}/03-redo.png`, fullPage: false });

    const after = await page.evaluate(() => ({
      modalOpen: document.querySelector("#nativeMotionCueModal")?.classList.contains("show"),
      applyDisabled: document.querySelector("#applyNativeMotionCueBtn")?.disabled,
      redoDisabled: document.querySelector("#redoNativeMotionCueBtn")?.disabled,
      videoVisible: document.querySelector("#nativeMotionCuePreviewVideo")?.classList.contains("show"),
      videoSrc: document.querySelector("#nativeMotionCuePreviewVideo")?.getAttribute("src") || "",
      hint: document.querySelector("#nativeMotionCueHint")?.textContent || "",
      motionCount: Array.isArray(project?.motions) ? project.motions.length : 0,
    }));
    await browser.close();

    const failed = [];
    if (before.buttonDisabled) failed.push("Pencil button is disabled for a project with source video");
    if (!after.modalOpen) failed.push("Native motion cue modal did not remain open after preview");
    if (after.applyDisabled) failed.push("Apply stayed disabled after preview");
    if (after.redoDisabled) failed.push("Redo stayed disabled after preview");
    if (!after.videoVisible || !after.videoSrc) failed.push("Preview video was not visible");
    if (previewResponses.length < 2) failed.push("Generate + Redo did not create two preview responses");
    if (previewResponses.length >= 2) {
      const first = previewResponses[0];
      const second = previewResponses[1];
      if (first.preview_id === second.preview_id) failed.push("Redo reused the same preview_id");
      if (JSON.stringify(first.signature) === JSON.stringify(second.signature)) failed.push("Redo signature did not change");
    }
    if (after.motionCount !== before.motionCount) failed.push("Preview/Redo mutated project motion count before Apply");
    const result = { failed, consoleErrors, before, after, previewResponses, artifactDir: ARTIFACT_DIR };
    fs.writeFileSync(`${ARTIFACT_DIR}/result.json`, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    if (failed.length) process.exitCode = 1;
  } catch (error) {
    await page.screenshot({ path: `${ARTIFACT_DIR}/error.png`, fullPage: false }).catch(() => {});
    await browser.close();
    console.error(error);
    process.exitCode = 1;
  }
}

main();
