type Props = {
  size?: number;
  gap?: number;
  speed?: number;
  className?: string;
  count?: number;
};

function StarShape({
  color,
  size,
  delay,
  speed,
}: {
  color: string;
  size: number;
  delay: number;
  speed: number;
}) {
  return (
    <div
      className="star-shape"
      style={{
        width: size,
        height: size,
        animationDelay: `${delay}s`,
        animationDuration: `${speed}s`,
      }}
    >
      <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden>
        <g>
          <rect x="44" y="15" width="12" height="35" rx="6" ry="6" fill={color} />
          <rect
            x="44"
            y="15"
            width="12"
            height="35"
            rx="6"
            ry="6"
            fill={color}
            transform="rotate(72 50 50)"
          />
          <rect
            x="44"
            y="15"
            width="12"
            height="35"
            rx="6"
            ry="6"
            fill={color}
            transform="rotate(144 50 50)"
          />
          <rect
            x="44"
            y="15"
            width="12"
            height="35"
            rx="6"
            ry="6"
            fill={color}
            transform="rotate(216 50 50)"
          />
          <rect
            x="44"
            y="15"
            width="12"
            height="35"
            rx="6"
            ry="6"
            fill={color}
            transform="rotate(288 50 50)"
          />
        </g>
      </svg>
    </div>
  );
}

export function StarLoader({
  size = 18,
  gap = 8,
  speed = 1.2,
  className = "",
  count = 3,
}: Props) {
  const colors = ["#E91E63", "#2196F3", "#8BC34A"];
  const stars = Array.from({ length: Math.max(1, Math.min(count, 3)) }, (_, index) => index);
  return (
    <div
      className={`star-loader${className ? ` ${className}` : ""}`}
      role="status"
      aria-label="불러오는 중"
      style={{ gap }}
    >
      {stars.map((index) => (
        <StarShape
          key={colors[index]}
          color={colors[index]}
          size={size}
          delay={(index * speed) / 3}
          speed={speed}
        />
      ))}
    </div>
  );
}
