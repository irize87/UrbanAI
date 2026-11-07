import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { API_BASE } from "../api";

export type BasemapId = "osm" | "vworld" | "carto";

const RING_COLOR = "#FF6B6B";

const RING_STYLE: L.PathOptions = {
  color: RING_COLOR,
  weight: 3,
  opacity: 1,
  fillColor: RING_COLOR,
  fillOpacity: 0.08,
  interactive: false,
};

type Props = {
  lat: number;
  lng: number;
  radiusM: number;
  shape: "circle" | "rect";
  bbox: { min_lat: number; min_lng: number; max_lat: number; max_lng: number };
  basemap: BasemapId;
  vworldReady?: boolean;
  onMove: (lat: number, lng: number) => void;
};

function createBaseLayer(id: BasemapId): L.TileLayer {
  switch (id) {
    case "vworld":
      // 백엔드 프록시 — 키 비노출. VWorld WMTS는 {z}/{y}/{x}
      return L.tileLayer(`${API_BASE}/api/tiles/base/{z}/{y}/{x}.png`, {
        attribution: '&copy; <a href="https://www.vworld.kr">VWorld</a>',
        maxZoom: 19,
        updateWhenIdle: true,
        updateWhenZooming: false,
        keepBuffer: 2,
        errorTileUrl:
          "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
      });
    case "carto":
      // 백엔드 프록시 — CARTO_API_KEY 비노출. Voyager {z}/{x}/{y}
      return L.tileLayer(`${API_BASE}/api/tiles/carto/{z}/{x}/{y}.png`, {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        maxZoom: 20,
        updateWhenIdle: true,
        updateWhenZooming: false,
        keepBuffer: 2,
        errorTileUrl:
          "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
      });
    case "osm":
    default:
      return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
        updateWhenIdle: true,
        updateWhenZooming: false,
        keepBuffer: 2,
      });
  }
}

function rectBounds(lat: number, lng: number, radiusM: number): L.LatLngBoundsExpression {
  const metersPerDegLat = 111320;
  const metersPerDegLng = 111320 * Math.cos((lat * Math.PI) / 180);
  const dLat = radiusM / metersPerDegLat;
  const dLng = radiusM / metersPerDegLng;
  return [
    [lat - dLat, lng - dLng],
    [lat + dLat, lng + dLng],
  ];
}

export function SiteMap({
  lat,
  lng,
  radiusM,
  shape,
  bbox,
  basemap,
  onMove,
}: Props) {
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const overlayRef = useRef<L.Path | null>(null);
  const svgRendererRef = useRef<L.SVG | null>(null);
  const baseRef = useRef<L.TileLayer | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [lat, lng],
      zoom: 15,
      maxBounds: [
        [bbox.min_lat - 0.01, bbox.min_lng - 0.01],
        [bbox.max_lat + 0.01, bbox.max_lng + 0.01],
      ],
      maxBoundsViscosity: 0.9,
      preferCanvas: true,
    });
    svgRendererRef.current = L.svg({ padding: 0.5 }).addTo(map);

    const base = createBaseLayer(basemap);
    base.addTo(map);
    baseRef.current = base;

    const icon = L.divIcon({
      className: "site-pin",
      html: `<div style="width:14px;height:14px;background:${RING_COLOR};border:2px solid #fff;border-radius:50%;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>`,
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });

    const marker = L.marker([lat, lng], { draggable: true, icon }).addTo(map);
    marker.on("dragend", () => {
      const p = marker.getLatLng();
      onMove(p.lat, p.lng);
    });
    map.on("click", (e: L.LeafletMouseEvent) => {
      onMove(e.latlng.lat, e.latlng.lng);
    });

    mapRef.current = map;
    markerRef.current = marker;

    const ro = new ResizeObserver(() => {
      map.invalidateSize();
    });
    if (containerRef.current) ro.observe(containerRef.current);
    requestAnimationFrame(() => map.invalidateSize());

    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      baseRef.current = null;
      overlayRef.current = null;
      svgRendererRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (baseRef.current) {
      map.removeLayer(baseRef.current);
    }
    const next = createBaseLayer(basemap);
    next.addTo(map);
    next.bringToBack();
    baseRef.current = next;
    overlayRef.current?.bringToFront();
  }, [basemap]);

  useEffect(() => {
    const map = mapRef.current;
    const marker = markerRef.current;
    const renderer = svgRendererRef.current;
    if (!map || !marker || !renderer) return;
    marker.setLatLng([lat, lng]);

    const style = { ...RING_STYLE, renderer };
    const overlay = overlayRef.current;
    const isCircle = overlay instanceof L.Circle;
    const isRect = overlay instanceof L.Rectangle;

    if (shape === "circle" && isCircle) {
      overlay.setLatLng([lat, lng]);
      overlay.setRadius(radiusM);
      overlay.setStyle(style);
      overlay.bringToFront();
      return;
    }
    if (shape === "rect" && isRect) {
      overlay.setBounds(rectBounds(lat, lng, radiusM));
      overlay.setStyle(style);
      overlay.bringToFront();
      return;
    }

    if (overlay) {
      map.removeLayer(overlay);
      overlayRef.current = null;
    }
    overlayRef.current =
      shape === "circle"
        ? L.circle([lat, lng], { ...style, radius: radiusM }).addTo(map)
        : L.rectangle(rectBounds(lat, lng, radiusM), style).addTo(map);
    overlayRef.current.bringToFront();
  }, [lat, lng, radiusM, shape]);

  return <div ref={containerRef} className="map-canvas" />;
}
