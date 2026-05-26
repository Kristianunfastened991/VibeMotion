const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (_error) {
  ({ chromium } = require(path.join(ROOT, "tmp", "pw", "node_modules", "playwright")));
}

const PROJECT_ID = "presentation-testnew-clean-guide";
const BASE_URL = `http://127.0.0.1:8010/app/index.html?project=${PROJECT_ID}`;
const OUT_DIR = path.join(ROOT, "output", "presentation-guide-testnew-clean");
const RAW_DIR = path.join(OUT_DIR, "raw");
const MOTION_ID = "figma-plugin-a1ec06cd";
const LTX_LAYER_ID = "12:290";
const VIEWPORT = { width: 1600, height: 1000 };
const CANVAS = { width: 1920, height: 1080 };
const SHOT = { x: 40, y: 118, width: 1320, height: 825 };
const COLORS = ["#ff7a00", "#2563eb", "#16a34a", "#b45309", "#7c3aed"];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function imageDataUri(filePath) {
  return `data:image/png;base64,${fs.readFileSync(filePath).toString("base64")}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function clipRect(rect) {
  if (!rect) return null;
  const x1 = Math.max(0, Math.min(VIEWPORT.width, rect.x));
  const y1 = Math.max(0, Math.min(VIEWPORT.height, rect.y));
  const x2 = Math.max(0, Math.min(VIEWPORT.width, rect.x + rect.width));
  const y2 = Math.max(0, Math.min(VIEWPORT.height, rect.y + rect.height));
  if (x2 <= x1 || y2 <= y1) return null;
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

function transformRect(rect) {
  const sx = SHOT.width / VIEWPORT.width;
  const sy = SHOT.height / VIEWPORT.height;
  return {
    x: SHOT.x + rect.x * sx,
    y: SHOT.y + rect.y * sy,
    width: rect.width * sx,
    height: rect.height * sy,
  };
}

async function waitForApp(page) {
  await page.goto(`${BASE_URL}&guide=${Date.now()}`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForSelector(".figma-layer-row", { timeout: 12000 });
  await page.waitForTimeout(900);
}

async function setInspectorScroll(page, top) {
  await page.evaluate((scrollTop) => {
    document.querySelector(".inspector")?.scrollTo({ top: scrollTop, behavior: "instant" });
  }, top);
  await page.waitForTimeout(250);
}

async function seekDemoTime(page) {
  await page.evaluate(() => {
    if (typeof seekToTime === "function") seekToTime(4.2);
  });
  await page.waitForTimeout(350);
}

async function selectLayer(page) {
  await page.evaluate(([motionId, layerId]) => {
    if (typeof setSelectedFigmaLayer === "function") {
      setSelectedFigmaLayer(motionId, layerId, { scroll: true });
    }
  }, [MOTION_ID, LTX_LAYER_ID]);
  await page.waitForTimeout(450);
}

async function replaceDemoPrompt(page) {
  await page.evaluate(() => {
    const prompt = "Slow cinematic camera push-in";
    document.querySelectorAll("textarea").forEach((textarea) => {
      const value = textarea.value || textarea.textContent || "";
      if (/[\u0400-\u04FF]/.test(value)) {
        textarea.value = prompt;
        textarea.textContent = prompt;
      }
    });
  });
}

async function openLtxModal(page) {
  await page.evaluate(([motionId, layerId]) => {
    if (typeof openLtxPromptEditor === "function") openLtxPromptEditor(motionId, layerId);
  }, [MOTION_ID, LTX_LAYER_ID]);
  await page.waitForSelector("#ltxPromptModal.show", { timeout: 5000 });
  await replaceDemoPrompt(page);
  await page.waitForTimeout(650);
}

async function rectFor(page, item) {
  if (item.rect) return clipRect(item.rect);
  const rect = await page.evaluate((selector) => {
    const node = document.querySelector(selector);
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  }, item.selector);
  return clipRect(rect);
}

async function renderComposite(browser, scene, rawPath, items) {
  const prepared = items.map((item, index) => ({
    ...item,
    index: index + 1,
    color: COLORS[index % COLORS.length],
    rect: item._rect ? transformRect(item._rect) : null,
  }));
  const marks = prepared
    .filter((item) => item.rect)
    .map((item) => {
      const rect = item.rect;
      const pad = item.pad ?? 4;
      return `<div class="target" style="--c:${item.color};left:${rect.x - pad}px;top:${rect.y - pad}px;width:${rect.width + pad * 2}px;height:${rect.height + pad * 2}px"></div>`;
    })
    .join("");
  const cards = prepared
    .map(
      (item) => `
        <article class="card" style="--c:${item.color}">
          <span>${item.index}</span>
          <div>
            <h3>${escapeHtml(item.heading)}</h3>
            <p>${escapeHtml(item.body)}</p>
          </div>
        </article>`
    )
    .join("");

  const html = `<!doctype html>
  <html lang="en">
    <head>
      <meta charset="utf-8" />
      <style>
        * { box-sizing: border-box; }
        body {
          margin: 0;
          width: ${CANVAS.width}px;
          height: ${CANVAS.height}px;
          overflow: hidden;
          background: #f7f5f0;
          color: #111;
          font-family: Inter, Manrope, Arial, sans-serif;
        }
        .page {
          position: relative;
          width: ${CANVAS.width}px;
          height: ${CANVAS.height}px;
          padding: 30px 40px;
        }
        h1 {
          margin: 0;
          font-size: 30px;
          line-height: 1.1;
          letter-spacing: 0;
        }
        .subtitle {
          margin: 8px 0 0;
          max-width: 880px;
          color: #555;
          font-size: 14px;
          line-height: 1.45;
        }
        .pill {
          position: absolute;
          right: 40px;
          top: 40px;
          padding: 10px 14px;
          border-radius: 999px;
          background: #111;
          color: #fff;
          font-size: 13px;
          font-weight: 800;
        }
        .shot {
          position: absolute;
          left: ${SHOT.x}px;
          top: ${SHOT.y}px;
          width: ${SHOT.width}px;
          height: ${SHOT.height}px;
          object-fit: cover;
          border-radius: 18px;
          border: 1px solid rgba(0,0,0,.16);
          box-shadow: 0 26px 84px rgba(0,0,0,.16);
          background: #111;
        }
        .target {
          position: absolute;
          border: 3px solid var(--c);
          border-radius: 12px;
          box-shadow: 0 0 0 2px #fff;
          pointer-events: none;
        }
        .legend {
          position: absolute;
          left: 1400px;
          top: ${SHOT.y}px;
          width: 480px;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .legend-head {
          padding: 16px 18px;
          border-radius: 16px;
          background: #111;
          color: #fff;
          box-shadow: 0 16px 44px rgba(0,0,0,.16);
        }
        .legend-head strong {
          display: block;
          font-size: 16px;
          line-height: 1.2;
        }
        .legend-head em {
          display: block;
          margin-top: 5px;
          color: rgba(255,255,255,.72);
          font-style: normal;
          font-size: 12px;
          line-height: 1.4;
        }
        .card {
          display: grid;
          grid-template-columns: 34px 1fr;
          gap: 12px;
          padding: 15px 16px;
          border-radius: 16px;
          background: #fff;
          border: 1px solid rgba(0,0,0,.12);
          box-shadow: 0 14px 42px rgba(0,0,0,.1);
        }
        .card span {
          width: 32px;
          height: 32px;
          display: grid;
          place-items: center;
          border-radius: 999px;
          background: var(--c);
          color: #fff;
          font-weight: 900;
          font-size: 15px;
        }
        .card h3 {
          margin: 1px 0 4px;
          font-size: 16px;
          line-height: 1.2;
          letter-spacing: 0;
        }
        .card p {
          margin: 0;
          color: #555;
          font-size: 13px;
          line-height: 1.42;
        }
      </style>
    </head>
    <body>
      <main class="page">
        <h1>${escapeHtml(scene.title)}</h1>
        <p class="subtitle">${escapeHtml(scene.subtitle)}</p>
        <div class="pill">Source video: testNew.mp4</div>
        <img class="shot" src="${imageDataUri(rawPath)}" alt="" />
        ${marks}
        <aside class="legend">
          <div class="legend-head"><strong>Numbered guide</strong><em>Numbers stay in this panel. The app screenshot only uses matching colored outlines.</em></div>
          ${cards}
        </aside>
      </main>
    </body>
  </html>`;

  const composite = await browser.newPage({ viewport: CANVAS, deviceScaleFactor: 1 });
  await composite.setContent(html, { waitUntil: "load" });
  await composite.screenshot({ path: path.join(OUT_DIR, scene.file), fullPage: false });
  await composite.close();
}

async function captureScene(browser, page, scene) {
  await scene.prepare(page);
  const items = [];
  for (const item of scene.items) {
    items.push({ ...item, _rect: await rectFor(page, item) });
  }
  const rawPath = path.join(RAW_DIR, scene.file);
  await page.screenshot({ path: rawPath, fullPage: false });
  await renderComposite(browser, scene, rawPath, items);
}

async function main() {
  ensureDir(OUT_DIR);
  ensureDir(RAW_DIR);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 1, locale: "en-US" });
  await waitForApp(page);

  const scenes = [
    {
      file: "01-workspace-overview.png",
      title: "1. Workspace Overview",
      subtitle: "VibeMotion combines source video, Figma overlays, layer animation, timeline editing, and export in one workspace.",
      prepare: async (page) => {
        await page.keyboard.press("Escape").catch(() => {});
        await setInspectorScroll(page, 0);
        await seekDemoTime(page);
      },
      items: [
        { selector: ".inspector", heading: "Setup and Figma area", body: "Add motion, sync Figma frames, and inspect frame/layer controls." },
        { selector: ".preview-shell", heading: "Live preview canvas", body: "Review the current edit with the testNew.mp4 source video and overlays." },
        { selector: ".sidebar", heading: "Project panel", body: "Upload video, monitor logs, and prepare final export." },
        { selector: ".timeline", heading: "Timeline", body: "Control timing for source video clips and motion overlays." },
      ],
    },
    {
      file: "02-figma-sync-import.png",
      title: "2. Figma Sync and Frame Import",
      subtitle: "Refresh exported Figma frames and place them on the Motion track.",
      prepare: async (page) => {
        await page.keyboard.press("Escape").catch(() => {});
        await setInspectorScroll(page, 135);
        await seekDemoTime(page);
      },
      items: [
        { selector: "#figmaLoadPluginBtn", heading: "Update from Figma Space", body: "Loads the latest frames exported through the local Figma bridge." },
        { selector: "#figmaAssetList .figma-asset:first-child", heading: "Frame card", body: "Shows frame thumbnail, source, size, and layer count." },
        { selector: "#figmaAssetList .figma-asset:first-child .tree-toggle", heading: "Expand layers", body: "Opens the frame tree for selecting individual layers." },
        { rect: { x: 92, y: 880, width: 1220, height: 104 }, heading: "Motion track drop area", body: "Drag a frame here to create a timed overlay above the source video." },
      ],
    },
    {
      file: "03-layer-selection-controls.png",
      title: "3. Layer Selection and Controls",
      subtitle: "Select a Figma layer and adjust its transform, prompt, or animation mode.",
      prepare: async (page) => {
        await page.keyboard.press("Escape").catch(() => {});
        await setInspectorScroll(page, 250);
        await seekDemoTime(page);
        await selectLayer(page);
        await replaceDemoPrompt(page);
      },
      items: [
        { selector: ".figma-layer-row.selected", heading: "Selected layer row", body: "Defines exactly which Figma layer will be edited." },
        { selector: ".figma-layer-row.selected [data-layer-ltx]", heading: "LTX animation button", body: "Opens local LTX 2.3 generation for this image layer only." },
        { selector: ".figma-layer-details .figma-layer-controls", heading: "Transform fields", body: "Precise x, y, width, height, and scale values." },
        { selector: ".figma-layer-details textarea", heading: "Layer prompt", body: "The LTX prompt stays attached to the current layer." },
      ],
    },
    {
      file: "04-ltx-prompt-setup.png",
      title: "4. LTX 2.3 Prompt Setup",
      subtitle: "Write the prompt and choose generation settings before creating a preview.",
      prepare: async (page) => {
        await openLtxModal(page);
      },
      items: [
        { selector: "#ltxPreviewShell", heading: "Preview area", body: "Shows the source layer or generated video without cropping." },
        { selector: "#ltxPrompt", heading: "Video prompt", body: "Describe motion for the selected layer only." },
        { selector: "#ltxDuration", heading: "Duration", body: "Choose the generated clip length from available presets." },
        { selector: "#ltxQuality", heading: "Quality preset", body: "Select draft speed or stronger generation settings." },
      ],
    },
    {
      file: "05-ltx-review-apply.png",
      title: "5. Generate, Review, Apply",
      subtitle: "Generate a preview, redo it if needed, then apply it back to the selected layer.",
      prepare: async (page) => {
        await openLtxModal(page);
      },
      items: [
        { selector: "#ltxPreviewShell", heading: "Generated preview", body: "Review the animated result in the same safe preview area." },
        { selector: ".prompt-actions", heading: "Action buttons", body: "Cancel, redo, apply, or generate preview without covering button labels." },
      ],
    },
    {
      file: "06-timeline-editing.png",
      title: "6. Timeline Editing",
      subtitle: "Edit source video clips and motion layers separately for precise timing.",
      prepare: async (page) => {
        await page.keyboard.press("Escape").catch(() => {});
        await setInspectorScroll(page, 0);
        await seekDemoTime(page);
      },
      items: [
        { selector: ".timeline-toolbar", heading: "Edit tools", body: "Undo, redo, and split controls stay above the timeline." },
        { selector: "#videoTrack", heading: "Video track", body: "Source clips from testNew.mp4 can be moved, trimmed, and split." },
        { selector: "#motionTrack", heading: "Motion track", body: "Figma overlays and generated layer videos sit above the source video." },
        { selector: "#playhead", heading: "Playhead", body: "Scrub time and choose the exact point for timeline actions." },
      ],
    },
    {
      file: "07-review-export.png",
      title: "7. Review and Export",
      subtitle: "Review playback, watch render status, and export the finished video.",
      prepare: async (page) => {
        await page.keyboard.press("Escape").catch(() => {});
        await setInspectorScroll(page, 0);
        await seekDemoTime(page);
      },
      items: [
        { selector: ".preview-shell", heading: "Playback review", body: "Check composition and timing before export." },
        { selector: "#previewBadge", heading: "Status badge", body: "Shows whether the preview is current or rendering." },
        { selector: "#downloadBtn", heading: "Download render", body: "Starts final rendering and downloads the completed file." },
        { selector: "#jobStatus", heading: "Process log", body: "Readable feedback for sync, generation, rendering, and errors." },
      ],
    },
  ];

  for (const scene of scenes) {
    await captureScene(browser, page, scene);
  }
  await browser.close();

  fs.writeFileSync(
    path.join(OUT_DIR, "manifest.json"),
    JSON.stringify({ projectId: PROJECT_ID, sourceVideo: process.env.VIBEMOTION_PRESENTATION_SOURCE || "", screenshots: scenes.map((scene) => scene.file) }, null, 2),
    "utf8"
  );
  console.log(JSON.stringify({ outputDir: OUT_DIR, screenshots: scenes.map((scene) => scene.file) }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
