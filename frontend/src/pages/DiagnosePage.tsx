import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, diagnoseStream } from "../api/client";
import type { DiagnoseEvent } from "../api/types";

/**
 * JD 诊断页（spec §12.8）。
 *
 * - 入口：ReviewPage 完成校对后 / 首页选择已 confirmed 简历；
 * - 粘贴 JD → SSE 分步进度（拆解 JD → 举证 n/N → 生成报告）；
 * - 完成后进入报告页；断流可重试（重试产生新诊断行，不覆盖历史）。
 */
export default function DiagnosePage({ resumeId }: { resumeId: string }) {
  const queryClient = useQueryClient();
  const resumeQuery = useQuery({
    queryKey: ["resume", resumeId],
    queryFn: () => api.getResume(resumeId),
  });
  const historyQuery = useQuery({
    queryKey: ["diagnoses", resumeId],
    queryFn: () => api.listDiagnoses(resumeId),
  });

  const [jdText, setJdText] = useState("");
  const [running, setRunning] = useState(false);
  const [stepText, setStepText] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resume = resumeQuery.data;
  const confirmed = resume?.status === "confirmed";

  const handleStart = async () => {
    setRunning(true);
    setError(null);
    setStepText("正在连接…");
    setProgress(null);
    // 对象包装：SSE 回调内赋值，避免闭包变量在 TS 流分析中的收窄问题
    const result: { diagnosisId: string | null } = { diagnosisId: null };
    try {
      await diagnoseStream({ resume_id: resumeId, jd_text: jdText }, (event: DiagnoseEvent) => {
        if (event.type === "step") {
          if (event.step === "evidencing") {
            setStepText(event.message ?? `逐条举证 ${event.index + 1}/${event.total}`);
            setProgress({ done: event.index + 1, total: event.total });
          } else if (event.step === "parsing") {
            setStepText("正在拆解 JD 要求清单…");
          } else {
            setStepText("正在汇总诊断报告…");
          }
        } else if (event.type === "done") {
          result.diagnosisId = event.diagnosis_id;
        } else {
          setError(event.message);
        }
      });
      if (result.diagnosisId) {
        void queryClient.invalidateQueries({ queryKey: ["diagnoses", resumeId] });
        window.location.hash = `#/diagnosis/${result.diagnosisId}`;
        return;
      }
      setError((prev) => prev ?? "诊断未完成（连接中断），请重试");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
      setStepText(null);
      setProgress(null);
    }
  };

  if (resumeQuery.isLoading || historyQuery.isLoading) {
    return <div className="page">加载中…</div>;
  }
  if (resumeQuery.error) {
    return <div className="page error-card">{(resumeQuery.error as Error).message}</div>;
  }

  const history = historyQuery.data ?? [];

  return (
    <div className="page diagnose-page">
      <div className="review-toolbar">
        <div className="toolbar-left">
          <a className="btn-ghost btn-sm" href={`#/review/${resumeId}`}>
            ← 校对页
          </a>
          <span className="file-name" title={resumeId}>
            {resume?.filename}
          </span>
          <span className={`status-chip status-${resume?.status}`}>
            {confirmed ? "已完成校对" : "校对中"}
          </span>
          {resume?.mock && (
            <span className="meta-chip" title="该版本解析来源为 mock 规则，质量不代表真实水平">
              mock 解析
            </span>
          )}
        </div>
      </div>

      {!confirmed && (
        <div className="card warn-card">
          该简历尚未完成事实源校对：诊断只依据已确认的事实，
          请先在 <a href={`#/review/${resumeId}`}>校对页</a> 完成全部条目确认（ard/0002）。
        </div>
      )}

      <div className="card jd-card">
        <h2>粘贴目标岗位 JD</h2>
        <p className="pane-hint">
          每条要求都会在事实源中逐条举证——找不到可逐字验证的证据就如实报缺口（保守偏向，ard/0004）。
        </p>
        <textarea
          className="jd-input"
          data-testid="jd-input"
          placeholder="把职位描述粘贴到这里…"
          value={jdText}
          rows={10}
          disabled={running || !confirmed}
          onChange={(event) => setJdText(event.target.value)}
        />
        <div className="jd-actions">
          <button
            type="button"
            className="btn"
            data-testid="start-diagnose"
            disabled={running || !confirmed || !jdText.trim()}
            onClick={() => void handleStart()}
          >
            {running ? "诊断中…" : "开始诊断"}
          </button>
          {running && stepText && (
            <span className="diagnose-progress" data-testid="diagnose-progress">
              {stepText}
              {progress ? `（${progress.done}/${progress.total}）` : ""}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="card error-card" data-testid="diagnose-error">
          {error}
        </div>
      )}

      {history.length > 0 && (
        <div className="card">
          <h2>历史诊断</h2>
          <ul className="diagnosis-list">
            {history.map((item) => (
              <li key={item.id} data-testid={`diagnosis-item-${item.id}`}>
                <a className="diagnosis-link" href={`#/diagnosis/${item.id}`}>
                  {item.created_at}
                </a>
                {item.mock && <span className="meta-chip">mock</span>}
                {item.summary && (
                  <span className="meta-chip">
                    硬性要求 {item.summary.must_total} · 直接引用 {item.summary.must_direct} · 缺口{" "}
                    {item.summary.must_gap}
                  </span>
                )}
                {item.summary && !item.summary.worth_applying && (
                  <span className="meta-chip chip-danger">全 must 缺口</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
