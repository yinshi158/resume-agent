import type { Attribution } from "../api/types";

const OPTIONS: { value: Attribution; label: string; hint: string }[] = [
  { value: "individual", label: "个人", hint: "我独立完成的成果" },
  { value: "team", label: "团队", hint: "团队成果（改写时只许参与级动词）" },
  { value: "mixed", label: "混合", hint: "个人主导 + 团队协作" },
  { value: "unknown", label: "不确定", hint: "暂时无法确定归因" },
];

interface Props {
  value: Attribution;
  confirmed: boolean;
  onChange: (value: Attribution) => void;
}

/**
 * 归因四态控件（ard/0002）。
 * team / mixed / unknown 未确认时条目为"待确认"态；选择即表态（attribution_confirmed=true）。
 */
export default function AttributionControl({ value, confirmed, onChange }: Props) {
  const needsConfirmation = value !== "individual" && !confirmed;

  return (
    <div
      className={`attribution-control ${needsConfirmation ? "needs-confirmation" : ""}`}
      role="radiogroup"
      aria-label="归因"
    >
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          className={`attr-btn ${value === option.value ? "active" : ""}`}
          title={option.hint}
          onClick={(event) => {
            event.stopPropagation();
            onChange(option.value);
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
