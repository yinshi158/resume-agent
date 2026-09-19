import { useMemo } from "react";

import { SECTION_LABELS, type Attribution, type Fact, type FactPatch, type Section } from "../api/types";
import AnomalyBadge from "./AnomalyBadge";
import AttributionControl from "./AttributionControl";

const SECTION_ORDER: Section[] = ["summary", "work", "project", "education", "skill", "other"];

interface RowProps {
  fact: Fact;
  selected: boolean;
  onSelect: (factId: string) => void;
  onHover: (factId: string | null) => void;
  onPatch: (factId: string, patch: FactPatch) => void;
  onResolveFlag: (flagId: string) => void;
  onEdit: (fact: Fact) => void;
}

function FactRow({ fact, selected, onSelect, onHover, onPatch, onResolveFlag, onEdit }: RowProps) {
  const unresolvedFlags = fact.anomaly_flags.filter((flag) => !flag.resolved);
  const needsAttribution = fact.attribution !== "individual" && !fact.attribution_confirmed;
  const canConfirm = fact.attribution === "individual" || fact.attribution_confirmed;
  const payloadText = fact.edited_payload ? "已人工修正" : "";

  return (
    <div
      className={`fact-row ${selected ? "selected" : ""} ${fact.confirmed ? "confirmed" : ""}`}
      data-testid={`fact-row-${fact.id}`}
      onMouseEnter={() => onHover(fact.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(fact.id)}
    >
      <div className="fact-main">
        <div className="fact-quote">{fact.raw_quote || "（无原文片段）"}</div>
        <div className="fact-tags">
          {unresolvedFlags.map((flag) => (
            <AnomalyBadge key={flag.id} flag={flag} onResolve={onResolveFlag} />
          ))}
          {needsAttribution && (
            <span className="tag tag-pending" title="团队/混合/不确定的归因必须人工表态（ard/0002）">
              待确认归因
            </span>
          )}
          {payloadText && <span className="tag tag-edited">{payloadText}</span>}
        </div>
      </div>
      <div className="fact-actions">
        <AttributionControl
          value={fact.attribution}
          confirmed={fact.attribution_confirmed}
          onChange={(value: Attribution) =>
            onPatch(fact.id, { attribution: value, attribution_confirmed: true })
          }
        />
        <div className="fact-right">
          <label className="confirm-box" title={canConfirm ? "标记该条目已完成校对" : "请先确认归因"}>
            <input
              type="checkbox"
              checked={fact.confirmed}
              disabled={!canConfirm && !fact.confirmed}
              onChange={(event) => onPatch(fact.id, { confirmed: event.target.checked })}
            />
            已校对
          </label>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={(event) => {
              event.stopPropagation();
              onEdit(fact);
            }}
          >
            编辑字段
          </button>
        </div>
      </div>
    </div>
  );
}

interface Props {
  facts: Fact[];
  selectedFactId: string | null;
  onSelect: (factId: string) => void;
  onHover: (factId: string | null) => void;
  onPatch: (factId: string, patch: FactPatch) => void;
  onResolveFlag: (flagId: string) => void;
  onEdit: (fact: Fact) => void;
}

/** 右栏事实表：按 section 分组（spec §8）。 */
export default function FactTable({ facts, selectedFactId, onSelect, onHover, onPatch, onResolveFlag, onEdit }: Props) {
  const groups = useMemo(() => {
    const bySection = new Map<Section, Fact[]>();
    for (const section of SECTION_ORDER) bySection.set(section, []);
    for (const fact of facts) bySection.get(fact.section)?.push(fact);
    return SECTION_ORDER.map((section) => ({ section, items: bySection.get(section) ?? [] })).filter(
      (group) => group.items.length > 0,
    );
  }, [facts]);

  if (facts.length === 0) {
    return <div className="empty-hint">当前模式下没有条目。可切换"首次全量"查看全部事实源。</div>;
  }

  return (
    <div className="fact-table">
      {groups.map((group) => (
        <section key={group.section} className="fact-group">
          <h3>
            {SECTION_LABELS[group.section]}
            <span className="count">{group.items.length}</span>
          </h3>
          {group.items.map((fact) => (
            <FactRow
              key={fact.id}
              fact={fact}
              selected={fact.id === selectedFactId}
              onSelect={onSelect}
              onHover={onHover}
              onPatch={onPatch}
              onResolveFlag={onResolveFlag}
              onEdit={onEdit}
            />
          ))}
        </section>
      ))}
    </div>
  );
}
