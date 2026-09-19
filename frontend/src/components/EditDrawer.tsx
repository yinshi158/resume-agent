import { useState } from "react";

import type { Fact } from "../api/types";

interface Props {
  fact: Fact;
  saving: boolean;
  onSave: (factId: string, payload: Record<string, unknown>) => void;
  onClose: () => void;
}

/**
 * 编辑抽屉：修改结构化字段（edited_payload）。
 * 原文层 canonical_text 不可修改（ard/0001）。
 */
export default function EditDrawer({ fact, saving, onSave, onClose }: Props) {
  const [text, setText] = useState(() =>
    JSON.stringify(fact.edited_payload ?? fact.payload, null, 2),
  );
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("payload 必须是 JSON 对象");
      }
      setError(null);
      onSave(fact.id, parsed as Record<string, unknown>);
    } catch (err) {
      setError(err instanceof Error ? err.message : "JSON 解析失败");
    }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" data-testid="edit-drawer" onClick={(event) => event.stopPropagation()}>
        <h3>编辑结构化字段</h3>
        <p className="hint">
          原文层（左栏）永不修改；这里修正的是结构化字段，供后续诊断/改写使用。
        </p>
        <div className="drawer-quote">“{fact.raw_quote}”</div>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={16}
          spellCheck={false}
        />
        {error && <div className="error-text">{error}</div>}
        <div className="drawer-actions">
          <button type="button" className="btn" onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </aside>
    </div>
  );
}
