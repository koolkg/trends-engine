interface Props {
  points: number[];
  positive?: boolean;
  width?: number;
  height?: number;
}

/** Posts per hour over the sparkline window, as in the design doc's prototype. */
export function Sparkline({ points, positive = true, width = 64, height = 24 }: Props) {
  if (points.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const max = Math.max(...points);
  const min = Math.min(...points);
  const y = (v: number) => height - 3 - ((v - min) / (max - min || 1)) * (height - 6);
  const step = width / (points.length - 1);
  const pts = points.map((v, i) => [i * step, y(v)] as const);
  const line = pts.map((p) => p.join(",")).join(" ");
  const [lx, ly] = pts[pts.length - 1];
  const color = positive ? "var(--teal)" : "var(--muted)";
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={`Posts per hour, last ${points.length} hours: ${points.join(", ")}`}
    >
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={color} opacity={0.16} />
      <polyline points={line} fill="none" stroke={color} strokeWidth={1.6} />
      <circle cx={lx} cy={ly} r={2.4} fill={color} />
    </svg>
  );
}
