import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { DiagnosisRequirement, Fact } from "../api/types";
import { REQ_STATUS_LABELS } from "../api/types";

/**
 * 诊断报告页（spec §12.8）。
 *
 * - summary 卡（worth_applying 显著提示）→ must 组 → preferred 组；
 * - 状态徽标（有证据/最接近/缺口）；quote 点击跳校对页定位高亮；
 * - nearest/gap 行内"最接近的是 X，相关吗？"一键捞回（ard/0004），
 *   纯缺口可从事实源选择一条经历捞回；重算由后端确定性完成。
 */
export default function DiagnosisReportPage({ diagnosisId }: { diagnosisId: string }) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ["diagnosis", diagnosisId],
    queryFn: () => api.getDiagnosis(diagnosisId),
  });
  const detail = detailQuery.data;
  const factsQuery = useQuery({
    queryKey: ["facts", detail?.resume_id ?? ""],
    queryFn: () => api.getFacts(detail!.resume_id),
    enabled: Boolean(detail),
  });
  const [notice, setNotice] = useState<string | null>(null);

  const reviveMutation = useMutation({
    mutationFn: (payload: { requirementId: string; factId: string }) =>
      api.reviveRequirement(payload.requirementId, payload.factId),
    onSuccess: () => {
      setNotice(null);
      void queryClient.invalidateQueries({ queryKey: ["diagnosis", diagnosisId] });
    },
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  if (detailQuery.isLoading) {
    return <div className="page">加载中…</div>;
  }
  if (detailQuery.error) {
    return <div className="page error-card">{(detailQuery.error as Error).message}</div>;
  }
  if (!detail) return null;

  const must = detail.requirements.filter((req) => req.priority === "must");
  const preferred = detail.requirements.filter((req) => req.priority === "preferred");
  const summary = detail.summary;

  const jumpToQuote = (req: DiagnosisRequirement) => {
    if (!req.quote_span) return;
    window.location.hash = `#/review/${detail.resume_id}?span=${req.quote_span.start}-${req.quote_span.end}`;
  };

  const handleRevive = (requirementId: string, factId: string) =>
    reviveMutation.mutate({ requirementId, factId });

  return (
    <div className="page report-page">
      <div className="review-toolbar">
        <div className="toolbar-left">
          <a className="btn-ghost btn-sm" href={`#/diagnose/${detail.resume_id}`}>
            ← 诊断页
          </a>
          <span className="file-name">诊断报告</span>
          <span className="meta-chip">{detail.created_at}</span>
          {detail.mock && (
            <span className="meta-chip" title="该次诊断由 mock 规则生成，质量不代表真实水平">
              mock 诊断
            </span>
          )}
        </div>
        <div className="toolbar-right">
          <a className="btn-ghost btn-sm" href={`#/review/${detail.resume_id}`}>
            查看事实源
          </a>
          {/* worth_applying=false 时按钮保留但提示风险——用户有权低覆盖投递（spec §21） */}
          <a
            className={`btn btn-sm ${summary.worth_applying ? "" : "btn-risk"}`}
            href={`#/rewrite/${diagnosisId}`}
            data-testid="start-rewrite-entry"
            title={
              summary.worth_applying
                ? "按此诊断的目标清单改写（硬性缺口剔除出分母，ard/0004）"
                : "该岗位硬性要求在事实源中均无对应经历，改写覆盖度可能很低——你仍有权投递"
            }
          >
            开始改写 →
          </a>
        </div>
      </div>

      {notice && <div className="notice-bar">{notice}</div>}

      <div className="report-disclaimer" data-testid="report-disclaimer">
        引用仅经程序验证逐字定位，相关性将在改写校验阶段核验；「最接近 / 缺口」仅表示词法层面未命中，可人工捞回。
      </div>

      <div className={`card summary-card ${summary.worth_applying ? "" : "summary-warn"}`}>
        <div className="summary-metrics">
          <div className="metric">
            <div className="metric-value">{summary.must_total}</div>
            <div className="metric-label">硬性要求</div>
          </div>
          <div className="metric metric-ok">
            <div className="metric-value">{summary.must_direct}</div>
            <div className="metric-label">直接引用</div>
          </div>
          <div className="metric metric-warn">
            <div className="metric-value">{summary.must_nearest}</div>
            <div className="metric-label">最接近</div>
          </div>
          <div className="metric metric-danger">
            <div className="metric-value">{summary.must_gap}</div>
            <div className="metric-label">缺口</div>
          </div>
          <div className="metric">
            <div className="metric-value">
              {summary.preferred_direct}/{summary.preferred_total}
            </div>
            <div className="metric-label">加分项命中</div>
          </div>
        </div>
        {!summary.worth_applying && (
          <div className="worth-warning" data-testid="worth-warning">
            {summary.notice ?? "该岗位硬性要求在事实源中均无对应经历，可能不值得投。"}
          </div>
        )}
        {summary.worth_applying && summary.must_gap > 0 && (
          <div className="summary-hint">
            硬性缺口 {summary.must_gap} 条：M3 改写会将其剔除出目标分母（ard/0004）；
            若某条实际相关，可在行内一键捞回。
          </div>
        )}
      </div>

      <details className="card jd-collapse">
        <summary>JD 原文（{detail.jd_text.length} 字）</summary>
        <pre className="jd-pre">{detail.jd_text}</pre>
      </details>

      {detail.warnings.length > 0 && (
        <div className="card warn-card">
          <h3>诊断提示</h3>
          <ul>
            {detail.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <ReqGroup
        title={`硬性要求（${must.length}）`}
        requirements={must}
        facts={factsQuery.data ?? []}
        busy={reviveMutation.isPending}
        onJumpQuote={jumpToQuote}
        onRevive={handleRevive}
      />
      {preferred.length > 0 && (
        <ReqGroup
          title={`加分项（${preferred.length}）`}
          requirements={preferred}
          facts={factsQuery.data ?? []}
          busy={reviveMutation.isPending}
          onJumpQuote={jumpToQuote}
          onRevive={handleRevive}
        />
      )}
    </div>
  );
}

interface ReqGroupProps {
  title: string;
  requirements: DiagnosisRequirement[];
  facts: Fact[];
  busy: boolean;
  onJumpQuote: (req: DiagnosisRequirement) => void;
  onRevive: (requirementId: string, factId: string) => void;
}

function ReqGroup({ title, requirements, facts, busy, onJumpQuote, onRevive }: ReqGroupProps) {
  return (
    <section className="card req-group">
      <h2>{title}</h2>
      <div className="req-list">
        {requirements.map((req) => (
          <RequirementRow
            key={req.id}
            req={req}
            facts={facts}
            busy={busy}
            onJumpQuote={onJumpQuote}
            onRevive={onRevive}
          />
        ))}
      </div>
    </section>
  );
}

interface RequirementRowProps {
  req: DiagnosisRequirement;
  facts: Fact[];
  busy: boolean;
  onJumpQuote: (req: DiagnosisRequirement) => void;
  onRevive: (requirementId: string, factId: string) => void;
}

function RequirementRow({ req, facts, busy, onJumpQuote, onRevive }: RequirementRowProps) {
  const [pickedFactId, setPickedFactId] = useState("");
  // 下拉分两组：后端给的降级候选（可考虑的候选）→ 其余可定位条目；
  // 不可定位条目（span=-1）捞回必失败，不列入
  const candidateIds = new Set((req.candidate_facts ?? []).map((fact) => fact.id));
  const otherFacts = facts.filter(
    (fact) => fact.span_start >= 0 && !candidateIds.has(fact.id),
  );

  return (
    <div className={`req-row req-${req.status}`} data-testid={`req-row-${req.id}`}>
      {/* 证据强度区分（P3 审查）：revive = fact_id 锚定（人工判定）→「人工确认」；
          pipeline direct = quote 唯一定位（程序判定）→「有直接引用」 */}
      <span className={`req-badge req-badge-${req.status}`}>
        {req.user_revived ? "人工确认" : REQ_STATUS_LABELS[req.status]}
      </span>
      <div className="req-body">
        <div className="req-text">
          {req.text}
          {req.user_revived && <span className="revived-chip">人工捞回</span>}
        </div>
        {req.keywords.length > 0 && (
          <div className="req-keywords">关键词：{req.keywords.join("、")}</div>
        )}
        {req.status === "direct" &&
          (req.quote_span ? (
            <button
              type="button"
              className="req-quote req-quote-link"
              title="在校对页定位这段原文"
              onClick={() => onJumpQuote(req)}
            >
              “{req.quote}”
            </button>
          ) : (
            <span className="req-quote">“{req.quote}”</span>
          ))}
        {(req.status === "nearest" || req.status === "gap") && (
          <div className="req-note">
            <span className="req-note-text">{req.nearest_note ?? "无直接证据"}</span>
            {req.fact_id ? (
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={busy}
                data-testid={`revive-${req.id}`}
                onClick={() => onRevive(req.id, req.fact_id!)}
              >
                相关，捞回
              </button>
            ) : (
              <span className="req-pick">
                <select
                  aria-label="选择一条事实源经历"
                  value={pickedFactId}
                  onChange={(event) => setPickedFactId(event.target.value)}
                >
                  <option value="">选择一条经历…</option>
                  {(req.candidate_facts?.length ?? 0) > 0 && (
                    <optgroup label="可考虑的候选">
                      {req.candidate_facts!.map((fact) => (
                        <option key={fact.id} value={fact.id}>
                          {fact.raw_quote.slice(0, 40)}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  <optgroup label="全部条目">
                    {otherFacts.map((fact) => (
                      <option key={fact.id} value={fact.id}>
                        {fact.raw_quote.slice(0, 40)}
                      </option>
                    ))}
                  </optgroup>
                </select>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  disabled={busy || !pickedFactId}
                  data-testid={`revive-pick-${req.id}`}
                  onClick={() => onRevive(req.id, pickedFactId)}
                >
                  捞回
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
