const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
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
const BACKUP_JSON = path.join(ROOT, "qa_artifacts", `project-backup-ltx-canvas-${Date.now()}.json`);
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

function prepareSyntheticLtxLayer() {
  const project = JSON.parse(fs.readFileSync(PROJECT_JSON, "utf8"));
  const motion = project.motions?.find((item) => item?.source_type === "figma" && Array.isArray(item.figma_layers));
  const layer = motion?.figma_layers?.find((item) =>
    item?.visible !== false &&
    item?.kind === "image" &&
    item?.asset_path &&
    !String(item?.id || "").startsWith("__frame_choreo_")
  );
  if (!motion || !layer) throw new Error("No current Figma image layer available for LTX canvas QA");

  const videoRel = "assets/qa-ltx-canvas-drag.mp4";
  const videoAbs = path.join(ROOT, "projects", PROJECT_ID, videoRel);
  fs.mkdirSync(path.dirname(videoAbs), { recursive: true });
  execFileSync("ffmpeg", [
    "-y",
    "-f", "lavfi",
    "-i", "color=c=0x121212:s=320x420:d=2:r=24",
    "-vf", "drawbox=x=20:y=20:w=280:h=380:color=0x38bdf8@0.9:t=6,drawbox=x=92:y=92:w=136:h=136:color=0xff7a59@0.85:t=fill",
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    videoAbs,
  ], { stdio: "ignore" });
  layer.ltx_video_path = videoRel;
  layer.ltx_duration = 2;
  layer.ltx_fps = 24;
  layer.ltx_max_side = 420;
  fs.writeFileSync(PROJECT_JSON, JSON.stringify(project, null, 2), "utf8");
  return { motionId: String(motion.id), layerId: String(layer.id), videoAbs };
}

