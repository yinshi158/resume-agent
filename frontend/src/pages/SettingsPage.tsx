import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";

/** 设置页：OpenAI 兼容端点 + key；mock 状态显示（spec §8）。 */
export default function SettingsPage() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<"auto" | "live" | "mock">("auto");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyTouched, setApiKeyTouched] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const data = settingsQuery.data;
    if (!data) return;
    setBaseUrl(data.llm_base_url);
    setModel(data.llm_model);
    setMode(data.llm_mode);
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () =>
      api.putSettings({
        llm_base_url: baseUrl,
        llm_model: model,
        llm_mode: mode,
        ...(apiKeyTouched ? { llm_api_key: apiKey } : {}),
      }),
    onSuccess: (data) => {
      setNotice("已保存");
      setApiKey("");
      setApiKeyTouched(false);
      queryClient.setQueryData(["settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) => setNotice(err instanceof Error ? err.message : String(err)),
  });

  const settings = settingsQuery.data;

  return (
    <div className="page settings-page">
      <div className="card">
        <h2>模型设置（OpenAI 兼容端点）</h2>
        <p className="hint">
          隐私：简历数据全部保存在本机 SQLite；唯一的出网请求是你自配的模型 API（默认 DeepSeek）。
          不填 key 时全链路以 mock 演示模式运行（规则抽取，质量不代表真实水平）。
        </p>

        <label className="field">
          <span>API 端点（Base URL）</span>
          <input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://api.deepseek.com/v1"
          />
        </label>

        <label className="field">
          <span>模型名</span>
          <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="deepseek-chat" />
        </label>

        <label className="field">
          <span>API Key</span>
          <input
            type="password"
            value={apiKey}
            placeholder={settings?.llm_api_key_set ? "已配置（留空则不修改）" : "未配置（mock 模式）"}
            onChange={(event) => {
              setApiKey(event.target.value);
              setApiKeyTouched(true);
            }}
          />
        </label>

        <label className="field">
          <span>运行模式</span>
          <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
            <option value="auto">auto（有 key 用真实模型，无 key 用 mock）</option>
            <option value="mock">mock（强制演示模式）</option>
            <option value="live">live（强制真实模型）</option>
          </select>
        </label>

        <div className="settings-actions">
          <button type="button" className="btn" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "保存中…" : "保存设置"}
          </button>
          {notice && <span className="notice-inline">{notice}</span>}
        </div>
      </div>

      <div className="card">
        <h2>环境状态</h2>
        <ul className="status-list">
          <li>
            当前模式：
            {settings?.mock ? (
              <span className="mock-badge mock-on">mock 演示模式（规则抽取）</span>
            ) : (
              <span className="mock-badge mock-live">真实模型</span>
            )}
          </li>
          <li>
            MinerU（双栏/复杂排版重解析）：
            {settings?.mineru_available ? (
              <span className="ok">已安装</span>
            ) : (
              <span className="muted">未安装（可选依赖：pip install "mineru[core]" 后可用）</span>
            )}
          </li>
          <li>
            数据库：<code>data/resume_agent.db</code>（本机 SQLite，可整库拷贝备份）
          </li>
        </ul>
      </div>
    </div>
  );
}
