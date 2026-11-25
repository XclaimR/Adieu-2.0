/** "45 min", "1h", "2h 05m". The total is rounded to `step` minutes before it is split, so 119.6 reads "2h",
 * never "1h 60m" (Ticket #101). These are estimates, so never less than one step. */
export function duration(minutes: number, step = 5) {
  const m = Math.max(step, Math.round(minutes / step) * step);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${String(m % 60).padStart(2, "0")}m` : ""}`;
}
