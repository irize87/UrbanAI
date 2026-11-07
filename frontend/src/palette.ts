/** 원본 sitedia 색 규칙 — 2D 패널과 3D 모델이 같은 팔레트를 쓴다. */

export const AGE_RAMP = [
  "#7c2d12",
  "#b34a35",
  "#d9714e",
  "#ee9d6f",
  "#f6c89f",
  "#fdeeda",
];
export const AGE_LABELS = [
  "~1979",
  "1980년대",
  "1990년대",
  "2000년대",
  "2010년대",
  "2020~",
];
export const AGE_LIMITS = [1980, 1990, 2000, 2010, 2020, 9999];
export const AGE_UNKNOWN_2D = "#ececec";
export const HIGH_ACCENT = "#4169e1";
export const LOWRISE_ACCENT = "#4ecdc4";
export const AGE_UNKNOWN_3D = "#dcdcdc";

export const HEIGHT_RAMP = ["#dbeafe", "#93c5fd", "#3b82f6", "#1d4ed8", "#172554"];
export const HEIGHT_LABELS = ["~12m", "12~25m", "25~45m", "45~80m", "80m~"];
export const HEIGHT_LIMITS = [12, 25, 45, 80, 1e9];

export const SOLID_3D = "#d8d8d8";
export const GHOST_TRANSLUCENT = "#b9bec6";
export const GHOST_OPAQUE = "#d5d8dd";
export const ROAD_3D = "#c4c4c4";

export const USE_COLORS: Record<string, string> = {
  주거: "#f2d98d",
  근린생활: "#f0a878",
  업무: "#6f9fd0",
  판매: "#e0607f",
  숙박: "#a97fd0",
  문화집회: "#5fbfb0",
  교육복지: "#86c06a",
  공업: "#9c8878",
  기타: "#c4c4c4",
  미상: "#ececec",
};

export const GREEN_3D: Record<string, string> = {
  park: "#c3e6a4",
  other: "#c3e6a4",
  water: "#8ecae6",
  sea: "#8ecae6",
};

export const LEGEND_ORDER = [
  "zoning",
  "use",
  "age",
  "green",
  "planned",
  "district",
  "transit",
] as const;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [
    parseInt(full.slice(0, 2), 16) / 255,
    parseInt(full.slice(2, 4), 16) / 255,
    parseInt(full.slice(4, 6), 16) / 255,
  ];
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l];
  const d = mx - mn;
  const s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  let h = 0;
  if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (mx === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, s, l];
}

function hueToRgb(p: number, q: number, t: number): number {
  let tt = t;
  if (tt < 0) tt += 1;
  if (tt > 1) tt -= 1;
  if (tt < 1 / 6) return p + (q - p) * 6 * tt;
  if (tt < 1 / 2) return q;
  if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
  return p;
}

