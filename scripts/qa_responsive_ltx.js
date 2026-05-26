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
const BASE_URL = process.env.VIBEMOTION_QA_URL || `http://127.0.0.1:8010/app/index.html?project=${PROJECT_ID}`;

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

async function pickLtxTarget(page) {
  return page.evaluate(() => {
    const motion = project?.motions?.find((item) => item?.source_type === "figma" && Array.isArray(item.figma_layers));
    const layer = motion?.figma_layers?.find((item) =>
      item?.visible !== false &&
      item?.kind === "image" &&
      item?.asset_path &&
      !String(item?.id || "").startsWith("__frame_choreo_")
    );
    if (!motion || !layer) throw new Error("No current Figma image layer available for responsive LTX QA");
    return { motionId: String(motion.id), layerId: String(layer.id) };
  });
}

async function main() {
  const browser = await launchChromium();
  const results = [];
  for (const viewport of [
    { width: 320, height: 740 },
    { width: 768, height: 1024 },
    { width: 1024, height: 768 },
  ]) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
    const errors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".figma-layer-row", { timeout: 10000 });
    await page.waitForTimeout(800);
    const target = await pickLtxTarget(page);
    await page.evaluate(({ motionId, layerId }) => openLtxPromptEditor(motionId, layerId), target);
    await page.waitForSelector("#ltxPromptModal.show", { timeout: 5000 });
    await page.waitForTimeout(500);
    const data = await page.evaluate(() => {
      const modal = document.querySelector("#ltxPromptModal .prompt-dialog").getBoundingClientRect();
      const shell = document.querySelector("#ltxPreviewShell").getBoundingClientRect();
      const actions = document.querySelector("#ltxPromptModal .prompt-actions").getBoundingClientRect();
      return {
        viewport: { width: innerWidth, height: innerHeight },
        documentWidth: document.documentElement.scrollWidth,
        modal: { x: modal.x, y: modal.y, width: modal.width, height: modal.height, bottom: modal.bottom },
        shell: { width: shell.width, height: shell.height },
        actions: { x: actions.x, width: actions.width, right: actions.right },
        fitsViewport: modal.x >= 0 && modal.right <= innerWidth && modal.y >= 0 && modal.bottom <= innerHeight,
        noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth + 2,
      };
    });
    await page.screenshot({
      path: path.join(ROOT, "output", "playwright", `qa-responsive-ltx-${viewport.width}x${viewport.height}.png`),
      fullPage: false,
    });
    results.push({ viewport, data, errors });
    await page.close();
  }
  await browser.close();
  const failed = [];
  for (const result of results) {
    if (!result.data.fitsViewport) failed.push(`${result.viewport.width}x${result.viewport.height}: LTX modal does not fit viewport`);
    if (!result.data.noHorizontalOverflow) failed.push(`${result.viewport.width}x${result.viewport.height}: document has horizontal overflow`);
  }
  console.log(JSON.stringify({ failed, results }, null, 2));
  if (failed.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
