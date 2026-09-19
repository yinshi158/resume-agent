import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { Fact, FactPatch } from "../api/types";
import CanonicalTextView from "../components/CanonicalTextView";
import EditDrawer from "../components/EditDrawer";
import FactTable from "../components/FactTable";

/**
 * gate1 校对页（spec §8，第一咽喉，ard/0002）。
 *
 * - 左栏：canonical_text + span 高亮联动（selectedFactId / hover）；
 * - 右栏：按 section 分组的事实表 + 异象徽标 + 归因四态 + 编辑抽屉；
 * - 顶栏：模式切换（首次全量 / 只看标记）、MinerU 重解析、完成校对、
 *   JD 诊断入口（全部确认后可用）；
 * - highlightSpan：诊断报告 quote 跳入时的定位高亮（spec §12.8）。
 */
export default function ReviewPage({
  resumeId,
  highlightSpan,
}: {
  resumeId: string;
  highlightSpan?: { start: number; end: number } | null;
}) {
  const queryClient = useQueryClient();
  const resumeQuery = useQuery({ queryKey: ["resume", resumeId], queryFn: () => api.getResume(resumeId) });
  const factsQuery = useQuery({ queryKey: ["facts", resumeId], queryFn: () => api.getFacts(resumeId) });

  const [selectedFactId, setSelectedFactId] = useState<string | null>(null);
  const [hoveredFactId, setHoveredFactId] = useState<string | null>(null);
  const [mode, setMode] = useState<"full" | "marked">("full");
  const [editingFact, setEditingFact] = useState<Fact | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const resume = resumeQuery.data;
  const facts = useMemo(() => factsQuery.data ?? [], [factsQuery.data]);
  const allowMarkedOnly = resume?.allow_marked_only ?? false;
  const effectiveMode = allowMarkedOnly ? mode : "full";

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["facts", resumeId] });
    void queryClient.invalidateQueries({ queryKey: ["resume", resumeId] });
  };

  const patchMutation = useMutation({
    mutationFn: ({ factId, patch }: { factId: string; patch: FactPatch }) => api.patchFact(factId, patch),
    onSuccess: () => {
      setNotice(null);
      invalidate();
    },
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  const resolveMutation = useMutation({
    mutationFn: (flagId: string) => api.resolveFlag(flagId),
    onSuccess: invalidate,
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  const reparseMutation = useMutation({
    mutationFn: () => api.reparse(resumeId),
    onSuccess: (data) => {
      window.location.hash = `#/review/${data.resume_id}`;
    },
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  const visibleFacts = useMemo(() => {
    if (effectiveMode === "marked") {
      return facts.filter((fact) => fact.anomaly_flags.some((flag) => !flag.resolved));
    }
    return facts;
  }, [facts, effectiveMode]);

  const visibleFactIds = useMemo(() => new Set(visibleFacts.map((fact) => fact.id)), [visibleFacts]);
  const pendingCount = facts.filter((fact) => !fact.confirmed).length;
  const markedCount = facts.filter((fact) => fact.anomaly_flags.some((flag) => !flag.resolved)).length;

  const handleFinish = () => {
    if (pendingCount > 0) {
      const firstPending = facts.find((fact) => !fact.confirmed);
      if (firstPending) {
        if (effectiveMode === "marked") setMode("full");
        setSelectedFactId(firstPending.id);
      }
      setNotice(`还有 ${pendingCount} 条未完成校对，已定位到第一条。`);
      return;
    }
    setNotice("全部条目已确认，点右上角「JD 诊断 →」粘贴目标岗位 JD，即可逐条举证。");
  };

  const handlePatch = (factId: string, patch: FactPatch) => patchMutation.mutate({ factId, patch });

  if (resumeQuery.isLoading || factsQuery.isLoading) {
    return <div className="page">加载中…</div>;
  }
  if (resumeQuery.error) {
    return <div className="page error-card">{(resumeQuery.error as Error).message}</div>;
  }
  if (!resume) return null;

  return (
    <div className="page review-page">
      <div className="review-toolbar">
        <div className="toolbar-left">
          <a className="btn-ghost btn-sm" href="#/upload">
            ← 返回
          </a>
          <span className="file-name" title={resume.id}>
            {resume.filename}
          </span>
          <span className={`status-chip status-${resume.status}`}>
            {resume.status === "confirmed" ? "已完成校对" : "校对中"}
          </span>
          <span className="meta-chip" title="解析器 / 归一化版本 / 条目数">
            {resume.parser} · normalize v{resume.normalize_version} · {facts.length} 条
          </span>
        </div>
        <div className="toolbar-right">
          <div className="mode-switch" role="tablist" aria-label="校对模式">
            <button
              type="button"
              role="tab"
              aria-selected={effectiveMode === "full"}
              className={effectiveMode === "full" ? "active" : ""}
              onClick={() => setMode("full")}
            >
              首次全量（{facts.length}）
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={effectiveMode === "marked"}
              className={effectiveMode === "marked" ? "active" : ""}
              disabled={!allowMarkedOnly}
              title={allowMarkedOnly ? "只看有未处理异象标记的条目" : "首次导入必须全量过一遍（ard/0002）"}
              onClick={() => setMode("marked")}
            >
              只看标记（{markedCount}）
            </button>
          </div>
          <button
            type="button"
            className="btn-ghost"
            disabled={reparseMutation.isPending || !resume.mineru_available}
            title={
              resume.mineru_available
                ? "阅读序混乱时切换 MinerU 重新解析（产生新版本，不覆盖当前）"
                : "未检测到 MinerU（可选依赖）：安装后可一键重解析，避免双栏乱序只能改 PDF 重传（spec §4）"
            }
            onClick={() => reparseMutation.mutate()}
          >
            {reparseMutation.isPending ? "重解析中…" : "MinerU 重解析"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={resume.status !== "confirmed"}
            title={
              resume.status === "confirmed"
                ? "进入 JD 诊断：粘贴目标岗位 JD，逐条举证"
                : "全部条目确认（完成校对）后才可进行 JD 诊断（ard/0002）"
            }
            onClick={() => {
              window.location.hash = `#/diagnose/${resumeId}`;
            }}
          >
            JD 诊断 →
          </button>
          <button type="button" className="btn" onClick={handleFinish}>
            完成校对{pendingCount > 0 ? `（剩 ${pendingCount}）` : ""}
          </button>
        </div>
      </div>

      {notice && <div className="notice-bar">{notice}</div>}

      <div className="review-body">
        <section className="canonical-pane">
          <h2>规范原文层（canonical_text）</h2>
          <p className="pane-hint">
            入库即不可变；所有字段锚定在这里的字符偏移上。点击左侧或右栏条目可联动高亮。
          </p>
          <CanonicalTextView
            text={resume.canonical_text}
            facts={facts}
            visibleFactIds={visibleFactIds}
            selectedFactId={selectedFactId}
            hoveredFactId={hoveredFactId}
            onSelectFact={setSelectedFactId}
            extraSpan={highlightSpan ?? null}
          />
        </section>

        <section className="facts-pane">
          <h2>事实源（{visibleFacts.length} 条）</h2>
          <FactTable
            facts={visibleFacts}
            selectedFactId={selectedFactId}
            onSelect={setSelectedFactId}
            onHover={setHoveredFactId}
            onPatch={handlePatch}
            onResolveFlag={(flagId) => resolveMutation.mutate(flagId)}
            onEdit={setEditingFact}
          />
        </section>
      </div>

      {editingFact && (
        <EditDrawer
          fact={editingFact}
          saving={patchMutation.isPending}
          onClose={() => setEditingFact(null)}
          onSave={(factId, payload) => {
            patchMutation.mutate(
              { factId, patch: { edited_payload: payload } },
              { onSuccess: () => setEditingFact(null) },
            );
          }}
        />
      )}
    </div>
  );
}