function hslToHex(h: number, s: number, l: number): string {
  let r: number;
  let g: number;
  let b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hueToRgb(p, q, h + 1 / 3);
    g = hueToRgb(p, q, h);
    b = hueToRgb(p, q, h - 1 / 3);
  }
  const to = (v: number) =>
    Math.round(v * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

export function tintRamp(template: string[], base: string | null | undefined): string[] {
  if (!base) return [...template];
  try {
    const [bh, bs] = rgbToHsl(...hexToRgb(base));
    return template.map((step) => {
      const [, , tl] = rgbToHsl(...hexToRgb(step));
      return hslToHex(bh, bs, tl);
    });
  } catch {
    return [...template];
  }
}

export function darkenHex(hex: string, amount = 0.18): string {
  try {
    const [h, s, l] = rgbToHsl(...hexToRgb(hex));
    return hslToHex(h, s, Math.max(0, l - amount));
  } catch {
    return hex;
  }
}

export type PanelStyle = {
  accent?: string | null;
  cats?: Record<string, string>;
  off?: string[];
};

export function stylePanelSvg(
  svg: string,
  panelKey: string,
  style: PanelStyle,
): string {
  if (!svg) return svg;
  if (panelKey === "aerial" || panelKey === "sun_hours") return svg;
  const parser = new DOMParser();
  const doc = parser.parseFromString(svg, "image/svg+xml");
  if (doc.querySelector("parsererror")) return svg;
  const off = new Set(style.off || []);
  const cats = style.cats || {};
  const accent = style.accent;
  const ageColors =
    panelKey === "age" ? tintRamp(AGE_RAMP, accent || AGE_RAMP[1]) : null;

  doc.querySelectorAll("[data-cat]").forEach((el) => {
    const cat = el.getAttribute("data-cat") || "";
    if (el.tagName.toLowerCase() === "image") return;
    if (off.has(cat)) {
      el.setAttribute("display", "none");
      return;
    }
    el.removeAttribute("display");
    if (cat === "road" || cat === "ghost" || cat === "footprint" || cat === "void" || cat === "heat" || cat === "heat-base" || cat === "contour" || cat === "aerial" || cat === "sun") return;
    let fill: string | null = null;
    if (ageColors) {
      if (cat === "미상") fill = AGE_UNKNOWN_2D;
      else {
        const idx = AGE_LABELS.indexOf(cat);
        fill = idx >= 0 ? ageColors[idx] : accent || null;
      }
    } else if (cats[cat]) {
      fill = cats[cat];
    } else if (accent && cat === "main") {
      fill = accent;
    }
    if (!fill) return;
    const currentFill = el.getAttribute("fill");
    if (currentFill === "none") {
      el.setAttribute("stroke", fill);
    } else {
      el.setAttribute("fill", fill);
      const stroke = el.getAttribute("stroke");
      if (stroke && stroke !== "none") {
        const keepWhite =
          panelKey === "plot_size" ||
          stroke.toLowerCase() === "#ffffff" ||
          stroke.toLowerCase() === "#fff";
        el.setAttribute("stroke", keepWhite ? "#ffffff" : darkenHex(fill, 0.22));
      }
    }
  });
  return serializeSvgKeepingImages(doc);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** 원형 클립이면 사각 흰 배경을 빼고, 원 안만 하얗게 남겨 PNG 바깥이 투명해지게 한다. */
export function prepareSvgForTransparentCirclePng(svg: string): string {
  if (!svg) return svg;
  const parser = new DOMParser();
  const doc = parser.parseFromString(svg, "image/svg+xml");
  if (doc.querySelector("parsererror")) return svg;
  const clipCircle = doc.querySelector("clipPath circle");
  if (!clipCircle) return svg;
  const root = doc.documentElement;
  [...root.children].forEach((el) => {
    if (el.tagName.toLowerCase() !== "rect") return;
    const fill = (el.getAttribute("fill") || "").toLowerCase();
    if (fill === "#ffffff" || fill === "#fff" || fill === "white") {
      el.remove();
    }
  });
  const g = doc.querySelector("g[clip-path]");
  if (g) {
    const bg = clipCircle.cloneNode(true) as Element;
    bg.setAttribute("fill", "#ffffff");
    bg.setAttribute("stroke", "none");
    g.insertBefore(bg, g.firstChild);
  }
  if (!root.getAttribute("xmlns")) {
    root.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  }
  return serializeSvgKeepingImages(doc);
}

function serializeSvgKeepingImages(doc: Document): string {
  const root = doc.documentElement;
  const hrefs = [...root.querySelectorAll("image")].map((el) => {
    return (
      el.getAttribute("href") ||
      el.getAttributeNS("http://www.w3.org/1999/xlink", "href") ||
      ""
    );
  });
  const xml = new XMLSerializer().serializeToString(root);
  if (!hrefs.some((h) => h.startsWith("data:")) || xml.includes("data:image")) {
    return xml;
  }
  let i = 0;
  return xml.replace(/<image\b[^>]*\/?>/g, (tag) => {
    const href = hrefs[i++];
    if (!href) return tag;
    const q = href.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
    if (/\bhref=/.test(tag)) {
      return tag
        .replace(/\bhref="[^"]*"/, `href="${q}"`)
        .replace(/\bxlink:href="[^"]*"/, `xlink:href="${q}"`);
    }
    return tag.replace("<image", `<image href="${q}" xlink:href="${q}"`);
  });
}

export async function svgToPngBlob(svg: string, longPx = 2000): Promise<Blob> {
  const prepared = prepareSvgForTransparentCirclePng(svg);
  const m = prepared.match(/viewBox="([-\d.\s]+)"/);
  const parts = m ? m[1].trim().split(/\s+/).map(Number) : [0, 0, 800, 800];
  const vw = parts[2] || 800;
  const vh = parts[3] || 800;
  const scale = longPx / Math.max(vw, vh);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(vw * scale);
  canvas.height = Math.round(vh * scale);
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) throw new Error("canvas 없음");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const blob = new Blob([prepared], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    await new Promise<void>((ok, ng) => {
      img.onload = () => ok();
      img.onerror = () => ng(new Error("SVG 로드 실패"));
      img.src = url;
    });
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  } finally {
    URL.revokeObjectURL(url);
  }
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG 변환 실패"))), "image/png");
  });
}
