figma.showUI(__html__, { width: 360, height: 420 });

function bytesToBase64(bytes) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let output = "";
  let index = 0;
  while (index < bytes.length) {
    const a = bytes[index++];
    const b = index < bytes.length ? bytes[index++] : NaN;
    const c = index < bytes.length ? bytes[index++] : NaN;
    const triple = (a << 16) | ((b || 0) << 8) | (c || 0);
    output += chars[(triple >> 18) & 63];
    output += chars[(triple >> 12) & 63];
    output += Number.isNaN(b) ? "=" : chars[(triple >> 6) & 63];
    output += Number.isNaN(c) ? "=" : chars[triple & 63];
  }
  return output;
}

function isExportableRoot(node) {
  return ["FRAME", "COMPONENT", "INSTANCE", "GROUP", "SECTION"].includes(node.type);
}

function isContainer(node) {
  return ["FRAME", "COMPONENT", "INSTANCE", "GROUP", "SECTION"].includes(node.type);
}

function isMixed(value) {
  return value === figma.mixed || typeof value === "symbol";
}

function numberValue(value, fallback) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return fallback;
}

function colorToCss(color, opacity) {
  const alpha = Math.max(0, Math.min(1, numberValue(opacity, 1) * numberValue(color.a, 1)));
  return `rgba(${Math.round(numberValue(color.r, 0) * 255)}, ${Math.round(numberValue(color.g, 0) * 255)}, ${Math.round(numberValue(color.b, 0) * 255)}, ${alpha.toFixed(4)})`;
}

function solidFill(node, nodeOpacityOverride = null) {
  const fills = "fills" in node && !isMixed(node.fills) ? node.fills : [];
  const opacity = nodeOpacityOverride === null
    ? ("opacity" in node ? numberValue(node.opacity, 1) : 1)
    : numberValue(nodeOpacityOverride, 1);
  for (const paint of fills || []) {
    if (paint.visible === false) continue;
    if (paint.type === "SOLID" && paint.color) {
      return colorToCss(paint.color, numberValue(paint.opacity, 1) * opacity);
    }
  }
  return null;
}

function solidStroke(node) {
  const strokes = "strokes" in node && !isMixed(node.strokes) ? node.strokes : [];
  const opacity = "opacity" in node ? numberValue(node.opacity, 1) : 1;
  for (const paint of strokes || []) {
    if (paint.visible === false) continue;
    if (paint.type === "SOLID" && paint.color) {
      return {
        color: colorToCss(paint.color, numberValue(paint.opacity, 1) * opacity),
        weight: numberValue(node.strokeWeight, 1),
      };
    }
  }
  return null;
}

function hasImageFill(node) {
  const fills = "fills" in node && !isMixed(node.fills) ? node.fills : [];
  return (fills || []).some((paint) => paint.visible !== false && paint.type === "IMAGE");
}

function hasUnsupportedVisual(node) {
  const fills = "fills" in node && !isMixed(node.fills) ? node.fills : [];
  const effects = "effects" in node && !isMixed(node.effects) ? node.effects : [];
  const hasGradient = (fills || []).some((paint) => paint.visible !== false && String(paint.type || "").includes("GRADIENT"));
  const hasEffects = (effects || []).some((effect) => effect.visible !== false);
  return hasGradient || hasEffects;
}

function cornerRadius(node) {
  if ("cornerRadius" in node && typeof node.cornerRadius === "number") return node.cornerRadius;
  if ("topLeftRadius" in node) {
    return Math.max(
      numberValue(node.topLeftRadius, 0),
      numberValue(node.topRightRadius, 0),
      numberValue(node.bottomRightRadius, 0),
      numberValue(node.bottomLeftRadius, 0)
    );
  }
  return 0;
}

function nodeBox(node, rootBox) {
  if (!("absoluteBoundingBox" in node) || !node.absoluteBoundingBox) return null;
  const box = node.absoluteBoundingBox;
  if (!box.width || !box.height) return null;
  return {
    x: Math.round((box.x - rootBox.x) * 1000) / 1000,
    y: Math.round((box.y - rootBox.y) * 1000) / 1000,
    width: Math.round(box.width * 1000) / 1000,
    height: Math.round(box.height * 1000) / 1000,
  };
}

function layerFromBase(base, values) {
  const layer = {};
  for (const key in base) layer[key] = base[key];
  for (const key in values) layer[key] = values[key];
  return layer;
}

