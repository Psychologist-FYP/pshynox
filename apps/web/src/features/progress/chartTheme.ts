// Neutral chart tokens shared by every progress chart. Series slots 1–2 validated on #ffffff
// (CVD ΔE 24.7, normal-vision ΔE 33.6, both ≥ 3:1 contrast). Charts sit on a white surface,
// independent of the application theme, so they read like standard clinical charts.
export const CHART = {
  surface: "#ffffff",
  ink: "#1f2328",
  inkSecondary: "#57606a",
  muted: "#6e7781",
  grid: "#e5e7eb",
  axis: "#c9ced6",
  bandA: "#f6f7f9",
  bandB: "#ffffff",
  marker: "#57606a",
  font: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  blue: "#2a78d6",
  orange: "#eb6834",
};

export const axisTick = { fontSize: 11, fill: CHART.muted, fontFamily: CHART.font };
