import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, rewriteStream } from "../api/client";
import type { RewriteEvent } from "../api/types";
import { REWRITE_STATUS_LABELS } from "../api/types";

/**
 * 改写页（spec §21）。
 *
 * - 入口：诊断报告页「开始改写」；
 * - SSE 分步进度（writing round n → validating round n → escalated → done），
 *   断流可重试（沿用 M2 DiagnosePage 模式）；
 * - done 后进入 gate2 逐句确认页；历史改写可回溯。
 */
export default function RewritePage({ diagnosisId }: { diagnosisId: string }) {
  const queryClient = useQueryClient();
  const diagnosisQuery = useQuery({
    queryKey: ["diagnosis", diagnosisId],
    queryFn: () => api.getDiagnosis(diagnosisId),
  });
  const historyQuery = useQuery({
    queryKey: ["rewrites", diagnosisId],
    queryFn: () => api.listRewrites(diagnosisId),
  });

  const [running, setRunning] = useState(false);
  const [stepText, setStepText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const diagnosis = diagnosisQuery.data;

  const handleStart = async () => {
    setRunning(true);
    setError(null);
    setStepText("正在连接…");
    const result: { rewriteId: string | null } = { rewriteId: null };
    try {
      await rewriteStream({ diagnosis_id: diagnosisId }, (event: RewriteEvent) => {
        if (event.type === "step") {
          if (event.step === "writing") {
            setStepText(
              event.round === 0
                ? "改写第 1 轮：正在生成句集…"
                : `重写第 ${event.round} 轮：正在修复未通过句…`,
            );
          } else if (event.step === "validating") {
            setStepText(`校验第 ${event.round + 1} 轮（数字 / 归因 / 衍生 / 语义）…`);
          } else {
            setStepText("存在升级项（硬性缺口 / 无支撑句），已附回报告");
          }
        } else if (event.type === "done") {
          result.rewriteId = event.rewrite_id;
        } else {
          setError(event.message);
        }
      });
      if (result.rewriteId) {
        void queryClient.invalidateQueries({ queryKey: ["rewrites", diagnosisId] });
        window.location.hash = `#/gate2/${result.rewriteId}`;
        return;
      }
      setError((prev) => prev ?? "改写未完成（连接中断），请重试");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
      setStepText(null);
    }
  };

  if (diagnosisQuery.isLoading || historyQuery.isLoading) {
    return <div className="page">加载中…</div>;
  }
  if (diagnosisQuery.error) {
    return <div className="page error-card">{(diagnosisQuery.error as Error).message}</div>;
  }
  if (!diagnosis) return null;

  const summary = diagnosis.summary;
  const history = historyQuery.data ?? [];

  return (
    <div className="page rewrite-page">
      <div className="review-toolbar">
        <div className="toolbar-left">
          <a className="btn-ghost btn-sm" href={`#/diagnosis/${diagnosisId}`}>
            ← 诊断报告
          </a>
          <span className="file-name">改写</span>
          <span className="meta-chip">{diagnosis.created_at}</span>
          {diagnosis.mock && (
            <span className="meta-chip" title="该诊断由 mock 规则生成，质量不代表真实水平">
              mock 诊断
            </span>
          )}
        </div>
      </div>

      <div className="card">
        <h2>改写目标清单（分母修正后）</h2>
        <p className="pane-hint">
          目标 = 有直接引用 / 最接近 / 人工捞回的条目；硬性缺口一律剔除——
          注定无证据的要求留在分母只会制造擦边压力（ard/0004）。
        </p>
        <div className="rewrite-summary-chips">
          <span className="meta-chip">硬性要求 {summary.must_total}</span>
          <span className="meta-chip">直接引用 {summary.must_direct}</span>
          <span className="meta-chip">最接近 {summary.must_nearest}</span>
          <span className={`meta-chip ${summary.must_gap > 0 ? "chip-danger" : ""}`}>
            缺口 {summary.must_gap}（剔除出改写）
          </span>
        </div>
        <div className="jd-actions">
          <button
            type="button"
            className="btn"
            data-testid="start-rewrite"
            disabled={running}
            onClick={() => void handleStart()}
          >
            {running ? "改写中…" : "开始改写"}
          </button>
          {running && stepText && (
            <span className="diagnose-progress" data-testid="rewrite-progress">
              {stepText}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="card error-card" data-testid="rewrite-error">
          {error}
        </div>
      )}

      {history.length > 0 && (
        <div className="card">
          <h2>历史改写</h2>
          <ul className="diagnosis-list">
            {history.map((item) => (
              <li key={item.id} data-testid={`rewrite-item-${item.id}`}>
                <a className="diagnosis-link" href={`#/gate2/${item.id}`}>
                  {item.created_at}
                </a>
                {item.mock && <span className="meta-chip">mock</span>}
                <span className="meta-chip">{REWRITE_STATUS_LABELS[item.status] ?? item.status}</span>
                <span className="meta-chip">
                  句 {item.sentence_count} · 重写 {item.rounds} 轮
                  {item.escalations > 0 ? ` · 升级 ${item.escalations}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
