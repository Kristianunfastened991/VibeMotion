const fs = require("node:fs");
const path = require("node:path");
const ROOT = path.resolve(__dirname, "..");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (_error) {
  ({ chromium } = require(path.join(ROOT, "tmp", "pw", "node_modules", "playwright")));
}

const PROJECTS_ROOT = path.join(ROOT, "projects");
const SOURCE_PROJECT_ID = process.env.VIBEMOTION_QA_PROJECT || "testnew-9a98bc82";
const TEMP_PROJECT_ID = `qa-director-ui-${Date.now()}`;
const SOURCE_PROJECT_DIR = path.join(PROJECTS_ROOT, SOURCE_PROJECT_ID);
const TEMP_PROJECT_DIR = path.join(PROJECTS_ROOT, TEMP_PROJECT_ID);
const ARTIFACT_DIR = path.join(ROOT, "output", "playwright");
const BASE_API = process.env.VIBEMOTION_QA_API || "http://127.0.0.1:8010";
const PROMPT = [
  "Background layer appears with Venetian Blinds for 0.5 seconds.",
  "Then photos appear with parallax.",
  "Then text appears with fade up lines from top to bottom, headline first.",
  "Then black buttons rise on position Y from below with a light fade in.",
  "The whole composition must finish appearing in 2 seconds.",
  "At the end all layers scatter and fall down with gravity physics while fading out.",
].join(" ");

fs.mkdirSync(ARTIFACT_DIR, { recursive: true });

function safeRemoveTempProject() {
  const root = path.resolve(PROJECTS_ROOT);
  const target = path.resolve(TEMP_PROJECT_DIR);
  if (!target.startsWith(root + path.sep) || !path.basename(target).startsWith("qa-director-ui-")) {
    throw new Error(`Refusing to remove unexpected path: ${target}`);
  }
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
  }
}

function findInstalledChromium() {
  const root = path.join(process.env.USERPROFILE || "", "AppData", "Local", "ms-playwright");
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

function firstFigmaMotion(project) {
  const motion = (project.motions || []).find((item) => item?.source_type === "figma" && Array.isArray(item.figma_layers) && item.figma_layers.length);
  if (!motion) throw new Error("No Figma motion found in source project");
  return motion;
}

async function applyDirectorPrompt(projectId, motionId) {
  const response = await fetch(`${BASE_API}/api/projects/${projectId}/motion/${motionId}/prompt`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ prompt: PROMPT, mode: "replace" }),
  });
  if (!response.ok) {
    throw new Error(`Prompt API failed ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

async function main() {
  safeRemoveTempProject();
  fs.cpSync(SOURCE_PROJECT_DIR, TEMP_PROJECT_DIR, { recursive: true });
  const sourceState = JSON.parse(fs.readFileSync(path.join(TEMP_PROJECT_DIR, "project.json"), "utf8"));
  sourceState.project_id = TEMP_PROJECT_ID;
  fs.writeFileSync(path.join(TEMP_PROJECT_DIR, "project.json"), JSON.stringify(sourceState, null, 2));

  let browser;
  try {
    const motion = firstFigmaMotion(sourceState);
    const updatedState = await applyDirectorPrompt(TEMP_PROJECT_ID, motion.id);
    const updatedMotion = firstFigmaMotion(updatedState);
    const layerWithScenario = (updatedMotion.figma_layers || [])
      .find((layer) => layer?.motion_recipe?.prompt_scenario?.motion_director);
    const firstScenario = layerWithScenario?.motion_recipe?.prompt_scenario;
    if (!firstScenario) throw new Error("Prompt scenario with motion_director was not saved to project state");

    browser = await launchChromium();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    await page.goto(`${BASE_API}/app/index.html?project=${TEMP_PROJECT_ID}&fresh=1`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForSelector(".figma-asset", { timeout: 10000 });
    await page.waitForFunction(
      (nodeId) => Array.isArray(figmaAssets) && figmaAssets.some((asset) => String(asset?.node_id || asset?.id || "") === String(nodeId)),
      updatedMotion.figma_node_id,
      { timeout: 10000 },
    );
    await page.evaluate(({ motionId, layerId }) => {
      expandedMotionIds.add(motionId);
      manuallyCollapsedMotionIds.delete(motionId);
      selectedFigmaLayer = { motionId, layerId };
      renderFigmaAssetList();
    }, { motionId: updatedMotion.id, layerId: String(layerWithScenario.id || "") });
    await page.waitForSelector(".motion-director-card", { timeout: 5000 });
    await page.waitForSelector(".motion-qa-gate-card", { timeout: 5000 });
    await page.evaluate(() => {
      document.querySelector(".motion-qa-gate-card")?.scrollIntoView({ block: "center", inline: "nearest" });
    });
    await page.waitForTimeout(300);
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(ARTIFACT_DIR, "qa-motion-director-ui.png"), fullPage: false });

    const ui = await page.evaluate(() => {
      const text = (selector) => [...document.querySelectorAll(selector)].map((el) => el.textContent.trim()).filter(Boolean);
      return {
        directorCards: document.querySelectorAll(".motion-director-card").length,
        directorStatuses: text(".motion-director-status"),
        gateCards: document.querySelectorAll(".motion-qa-gate-card").length,
        gateStatuses: text(".motion-qa-gate-status"),
        gateChips: text(".motion-qa-gate-chip"),
        chips: text(".motion-director-chip"),
        scenarioRows: text(".motion-scenario-row"),
        overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
      };
    });

    const expectedChipParts = ["venetian", "photo", "text", "button", "scatter"];
    const missingChips = expectedChipParts.filter((part) => !ui.chips.some((chip) => chip.toLowerCase().includes(part)));
    const failed = [];
    if (consoleErrors.length) failed.push(`console errors: ${consoleErrors.join(" | ")}`);
    if (!ui.directorCards) failed.push("director card is not visible");
    if (!ui.directorStatuses.some((status) => status.toLowerCase() === "ok")) failed.push("director status is not ok");
    if (!ui.gateCards) failed.push("QA gate card is not visible");
    if (!ui.gateStatuses.some((status) => status.toLowerCase() === "ok")) failed.push("QA gate status is not ok");
    for (const label of ["Prompt plan", "Prompt timing", "DSL source", "Visual contract", "Figma fidelity"]) {
      if (!ui.gateChips.some((chip) => chip.toLowerCase().includes(label.toLowerCase()))) {
        failed.push(`missing QA gate chip: ${label}`);
      }
    }
    if (ui.scenarioRows.length < 5) failed.push(`expected at least 5 scenario rows, got ${ui.scenarioRows.length}`);
    if (missingChips.length) failed.push(`missing director chips: ${missingChips.join(", ")}`);
    if (ui.overflowX) failed.push("page has horizontal overflow");

    console.log(JSON.stringify({
      failed,
      tempProject: TEMP_PROJECT_ID,
      screenshot: path.join(ARTIFACT_DIR, "qa-motion-director-ui.png"),
      director: firstScenario.motion_director,
      ui,
    }, null, 2));
    if (failed.length) process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try {
      safeRemoveTempProject();
    } catch (error) {
      console.error(`cleanup warning: ${error.message}`);
    }
  }
}

main().catch((error) => {
  console.error(error);
  safeRemoveTempProject();
  process.exitCode = 1;
});