async function imageLayerFromNode(node, rootBox, namePrefix) {
  const box = nodeBox(node, rootBox);
  if (!box) return null;
  const bytes = await node.exportAsync({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
  return {
      id: node.id,
      name: node.name || namePrefix || node.type,
      node_type: node.type,
      kind: "image",
    x: box.x,
    y: box.y,
    width: box.width,
      height: box.height,
      opacity: "opacity" in node ? numberValue(node.opacity, 1) : 1,
      png_base64: bytesToBase64(bytes),
  };
}

async function layerPngBase64(node) {
  try {
    const bytes = await node.exportAsync({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
    return bytesToBase64(bytes);
  } catch (error) {
    return "";
  }
}

function shouldAttachExactLayerPng(node) {
  return [
    "TEXT",
    "VECTOR",
    "BOOLEAN_OPERATION",
    "STAR",
    "LINE",
    "ELLIPSE",
    "POLYGON",
  ].includes(node.type);
}

async function collectLayers(node, rootBox, layers, includeSelf) {
  if ("visible" in node && node.visible === false) return;
  const box = nodeBox(node, rootBox);
  const base = box
    ? {
        id: node.id,
        name: node.name || node.type,
        node_type: node.type,
        x: box.x,
        y: box.y,
        width: box.width,
        height: box.height,
        opacity: includeSelf && ["FRAME", "COMPONENT", "INSTANCE"].includes(node.type)
          ? 1
          : "opacity" in node ? numberValue(node.opacity, 1) : 1,
      }
    : null;

  const rootFrameSelf = includeSelf && ["FRAME", "COMPONENT", "INSTANCE"].includes(node.type);
  const fill = solidFill(node, rootFrameSelf ? 1 : null);
  const stroke = solidStroke(node);
  if (base && (includeSelf || node.type !== "GROUP")) {
    if (hasUnsupportedVisual(node) && !isContainer(node) && node.type !== "TEXT") {
      const imageLayer = await imageLayerFromNode(node, rootBox, node.name);
      if (imageLayer) layers.push(imageLayer);
      return;
    }

    if (fill && node.type !== "TEXT") {
      const shapeLayer = layerFromBase(base, {
        kind: "shape",
        fill,
        radius: Math.round(cornerRadius(node) * 1000) / 1000,
      });
      if (shouldAttachExactLayerPng(node)) {
        const pngBase64 = await layerPngBase64(node);
        if (pngBase64) shapeLayer.png_base64 = pngBase64;
      }
      if (stroke) {
        shapeLayer.stroke = stroke.color;
        shapeLayer.stroke_weight = Math.round(stroke.weight * 1000) / 1000;
      }
      layers.push(shapeLayer);
    } else if (stroke && node.type !== "TEXT" && !hasImageFill(node)) {
      const shapeLayer = layerFromBase(base, {
        kind: "shape",
        fill: "rgba(0, 0, 0, 0)",
        stroke: stroke.color,
        stroke_weight: Math.round(stroke.weight * 1000) / 1000,
        radius: Math.round(cornerRadius(node) * 1000) / 1000,
      });
      if (shouldAttachExactLayerPng(node)) {
        const pngBase64 = await layerPngBase64(node);
        if (pngBase64) shapeLayer.png_base64 = pngBase64;
      }
      layers.push(shapeLayer);
    }

    if (node.type === "TEXT") {
      const fontName = !isMixed(node.fontName) && node.fontName ? node.fontName : { family: "Arial", style: "Regular" };
      const fontSize = numberValue(node.fontSize, Math.max(12, base.height * 0.45));
      const lineHeight =
        node.lineHeight && !isMixed(node.lineHeight) && node.lineHeight.unit === "PIXELS"
          ? numberValue(node.lineHeight.value, fontSize * 1.1)
          : fontSize * 1.1;
      const textLayer = layerFromBase(base, {
        kind: "text",
        text: node.characters || "",
        font_size: fontSize,
        font_weight: /bold|black|heavy|semibold/i.test(String(fontName.style || "")) ? 700 : 500,
        font_family: fontName.family || "Arial",
        line_height: lineHeight,
        color: fill || "rgba(0, 0, 0, 1)",
        text_align: String(node.textAlignHorizontal || "LEFT").toLowerCase(),
      });
      const pngBase64 = await layerPngBase64(node);
      if (pngBase64) textLayer.png_base64 = pngBase64;
      layers.push(textLayer);
      return;
    }

    if (hasImageFill(node) && node.type !== "FRAME") {
      const imageLayer = await imageLayerFromNode(node, rootBox, node.name);
      if (imageLayer) layers.push(imageLayer);
      return;
    }
  }

  if (isContainer(node) && "children" in node) {
    for (const child of node.children) {
      await collectLayers(child, rootBox, layers, false);
    }
    return;
  }

  if (base && !fill && node.type !== "TEXT") {
    const imageLayer = await imageLayerFromNode(node, rootBox, node.name);
    if (imageLayer) layers.push(imageLayer);
  }
}

function exportableNodes(mode) {
  if (mode === "selection") {
    return figma.currentPage.selection.filter(isExportableRoot).filter((node) => {
      const width = Number(node.width || 0);
      const height = Number(node.height || 0);
      return width > 0 && height > 0 && width * height <= 12000000 && width <= 4096 && height <= 4096;
    });
  }
  return figma.currentPage.children.filter((node) =>
    ["FRAME", "COMPONENT", "INSTANCE"].includes(node.type)
  ).filter((node) => {
    const width = Number(node.width || 0);
    const height = Number(node.height || 0);
    return width > 0 && height > 0 && width * height <= 12000000 && width <= 4096 && height <= 4096;
  });
}

async function exportNodes(mode) {
  const nodes = exportableNodes(mode);
  if (!nodes.length) {
    figma.ui.postMessage({ type: "error", message: mode === "selection" ? "Select one or more frames first." : "No top-level frames found on this page." });
    return;
  }

  const sessionId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    figma.ui.postMessage({ type: "progress", message: `Exporting ${index + 1}/${nodes.length}: ${node.name}` });
    const rootBox = "absoluteBoundingBox" in node && node.absoluteBoundingBox
      ? node.absoluteBoundingBox
      : { x: 0, y: 0, width: node.width || 1, height: node.height || 1 };
    const layers = [];
    await collectLayers(node, rootBox, layers, true);
    const bytes = await node.exportAsync({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
    figma.ui.postMessage({
      type: "assets",
      scope: mode === "page" ? "page" : "selection",
      page: figma.currentPage.name,
      session_id: sessionId,
      total: nodes.length,
      index,
      assets: [{
      id: node.id,
      node_id: node.id,
      name: node.name,
      path: `${figma.currentPage.name} / ${node.name}`,
      page: figma.currentPage.name,
      node_type: node.type,
      width: Math.round(node.width || 1),
      height: Math.round(node.height || 1),
      canvas_x: Math.round(numberValue(rootBox.x, 0) * 1000) / 1000,
      canvas_y: Math.round(numberValue(rootBox.y, 0) * 1000) / 1000,
      export_index: index,
      children_count: "children" in node ? node.children.length : 0,
      figma_layers: layers,
      png_base64: bytesToBase64(bytes),
      }],
    });
  }
  figma.ui.postMessage({
    type: "assets",
    scope: mode === "page" ? "page" : "selection",
    page: figma.currentPage.name,
    session_id: sessionId,
    total: nodes.length,
    complete: true,
    assets: [],
  });
  figma.ui.postMessage({ type: "progress", message: `Done. Exported ${nodes.length} frame(s).` });
}

function restorePageFrameOpacity() {
  const changed = [];
  for (const node of figma.currentPage.children) {
    if (!["FRAME", "COMPONENT", "INSTANCE", "GROUP", "SECTION"].includes(node.type)) {
      continue;
    }
    if (typeof node.opacity === "number" && node.opacity < 0.999) {
      node.opacity = 1;
      changed.push({ id: node.id, name: node.name, type: node.type });
    }
  }
  figma.ui.postMessage({
    type: "progress",
    message: changed.length
      ? `Restored opacity to 100% on ${changed.length} top-level frame(s).`
      : "No faded top-level frames found on this page."
  });
}

figma.ui.onmessage = async (message) => {
  if (message.type === "export-selection") {
    await exportNodes("selection");
  }
  if (message.type === "export-page") {
    await exportNodes("page");
  }
  if (message.type === "restore-opacity") {
    restorePageFrameOpacity();
  }
  if (message.type === "close") {
    figma.closePlugin();
  }
};
