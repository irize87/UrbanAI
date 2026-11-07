import { useEffect, useRef, useState } from "react";
import type { PanelMeta } from "../api";
import {
  AGE_LABELS,
  AGE_RAMP,
  AGE_UNKNOWN_2D,
  downloadBlob,
  stylePanelSvg,
  svgToPngBlob,
  tintRamp,
} from "../palette";
import { StarLoader } from "./StarLoader";

function SvgFrame({ markup }: { markup: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    node.replaceChildren();
    const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
    const root = doc.documentElement;
    if (root.tagName.toLowerCase() !== "svg") return;
    node.appendChild(document.importNode(root, true));
  }, [markup]);
  return <div className="svg-frame" ref={host} />;
}

type Props = {
  panel: PanelMeta;
  svg?: string;
  loading?: boolean;
  accent: string | null;
  cats: Record<string, string>;
  off: string[];
  threshold: number | null;
  onAccent: (color: string) => void;
  onCatColor: (key: string, color: string) => void;
  onToggleCat: (key: string, on: boolean) => void;
  onToggleAll: (on: boolean) => void;
  onResetColors: () => void;
  onThreshold: (value: number) => void;
  pngPx?: number;
  metric?: string | null;
  onMetric?: (value: string) => void;
};

export function DiagramCell({
  panel,
  svg,
  loading = false,
  accent,
  cats,
  off,
  threshold,
  onAccent,
  onCatColor,
  onToggleCat,
  onToggleAll,
  onResetColors,
  onThreshold,
  pngPx = 2000,
  metric = null,
  onMetric,
}: Props) {
  const [palOpen, setPalOpen] = useState(false);
  const styled = svg
    ? stylePanelSvg(svg, panel.key, { accent, cats, off })
    : "";
  const locked = Boolean(panel.color_locked);
  const palRows = panel.cats || [];
  const hasPal = palRows.length > 0;
  const floorApprox =
    panel.threshold && threshold
      ? Math.max(1, Math.round(threshold / 3.25))
      : null;
  const metricRows = panel.metrics || [];
  const hasMetrics = metricRows.length > 0;
  const metricLabel =
    metricRows.find(([value]) => value === metric)?.[1] || metricRows[0]?.[1] || null;
  const sub =
    panel.threshold && threshold
      ? panel.key === "high"
        ? `${threshold}m 이상 · 약 ${floorApprox}층`
        : `${threshold}m 이하 · 약 ${floorApprox}층`
      : hasMetrics && metricLabel
        ? `${metricLabel} · KDE 100m`
        : null;
  const ready = Boolean(styled);

  const saveSvg = () => {
    if (!styled) return;
    downloadBlob(
      new Blob([styled], { type: "image/svg+xml;charset=utf-8" }),
      `${panel.key}.svg`,
    );
  };

  const savePng = async () => {
    if (!styled) return;
    const blob = await svgToPngBlob(styled, pngPx);
    downloadBlob(blob, `${panel.key}.png`);
  };

  return (
    <div className={`cell${palOpen ? " is-open" : ""}`}>
      <div className="preview">
        {styled ? (
          <SvgFrame markup={styled} />
        ) : (
          <div className={`svg-placeholder${loading ? " loading" : ""}`}>
            {loading ? <StarLoader size={16} gap={7} speed={1.15} /> : null}
          </div>
        )}
      </div>
      <div className="cap">
        {panel.label}
        <em>{panel.ko}</em>
        {sub ? <i>{sub}</i> : null}
      </div>
      <div className="cbar">
        {hasPal ? (
          <button
            type="button"
            className={palOpen ? "on" : ""}
            onClick={() => setPalOpen((v) => !v)}
            title="범주별 색·표시"
          >
            색 표시
          </button>
        ) : hasMetrics ? (
          <div className="metric-toggle" title="밀도 지표">
            {metricRows.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={metric === value ? "on" : ""}
                onClick={() => onMetric?.(value)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : locked ? (
          <span className="lock" title="색이 곧 의미라 고정입니다">
            색 고정
          </span>
        ) : accent ? (
          <input
            type="color"
            value={accent}
            title={
              panel.key === "age"
                ? "색상만 바꿉니다(명암 6단은 유지)"
                : "강조색"
            }
            onChange={(e) => onAccent(e.target.value)}
          />
        ) : null}
        {panel.threshold && threshold != null ? (
          <>
            <input
              type="number"
              min={3}
              max={300}
              step={1}
              value={threshold}
              title={
                panel.key === "high"
                  ? "이 높이 이상을 고층으로 봅니다"
                  : "이 높이 이하를 저층으로 봅니다"
              }
              onChange={(e) => onThreshold(Number(e.target.value))}
            />
            <span className="unit">m ≈{floorApprox}F</span>
          </>
        ) : null}
        <button type="button" className="dl" disabled={!ready} onClick={saveSvg}>
          SVG
        </button>
        <button
          type="button"
          className="dl"
          disabled={!ready}
          onClick={() => void savePng()}
        >
          PNG
        </button>
      </div>
      {hasPal && palOpen ? (
        <div className="pal open">
          {palRows.map((c) => {
            const isOff = off.includes(c.key);
            return (
              <label key={c.key} className={`row${isOff ? " off" : ""}`}>
                <input
                  type="checkbox"
                  checked={!isOff}
                  onChange={(e) => onToggleCat(c.key, e.target.checked)}
                />
                <input
                  type="color"
                  value={cats[c.key] || c.color}
                  title={c.label}
                  onChange={(e) => onCatColor(c.key, e.target.value)}
                />
                <span>{c.label}</span>
              </label>
            );
          })}
          <div className="acts">
            <button type="button" onClick={() => onToggleAll(true)}>
              전체 켜기
            </button>
            <button type="button" onClick={() => onToggleAll(false)}>
              전체 끄기
            </button>
            <button type="button" onClick={onResetColors}>
              색 초기화
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function DiagramLegend({
  panels,
  catColors,
  catOff,
  accents,
}: {
  panels: PanelMeta[];
  catColors: Record<string, Record<string, string>>;
  catOff: Record<string, string[]>;
  accents: Record<string, string>;
}) {
  const groups: { title: string; items: { name: string; color: string }[] }[] = [];
  for (const key of ["zoning", "use", "age", "green", "planned", "district", "transit"]) {
    const p = panels.find((x) => x.key === key);
    if (!p) continue;
    if (key === "age") {
      const ramp = tintRamp(AGE_RAMP, accents.age || AGE_RAMP[1]);
      groups.push({
        title: p.ko,
        items: [
          ...AGE_LABELS.map((n, i) => ({ name: n, color: ramp[i] })),
          { name: "미상", color: AGE_UNKNOWN_2D },
        ],
      });
      continue;
    }
    if (!p.cats) continue;
    const off = new Set(catOff[key] || []);
    const colors = catColors[key] || {};
    groups.push({
      title: p.ko,
      items: p.cats
        .filter((c) => !off.has(c.key))
        .map((c) => ({ name: c.label, color: colors[c.key] || c.color })),
    });
  }
  if (!groups.length) return null;
  return (
    <div className="legend">
      {groups.map((g) => (
        <div key={g.title} className="legend-row">
          <span className="t">{g.title}</span>
          {g.items.map((it) => (
            <span key={it.name} className="i">
              <span className="sw" style={{ background: it.color }} />
              {it.name}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
