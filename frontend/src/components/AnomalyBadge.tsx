import { RULE_LABELS, type AnomalyFlag } from "../api/types";

interface Props {
  flag: AnomalyFlag;
  onResolve?: (flagId: string) => void;
}

/** 异象徽标：detail 一句话 tooltip（spec §8），可标记已处理。 */
export default function AnomalyBadge({ flag, onResolve }: Props) {
  const message = typeof flag.detail?.message === "string" ? flag.detail.message : flag.rule;
  const label = RULE_LABELS[flag.rule] ?? flag.rule;

  return (
    <span
      className={`anomaly-badge ${flag.resolved ? "resolved" : ""}`}
      title={message}
      data-testid={`anomaly-${flag.id}`}
    >
      <span className="anomaly-label">{label}</span>
      {!flag.resolved && onResolve && (
        <button
          type="button"
          className="badge-resolve"
          title="标记已处理"
          onClick={(event) => {
            event.stopPropagation();
            onResolve(flag.id);
          }}
        >
          已处理
        </button>
      )}
    </span>
  );
}
