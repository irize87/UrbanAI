import { computeSunHoursGrid, type HeightBuilding, type SunHoursResult } from "./sunHours";

export type SunHoursJob = {
  buildings: HeightBuilding[];
  lat: number;
  lng: number;
  radiusM: number;
  dateIso: string;
  n?: number;
  stepMin?: number;
};

self.onmessage = (ev: MessageEvent<SunHoursJob>) => {
  const job = ev.data;
  const result: SunHoursResult = computeSunHoursGrid({
    buildings: job.buildings,
    lat: job.lat,
    lng: job.lng,
    radiusM: job.radiusM,
    date: job.dateIso,
    n: job.n,
    stepMin: job.stepMin,
  });
  self.postMessage(result);
};