async function main() {
  if (!fs.existsSync(PROJECT_JSON)) throw new Error(`QA project not found: ${PROJECT_JSON}`);
  fs.copyFileSync(PROJECT_JSON, BACKUP_JSON);
  const target = prepareSyntheticLtxLayer();
  const browser = await launchChromium();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  const failedResponses = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push({ status: response.status(), url: response.url() });
  });
  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForFunction(() => typeof project !== "undefined" && project?.motions?.length > 0, null, { timeout: 15000 });
    await page.waitForSelector(".motion-item", { timeout: 10000 });
    await page.waitForTimeout(1000);
    await page.evaluate(({ motionId, layerId }) => {
      const motion = findMotion(motionId);
      selectTimelineElement("motion", motionId);
      setSelectedFigmaLayer(motionId, layerId, { scroll: true });
      showMotionEditingPreviewAt(motion?.start || 0);
    }, target);
    await page.waitForSelector(`.editor-figma-hotspot[data-figma-layer-id="${target.layerId.replace(/"/g, '\\"')}"]`, { timeout: 10000 });
    await page.waitForFunction(() => {
      const media = document.querySelector(".editor-motion-image.figma-frame-base")
        || [...document.querySelectorAll("video")].find((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
      if (!media) return false;
      if (media.tagName === "VIDEO") return media.videoWidth > 0 && media.videoHeight > 0;
      if (media.tagName === "IMG") return media.naturalWidth > 0 && media.naturalHeight > 0;
      return true;
    }, null, { timeout: 10000 });
    const before = await page.evaluate((layerId) => {
      const hotspot = document.querySelector(`.editor-figma-hotspot[data-figma-layer-id="${CSS.escape(layerId)}"]`);
      const media = document.querySelector(".editor-motion-image.figma-frame-base")
        || [...document.querySelectorAll("video")].find((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
      const hotspotRect = hotspot?.getBoundingClientRect();
      const mediaRect = media?.getBoundingClientRect();
      return {
        hotspot: hotspotRect && { x: hotspotRect.x, y: hotspotRect.y, width: hotspotRect.width, height: hotspotRect.height },
        media: mediaRect && {
          tag: media.tagName,
          x: mediaRect.x,
          y: mediaRect.y,
          width: mediaRect.width,
          height: mediaRect.height,
          naturalWidth: media.tagName === "VIDEO" ? media.videoWidth : media.naturalWidth,
          naturalHeight: media.tagName === "VIDEO" ? media.videoHeight : media.naturalHeight,
          readyState: media.tagName === "VIDEO" ? media.readyState : null,
          src: media.currentSrc || media.src,
        },
      };
    }, target.layerId);
    if (!before.hotspot) throw new Error("LTX hotspot was not rendered in the preview canvas");
    await page.screenshot({ path: `${ARTIFACT_DIR}/qa-ltx-canvas-before-drag.png`, fullPage: false });

    const startX = before.hotspot.x + before.hotspot.width / 2;
    const startY = before.hotspot.y + before.hotspot.height / 2;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 35, startY + 20, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(1500);

    const after = await page.evaluate((layerId) => {
      const hotspot = document.querySelector(`.editor-figma-hotspot[data-figma-layer-id="${CSS.escape(layerId)}"]`);
      const media = document.querySelector(".editor-motion-image.figma-frame-base")
        || [...document.querySelectorAll("video")].find((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
      const selected = document.querySelector(".figma-layer-row.selected");
      const hotspotRect = hotspot?.getBoundingClientRect();
      const mediaRect = media?.getBoundingClientRect();
      return {
        selectedLayerId: selected?.dataset.layerId || "",
        hotspot: hotspotRect && { x: hotspotRect.x, y: hotspotRect.y, width: hotspotRect.width, height: hotspotRect.height },
        media: mediaRect && {
          tag: media.tagName,
          x: mediaRect.x,
          y: mediaRect.y,
          width: mediaRect.width,
          height: mediaRect.height,
          naturalWidth: media.tagName === "VIDEO" ? media.videoWidth : media.naturalWidth,
          naturalHeight: media.tagName === "VIDEO" ? media.videoHeight : media.naturalHeight,
          readyState: media.tagName === "VIDEO" ? media.readyState : null,
          src: media.currentSrc || media.src,
        },
      };
    }, target.layerId);
    await page.screenshot({ path: `${ARTIFACT_DIR}/qa-ltx-canvas-after-drag.png`, fullPage: false });
    const failed = [];
    if (after.selectedLayerId !== target.layerId) failed.push("Dragged LTX layer did not remain selected");
    if (!after.hotspot || after.hotspot.x === before.hotspot.x || after.hotspot.y === before.hotspot.y) failed.push("LTX layer hotspot did not move after drag");
    if (!after.media?.src) failed.push("Motion preview media disappeared after LTX layer drag");
    if (!after.media?.width || !after.media?.height) failed.push("Motion preview media has zero rendered size after LTX layer drag");
    if (!after.media?.naturalWidth || !after.media?.naturalHeight) failed.push("Motion preview media metadata is unavailable after LTX layer drag");
    const hasPluginAssetPollError = errors.some((text) => text.includes("loadFigmaPluginAssets") || text.includes("/api/figma/plugin/assets"))
      || failedResponses.some((item) => item.url.includes("/api/figma/plugin/assets"));
    const blockingErrors = errors.filter((text) => {
      if (text.includes("loadFigmaPluginAssets") || text.includes("/api/figma/plugin/assets")) return false;
      if (hasPluginAssetPollError && text.includes("Failed to load resource")) return false;
      return true;
    });
    const blockingResponses = failedResponses.filter((item) => !item.url.includes("/api/figma/plugin/assets"));
    if (blockingErrors.length) failed.push(`Console errors: ${blockingErrors.join(" | ")}`);
    if (blockingResponses.length) failed.push(`HTTP errors: ${blockingResponses.map((item) => `${item.status} ${item.url}`).join(" | ")}`);
    console.log(JSON.stringify({ failed, target, before, after, errors, failedResponses, backup: BACKUP_JSON }, null, 2));
    if (failed.length) process.exitCode = 1;
  } finally {
    await browser.close();
    fs.copyFileSync(BACKUP_JSON, PROJECT_JSON);
  }
}

main().catch((error) => {
  console.error(error);
  try {
    if (fs.existsSync(BACKUP_JSON)) fs.copyFileSync(BACKUP_JSON, PROJECT_JSON);
  } catch (_restoreError) {}
  process.exitCode = 1;
});
