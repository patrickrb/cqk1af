export function formatFreq(hz: number): string {
  if (!hz) return "—";
  const mhz = hz / 1_000_000;
  return `${mhz.toFixed(3)} MHz`;
}

export function formatDbm(dbm: number | null | undefined): string {
  if (dbm == null) return "—";
  return `${dbm.toFixed(1)} dBm`;
}
