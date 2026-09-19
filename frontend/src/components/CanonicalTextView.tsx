import { useEffect, useMemo, useRef } from "react";

import type { Fact, QuoteSpan } from "../api/types";

interface Segment {
  text: string;
  fact?: Fact;
  extra?: boolean;
}

/**
 * 按事实条目 span + 额外定位区间切分 canonical_text。
 * span 为 UTF-16 码元偏移，直接 slice（spec §8：无任何坐标换算）；
 * span_start < 0（回验失败）的条目跳过。
 *
 * extraSpan：诊断报告 quote 跳转的定位区间（spec §12.8）——它可能落在
 * 某个事实条目内，也可能落在条目未覆盖的文本上，两种都要能高亮。
 */
export function buildSegments(
  text: string,
  facts: Fact[],
  visibleFactIds?: Set<string>,
  extraSpan?: QuoteSpan | null,
): Segment[] {
  const factSpans = facts
    .filter((fact) => fact.span_start >= 0 && fact.span_end > fact.span_start)
    .filter((fact) => !visibleFactIds || visibleFactIds.has(fact.id))
    .map((fact) => ({ start: fact.span_start, end: fact.span_end, fact }));

  // 防御：重叠 span 先到先得（正常数据不重叠）
  const spans: { start: number; end: number; fact?: Fact }[] = [];
  let cursor = 0;
  for (const span of [...factSpans].sort((a, b) => a.start - b.start || a.end - b.end)) {
    if (span.start < cursor) continue;
    spans.push(span);
    cursor = span.end;
  }
  if (extraSpan && extraSpan.end > extraSpan.start) {
    spans.push({ start: extraSpan.start, end: Math.min(extraSpan.end, text.length) });
  }

  // 边界点切分（fact 与 extraSpan 可重叠）
  const points = new Set<number>([0, text.length]);
  for (const span of spans) {
    if (span.start > 0 && span.start < text.length) points.add(span.start);
    if (span.end > 0 && span.end < text.length) points.add(span.end);
  }
  const sorted = [...points].sort((a, b) => a - b);
  const segments: Segment[] = [];
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const start = sorted[i];
    const end = sorted[i + 1];
    if (end <= start) continue;
    const covering = spans.find((span) => span.start <= start && end <= span.end);
    segments.push({
      text: text.slice(start, end),
      fact: covering?.fact,
      extra: Boolean(extraSpan && extraSpan.start <= start && end <= extraSpan.end),
    });
  }
  return segments;
}

interface Props {
  text: string;
  facts: Fact[];
  visibleFactIds?: Set<string>;
  selectedFactId: string | null;
  hoveredFactId: string | null;
  onSelectFact: (factId: string) => void;
  /** 从诊断报告跳入时的 quote 定位高亮（spec §12.8） */
  extraSpan?: QuoteSpan | null;
}

/** gate1 左栏：规范原文层渲染 + span 高亮联动（spec §8）。 */
export default function CanonicalTextView({
  text,
  facts,
  visibleFactIds,
  selectedFactId,
  hoveredFactId,
  onSelectFact,
  extraSpan,
}: Props) {
  const segments = useMemo(
    () => buildSegments(text, facts, visibleFactIds, extraSpan),
    [text, facts, visibleFactIds, extraSpan],
  );
  const extraRef = useRef<HTMLElement | null>(null);

  // 跳入定位：滚动到 quote 高亮处（jsdom 无 scrollIntoView，测试环境跳过）
  useEffect(() => {
    extraRef.current?.scrollIntoView?.({ block: "center" });
  }, [extraSpan]);

  return (
    <div className="canonical-text" data-testid="canonical-text">
      {segments.map((segment, index) => {
        const isExtra = Boolean(segment.extra);
        if (!segment.fact) {
          if (!isExtra) return <span key={index}>{segment.text}</span>;
          return (
            <mark
              key={index}
              ref={extraRef}
              className="hl hl-extra"
              data-testid="extra-highlight"
            >
              {segment.text}
            </mark>
          );
        }
        const fact = segment.fact;
        const classes = ["hl"];
        if (isExtra) classes.push("hl-extra");
        if (fact.anomaly_flags.some((flag) => !flag.resolved)) classes.push("hl-marked");
        if (fact.confirmed) classes.push("hl-confirmed");
        if (fact.id === hoveredFactId) classes.push("hl-hovered");
        if (fact.id === selectedFactId) classes.push("hl-selected");
        return (
          <mark
            key={index}
            ref={isExtra ? extraRef : undefined}
            className={classes.join(" ")}
            data-fact-id={fact.id}
            onClick={() => onSelectFact(fact.id)}
          >
            {segment.text}
          </mark>
        );
      })}
    </div>
  );
}
