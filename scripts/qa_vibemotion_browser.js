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

const PROJECT_ID = process.env.VIBEMOTION_QA_PROJECT || "testnew-9a98bc82";
const BASE_URL = process.env.VIBEMOTION_QA_URL || `http://127.0.0.1:8010/app/index.html?project=${PROJECT_ID}&fresh=1`;
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

function rectInfo(rect) {
  return {
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  };
}

async function inspectLtxLayer(page, motionId, layerId, name) {
  console.log(`inspect:${name}:open`);
  await page.evaluate(([motionId, layerId]) => openLtxPromptEditor(motionId, layerId), [motionId, layerId]);
  await page.waitForSelector("#ltxPromptModal.show", { timeout: 5000 });
  await page.waitForTimeout(800);
  const data = await page.evaluate(() => {
    const rectInfo = (rect) => ({
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    });
    const modal = document.querySelector("#ltxPromptModal .prompt-dialog");
    const shell = document.querySelector("#ltxPreviewShell");
    const img = document.querySelector("#ltxPreviewImage");
    const video = document.querySelector("#ltxPreviewVideo");
    const textarea = document.querySelector("#ltxPrompt");
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const modalRect = modal.getBoundingClientRect();
    const shellRect = shell.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const videoRect = video.getBoundingClientRect();
    const activeMedia = video.classList.contains("show") ? videoRect : imgRect;
    return {
      viewport,
      modal: {
        ...rectInfo(modalRect),
        fitsViewport: modalRect.left >= 0 && modalRect.right <= viewport.width && modalRect.top >= 0 && modalRect.bottom <= viewport.height,
        scrollHeight: Math.round(modal.scrollHeight),
        clientHeight: Math.round(modal.clientHeight),
      },
      shell: rectInfo(shellRect),
      image: {
        show: img.classList.contains("show"),
        ...rectInfo(imgRect),
        naturalWidth: img.naturalWidth,
        naturalHeight: img.naturalHeight,
        objectFit: getComputedStyle(img).objectFit,
      },
      video: {
        show: video.classList.contains("show"),
        ...rectInfo(videoRect),
        naturalWidth: video.videoWidth,
        naturalHeight: video.videoHeight,
        objectFit: getComputedStyle(video).objectFit,
        controls: video.controls,
        paused: video.paused,
      },
      activeInsideShell:
        activeMedia.left >= shellRect.left - 0.5 &&
        activeMedia.right <= shellRect.right + 0.5 &&
        activeMedia.top >= shellRect.top - 0.5 &&
        activeMedia.bottom <= shellRect.bottom + 0.5,
      textarea: rectInfo(textarea.getBoundingClientRect()),
    };
  });
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-ltx-${name}.png`, fullPage: false });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(250);
  console.log(`inspect:${name}:done`);
  return { name, data };
}

async function inspectMotionPromptEnglish(page) {
  console.log("inspect:motion-prompt-english:open");
  await page.click("[data-layer-animate]");
  await page.waitForSelector("#motionPromptModal.show", { timeout: 5000 });
  const data = await page.evaluate(() => {
    const text = document.querySelector("#motionPromptModal")?.innerText || "";
    return {
      title: document.querySelector("#motionPromptTitle")?.textContent || "",
      hint: document.querySelector("#motionPromptHint")?.textContent || "",
      newLabel: document.querySelector("#newMotionPromptBtn")?.textContent || "",
      addLabel: document.querySelector("#appendMotionPromptBtn")?.textContent || "",
      cancelLabel: document.querySelector("#cancelMotionPromptBtn")?.textContent || "",
      hasCyrillic: /[\u0400-\u04FF]/.test(text),
    };
  });
  await page.click("#cancelMotionPromptBtn");
  await page.waitForSelector("#motionPromptModal.show", { state: "detached", timeout: 5000 }).catch(async () => {
    await page.waitForSelector("#motionPromptModal:not(.show)", { timeout: 5000 });
  });
  console.log("inspect:motion-prompt-english:done");
  return { name: "motion-prompt-english", data };
}

async function pickLtxTargets(page) {
  return page.evaluate(() => {
    const motion = project?.motions?.find((item) => item?.source_type === "figma" && Array.isArray(item.figma_layers));
    const layers = (motion?.figma_layers || []).filter((item) =>
      item?.visible !== false &&
      item?.kind === "image" &&
      item?.asset_path &&
      !String(item?.id || "").startsWith("__frame_choreo_")
    );
    if (!motion || !layers.length) throw new Error("No current Figma image layer available for LTX QA");
    const sorted = [...layers].sort((a, b) => Number(b.width || 0) * Number(b.height || 0) - Number(a.width || 0) * Number(a.height || 0));
    return {
      motionId: String(motion.id),
      primaryLayerId: String(sorted[0].id),
      secondaryLayerId: String((sorted[1] || sorted[0]).id),
    };
  });
}

async function inspectGenerateSpinner(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await page.route("**/api/projects/*/motion/*/figma-layer/ltx", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 900));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "qa-ltx-job", kind: "ltx", status: "queued", progress: 1, message: "QA queued" }),
    });
  });
  await page.route("**/api/jobs/qa-ltx-job", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "qa-ltx-job", kind: "ltx", status: "running", progress: 12, message: "QA running" }),
    });
  });
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".figma-layer-row", { timeout: 10000 });
  const target = await pickLtxTargets(page);
  await inspectLtxLayer(page, target.motionId, target.primaryLayerId, "spinner-setup");
  await page.evaluate(({ motionId, layerId }) => openLtxPromptEditor(motionId, layerId), { motionId: target.motionId, layerId: target.primaryLayerId });
  await page.fill("#ltxPrompt", "slow cinematic camera push in");
  const during = await page.evaluate(() => ({
    ...(() => {
      generateLtxPreview();
      return {
        generating: document.querySelector("#ltxPreviewShell").classList.contains("generating"),
        generateDisabled: document.querySelector("#generateLtxPromptBtn").disabled,
        redoDisabled: document.querySelector("#redoLtxPromptBtn").disabled,
        applyDisabled: document.querySelector("#applyLtxPromptBtn").disabled,
        label: document.querySelector("#generateLtxPromptBtn").textContent.trim(),
      };
    })(),
  }));
  await page.screenshot({ path: `${ARTIFACT_DIR}/qa-ltx-spinner.png`, fullPage: false });
  await page.close();
  return { name: "generate-spinner", data: during };
}

async function main() {
  const browser = await launchChromium();
  const results = [];
  const consoleErrors = [];

  for (const viewport of [
    { width: 1440, height: 900, label: "desktop" },
    { width: 390, height: 844, label: "mobile" },
  ]) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push({ viewport: viewport.label, text: msg.text() });
    });
    console.log(`viewport:${viewport.label}:goto`);
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 15000 });
    console.log(`viewport:${viewport.label}:wait-layers`);
    await page.waitForSelector(".figma-layer-row", { timeout: 10000 });
    await page.waitForTimeout(1200);
    console.log(`viewport:${viewport.label}:screenshot`);
    await page.screenshot({ path: `${ARTIFACT_DIR}/qa-home-${viewport.label}.png`, fullPage: false });

    const base = await page.evaluate(() => {
      const rows = [...document.querySelectorAll(".figma-layer-row")].length;
      const buttons = [...document.querySelectorAll("[data-layer-ltx]")].length;
      const bodyText = document.body.innerText || "";
      return {
        projectLoaded: rows > 0,
        motionCount: [...document.querySelectorAll(".motion-card")].length,
        figmaLayerRows: rows,
        ltxButtons: buttons,
        bodyHasCyrillic: /[\u0400-\u04FF]/.test(bodyText.replace(document.querySelector("#ltxPrompt")?.value || "", "")),
        viewportOverflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
      };
    });
    results.push({ viewport: viewport.label, base });

    if (viewport.label === "desktop") {
      results.push(await inspectMotionPromptEnglish(page));
      const target = await pickLtxTargets(page);
      results.push(await inspectLtxLayer(page, target.motionId, target.primaryLayerId, "desktop-photo-ltx-video"));
      results.push(await inspectLtxLayer(page, target.motionId, target.secondaryLayerId, "desktop-wide-source-image"));
    } else {
      const target = await pickLtxTargets(page);
      results.push(await inspectLtxLayer(page, target.motionId, target.primaryLayerId, "mobile-photo-ltx-video"));
    }

    await page.close();
  }

  results.push(await inspectGenerateSpinner(browser));

  await browser.close();
  const failed = [];
  for (const item of results) {
    if (item.base) {
      if (!item.base.projectLoaded) failed.push(`${item.viewport}: project did not load`);
      if (item.base.viewportOverflowX) failed.push(`${item.viewport}: horizontal viewport overflow`);
      if (item.base.bodyHasCyrillic) failed.push(`${item.viewport}: visible UI contains Cyrillic text`);
      continue;
    }
    const data = item.data;
    if (item.name === "generate-spinner") {
      if (!data.generating) failed.push("generate-spinner: loading spinner did not become visible immediately");
      if (!data.generateDisabled) failed.push("generate-spinner: generate button stayed enabled during request");
      if (data.label !== "Generating...") failed.push("generate-spinner: button label did not switch to Generating...");
      continue;
    }
    if (item.name === "motion-prompt-english") {
      if (data.hasCyrillic) failed.push("motion-prompt-english: modal contains Cyrillic text");
      if (data.newLabel !== "New") failed.push(`motion-prompt-english: New label is ${data.newLabel}`);
      if (data.addLabel !== "Add") failed.push(`motion-prompt-english: Add label is ${data.addLabel}`);
      if (data.cancelLabel !== "Cancel") failed.push(`motion-prompt-english: Cancel label is ${data.cancelLabel}`);
      continue;
    }
    if (!data.modal.fitsViewport) failed.push(`${item.name}: modal does not fit viewport`);
    if (!data.activeInsideShell) failed.push(`${item.name}: active preview media escapes shell`);
    if (data.video.show && data.video.controls) failed.push(`${item.name}: native video controls are visible`);
    if (data.video.show && data.video.objectFit !== "contain") failed.push(`${item.name}: video object-fit is not contain`);
    if (data.image.show && data.image.objectFit !== "contain") failed.push(`${item.name}: image object-fit is not contain`);
  }
  console.log(JSON.stringify({ failed, consoleErrors, results }, null, 2));
  if (failed.length || consoleErrors.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
