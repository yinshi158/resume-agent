import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, exportStream } from "../api/client";
import type {
  ExportEvent,
  LayerValidation,
  RewriteDetail,
  RewriteRequirementBrief,
  RewriteSentence,
  Section,
} from "../api/types";
import {
  GATE_STATUS_LABELS,
  LAYER_LABELS,
  REWRITE_STATUS_LABELS,
  SECTION_LABELS,
} from "../api/types";

/**
 * gate2 逐句确认页（spec §17.4，核心咽喉）。
 *
 * - 逐句卡片：改写句（内联编辑）↔ 出处对照（点击跳校对页定位高亮）；
 * - 判定徽标：L0/归因/L1/L2 各层红标 + detail 一句话 tooltip；
 * - 操作：确认 / 拒绝 / 编辑；全部处置完成后导出按钮解锁；
 * - 顶栏：升级清单提示（硬性缺口回诊断报告入口）。
 */

const LAYER_ORDER = ["L0", "verb", "L1", "L2"] as const;

function latestLayers(sentence: RewriteSentence): LayerValidation[] {
  const rounds = sentence.validations;
  if (rounds.length === 0) return [];
  return rounds[rounds.length - 1].layers;
}

function hasFailure(sentence: RewriteSentence): boolean {
  return latestLayers(sentence).some((layer) => layer.verdict === "fail");
}

export default function Gate2Page({ rewriteId }: { rewriteId: string }) {
  const queryClient = useQueryClient();
  const rewriteQuery = useQuery({
    queryKey: ["rewrite", rewriteId],
    queryFn: () => api.getRewrite(rewriteId),
  });
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: () => api.getSettings() });
  const [notice, setNotice] = useState<string | null>(null);

  const gateMutation = useMutation({
    mutationFn: (payload: { sentenceId: string; action: "confirm" | "reject" | "edit"; text?: string }) =>
      api.gateSentence(payload.sentenceId, payload.action, payload.text),
    onSuccess: (data) => {
      setNotice(null);
      queryClient.setQueryData<RewriteDetail>(["rewrite", rewriteId], (current) =>
        current
          ? {
              ...current,
              sentences: current.sentences.map((sentence) =>
                sentence.id === data.sentence_id
                  ? { ...sentence, gate_status: data.gate_status, text: data.text }
                  : sentence,
              ),
            }
          : current,
      );
      // 编辑会产生新一轮判定；重取拿到完整判定历史
      void queryClient.invalidateQueries({ queryKey: ["rewrite", rewriteId] });
    },
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  if (rewriteQuery.isLoading) {
    return <div className="page">加载中…</div>;
  }
  if (rewriteQuery.error) {
    return <div className="page error-card">{(rewriteQuery.error as Error).message}</div>;
  }
  const rewrite = rewriteQuery.data;
  if (!rewrite) return null;

  const sentences = rewrite.sentences;
  const pendingCount = sentences.filter((sentence) => sentence.gate_status === "pending").length;
  const handledCount = sentences.length - pendingCount;
  // spec §17.2：2 轮未过 / 标红句置前（人工优先处理）
  const displaySentences = [...sentences].sort((a, b) => {
    const failedDiff = Number(hasFailure(b)) - Number(hasFailure(a));
    return failedDiff !== 0 ? failedDiff : a.seq - b.seq;
  });

  const requirementById = new Map(rewrite.requirements.map((req) => [req.id, req]));

  return (
    <div className="page gate2-page">
      <div className="review-toolbar">
        <div className="toolbar-left">
          <a className="btn-ghost btn-sm" href={`#/rewrite/${rewrite.diagnosis_id}`}>
            ← 改写页
          </a>
          <span className="file-name">逐句确认（gate2）</span>
          <span className="meta-chip">{rewrite.created_at}</span>
          <span className="meta-chip">
            {REWRITE_STATUS_LABELS[rewrite.status] ?? rewrite.status}
          </span>
          {rewrite.rounds > 0 && <span className="meta-chip">重写 {rewrite.rounds} 轮</span>}
          {rewrite.mock && (
            <span
              className="meta-chip"
              title="该次改写/校验的生成来源含 mock 规则，质量不代表真实水平"
            >
              mock 改写
            </span>
          )}
        </div>
        <div className="toolbar-right">
          <span className="meta-chip" data-testid="handled-count">
            已处置 {handledCount}/{sentences.length}
          </span>
        </div>
      </div>

      {rewrite.escalations.length > 0 && (
        <div className="card escalation-banner" data-testid="escalation-banner">
          <h3>升级清单（{rewrite.escalations.length}）</h3>
          <p className="pane-hint">
            硬性缺口与"声明出处不蕴含"的句子不参与重试——回诊断报告补充经历，
            或在此直接处置（拒绝 / 编辑）。
          </p>
          <ul className="escalation-list">
            {rewrite.escalations.map((item, index) => (
              <li key={`${item.kind}-${index}`} className="escalation-item">
                <span className={`escalation-kind escalation-${item.kind}`}>
                  {item.kind === "requirement_gap" ? "硬性缺口" : "无支撑句"}
                </span>
                <span className="escalation-text">{item.message}</span>
                <a className="btn-ghost btn-sm" href={`#/diagnosis/${rewrite.diagnosis_id}`}>
                  回诊断报告
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {notice && <div className="notice-bar">{notice}</div>}

      <div className="sentence-list">
        {displaySentences.map((sentence) => (
          <SentenceCard
            key={sentence.id}
            sentence={sentence}
            requirements={requirementById}
            resumeId={rewrite.resume_id}
            busy={gateMutation.isPending}
            onGate={(action, text) =>
              gateMutation.mutate({ sentenceId: sentence.id, action, text })
            }
          />
        ))}
      </div>

      <ExportPanel
        rewriteId={rewriteId}
        pendingCount={pendingCount}
        totalCount={sentences.length}
        playwrightAvailable={settingsQuery.data?.playwright_available ?? false}
        onExported={() => void queryClient.invalidateQueries({ queryKey: ["rewrite", rewriteId] })}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 逐句卡片
// ---------------------------------------------------------------------------

interface SentenceCardProps {
  sentence: RewriteSentence;
  requirements: Map<string, RewriteRequirementBrief>;
  resumeId: string;
  busy: boolean;
  onGate: (action: "confirm" | "reject" | "edit", text?: string) => void;
}

function SentenceCard({ sentence, requirements, resumeId, busy, onGate }: SentenceCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(sentence.text);

  const layers = latestLayers(sentence);
  const failed = layers.some((layer) => layer.verdict === "fail");
  const roundCount = sentence.validations.length;

  const startEdit = () => {
    setDraft(sentence.text);
    setEditing(true);
  };
  const saveEdit = () => {
    const text = draft.trim();
    if (!text) return;
    onGate("edit", text);
    setEditing(false);
  };

  return (
    <div
      className={`card sentence-card ${failed ? "sentence-red" : ""}`}
      data-testid={`sentence-${sentence.id}`}
    >
      <div className="sentence-head">
        <span className="meta-chip">{SECTION_LABELS[sentence.section as Section]}</span>
        <span className={`gate-chip gate-${sentence.gate_status}`}>
          {GATE_STATUS_LABELS[sentence.gate_status]}
        </span>
        {roundCount > 1 && <span className="meta-chip">校验 {roundCount} 轮</span>}
        <span className="sentence-layers">
          {LAYER_ORDER.map((layerName) => {
            const layer = layers.find((item) => item.layer === layerName);
            if (!layer) return null;
            return (
              <span
                key={layerName}
                className={`layer-badge ${layer.verdict === "fail" ? "layer-fail" : "layer-pass"}`}
                title={layer.detail.message ?? ""}
                data-testid={`layer-${sentence.id}-${layerName}`}
              >
                {LAYER_LABELS[layerName]}
                {layer.verdict === "fail" ? "✗" : "✓"}
                {layer.detail.reused ? "·沿用" : ""}
                {layer.detail.error ? "·故障" : ""}
              </span>
            );
          })}
        </span>
      </div>

      {editing ? (
        <div className="sentence-edit">
          <textarea
            className="jd-input sentence-edit-area"
            data-testid={`edit-area-${sentence.id}`}
            value={draft}
            rows={3}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="sentence-actions">
            <button type="button" className="btn btn-sm" data-testid={`save-${sentence.id}`} onClick={saveEdit}>
              保存修改
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(false)}>
              取消
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="sentence-text">{sentence.text}</p>
          {sentence.text !== sentence.original_text && (
            <p className="sentence-original">LLM 原句：{sentence.original_text}</p>
          )}
        </>
      )}

      {sentence.derived.length > 0 && (
        <div className="sentence-derived">
          衍生数字：
          {sentence.derived.map((item, index) => (
            <span key={index} className="meta-chip" title={`formula: ${item.formula}`}>
              {item.value} = {item.formula}
            </span>
          ))}
        </div>
      )}

      <div className="sentence-sources">
        <span className="sources-label">出处：</span>
        {sentence.source_facts.map((fact) =>
          fact.missing || !fact.raw_quote ? (
            <span key={fact.id} className="source-chip source-missing" title="出处条目缺失或未确认">
              {fact.id.slice(0, 8)}…
            </span>
          ) : fact.locatable && (fact.span_start ?? -1) >= 0 ? (
            <button
              key={fact.id}
              type="button"
              className="source-chip source-link"
              title={`${fact.raw_quote}\n（点击在校对页定位原文）`}
              data-testid={`source-${sentence.id}-${fact.id}`}
              onClick={() => {
                window.location.hash = `#/review/${resumeId}?span=${fact.span_start}-${fact.span_end}`;
              }}
            >
              {fact.raw_quote.slice(0, 36)}
            </button>
          ) : (
            <span key={fact.id} className="source-chip" title={fact.raw_quote}>
              {fact.raw_quote.slice(0, 36)}
            </span>
          ),
        )}
      </div>

      {sentence.requirement_ids.length > 0 && (
        <div className="sentence-requirements">
          服务要求：
          {sentence.requirement_ids.map((reqId) => {
            const req = requirements.get(reqId);
            if (!req) return null;
            return (
              <span key={reqId} className="req-inline" title={req.text}>
                {req.priority === "must" ? "硬性" : "加分"}：{req.text.slice(0, 28)}
              </span>
            );
          })}
        </div>
      )}

      <div className="sentence-actions">
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy || sentence.gate_status === "confirmed"}
          data-testid={`confirm-${sentence.id}`}
          onClick={() => onGate("confirm")}
        >
          确认采用
        </button>
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={busy || sentence.gate_status === "rejected"}
          data-testid={`reject-${sentence.id}`}
          onClick={() => onGate("reject")}
        >
          拒绝
        </button>
        {!editing && (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            data-testid={`edit-${sentence.id}`}
            onClick={startEdit}
          >
            编辑
          </button>
        )}
        {failed && (
          <span className="sentence-red-hint">
            存在未通过判定——确认前请核对出处，或直接编辑/拒绝
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 导出面板（§17.2 咽喉 + §21 导出与「记录投递」）
// ---------------------------------------------------------------------------

interface ExportPanelProps {
  rewriteId: string;
  pendingCount: number;
  totalCount: number;
  playwrightAvailable: boolean;
  onExported: () => void;
}

function ExportPanel({
  rewriteId,
  pendingCount,
  totalCount,
  playwrightAvailable,
  onExported,
}: ExportPanelProps) {
  const [exporting, setExporting] = useState(false);
  const [error, setExportError] = useState<string | null>(null);
  const [result, setResult] = useState<{ versionId: string; fileUrl: string } | null>(null);

  const locked = pendingCount > 0 || totalCount === 0;

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    const captured: { versionId: string | null; fileUrl: string | null } = {
      versionId: null,
      fileUrl: null,
    };
    try {
      await exportStream({ rewrite_id: rewriteId }, (event: ExportEvent) => {
        if (event.type === "done") {
          captured.versionId = event.version_id;
          captured.fileUrl = event.file_url;
        } else if (event.type === "error") {
          setExportError(event.message);
        }
      });
      if (captured.versionId && captured.fileUrl) {
        setResult({ versionId: captured.versionId, fileUrl: captured.fileUrl });
        onExported();
      } else {
        setExportError((prev) => prev ?? "导出未完成（连接中断），请重试");
      }
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="card export-panel" data-testid="export-panel">
      <h2>导出（ATS 单模板 PDF）</h2>
      <p className="pane-hint">
        导出内容 = 已确认 + 已编辑句（拒绝剔除）；单栏、标准章节名、无表格文本框，
        关键词中英文并写。全部句子处置完成后解锁（第二咽喉，ard/0002）。
      </p>
      {!playwrightAvailable && (
        <div className="warn-card" data-testid="playwright-missing">
          未检测到 Playwright 浏览器（导出唯一渲染路径，R4）。请在后端执行：
          <code>pip install playwright && python -m playwright install chromium</code>
        </div>
      )}
      <div className="jd-actions">
        <button
          type="button"
          className="btn"
          data-testid="export-pdf"
          disabled={exporting || locked || !playwrightAvailable}
          onClick={() => void handleExport()}
        >
          {exporting ? "渲染中…" : "导出 PDF"}
        </button>
        {locked && totalCount > 0 && (
          <span className="diagnose-progress" data-testid="export-locked-hint">
            还有 {pendingCount} 句待处置
          </span>
        )}
      </div>

      {error && (
        <div className="error-card" data-testid="export-error">
          {error}
        </div>
      )}

      {result && (
        <div className="export-result" data-testid="export-result">
          <a className="btn btn-sm" href={result.fileUrl} target="_blank" rel="noreferrer">
            下载 PDF
          </a>
          <RecordApplicationForm versionId={result.versionId} />
        </div>
      )}
    </div>
  );
}

function RecordApplicationForm({ versionId }: { versionId: string }) {
  const [company, setCompany] = useState("");
  const [position, setPosition] = useState("");
  const [channel, setChannel] = useState("");
  const [recorded, setRecorded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.createApplication({ version_id: versionId, company, position, channel }),
    onSuccess: () => {
      setRecorded(true);
      setError(null);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  if (recorded) {
    return (
      <span className="meta-chip" data-testid="application-recorded">
        已记录投递（结果可后续追加，v1 只记录不分析）
      </span>
    );
  }

  return (
    <form
      className="application-form"
      data-testid="application-form"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <input
        className="app-input"
        placeholder="公司"
        aria-label="公司"
        value={company}
        onChange={(event) => setCompany(event.target.value)}
      />
      <input
        className="app-input"
        placeholder="岗位"
        aria-label="岗位"
        value={position}
        onChange={(event) => setPosition(event.target.value)}
      />
      <input
        className="app-input"
        placeholder="渠道（可选）"
        aria-label="渠道"
        value={channel}
        onChange={(event) => setChannel(event.target.value)}
      />
      <button
        type="submit"
        className="btn-ghost btn-sm"
        data-testid="record-application"
        disabled={mutation.isPending || !company.trim() || !position.trim()}
      >
        记录投递
      </button>
      {error && <span className="error-text">{error}</span>}
    </form>
  );
}
