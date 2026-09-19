import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, uploadResume } from "../api/client";

/** 上传页：拖拽上传、解析进度、mock 徽标；已校对简历 → JD 诊断入口（spec §12.8）。 */
export default function UploadPage() {
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // 已 confirmed 简历 → 直接进入 JD 诊断（spec §12.8 首页入口）
  const confirmedResumes = useQuery({
    queryKey: ["resumes", "confirmed"],
    queryFn: () => api.listResumes("confirmed"),
  });

  const handleFile = async (file: File) => {
    if (!/\.(pdf|docx)$/i.test(file.name)) {
      setError("仅支持 PDF / DOCX 格式的简历文件");
      return;
    }
    setBusy(true);
    setError(null);
    setWarnings([]);
    setPercent(0);
    try {
      const result = await uploadResume(file, setPercent);
      setWarnings(result.warnings);
      window.location.hash = `#/review/${result.resume_id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page upload-page">
      <div className="card intro">
        <h1>把简历变成"可信的事实源"</h1>
        <p>
          解析出的每条事实都带原文锚点（可程序回验），异常与歧义由系统标记——
          你只需要核对被标记的部分。数据全部保存在本机 SQLite，唯一的出网请求是你自配的模型 API。
        </p>
      </div>

      <div
        className={`dropzone ${dragging ? "dragging" : ""} ${busy ? "busy" : ""}`}
        data-testid="dropzone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files?.[0];
          if (file && !busy) void handleFile(file);
        }}
        onClick={() => {
          if (!busy) inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx"
          hidden
          data-testid="file-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleFile(file);
            event.target.value = "";
          }}
        />
        {busy ? (
          <div className="progress-block">
            <div className="progress-label">
              上传 {percent}% · {percent >= 100 ? "解析与抽取中（最长约 1 分钟）…" : "上传中…"}
            </div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${percent}%` }} />
            </div>
          </div>
        ) : (
          <>
            <div className="dropzone-title">拖拽简历到此处，或点击选择文件</div>
            <div className="dropzone-sub">支持 PDF / DOCX；解析完成后进入事实源校对页</div>
          </>
        )}
      </div>

      {error && <div className="card error-card">{error}</div>}
      {(() => {
        const resumes = confirmedResumes.data?.resumes ?? [];
        if (resumes.length === 0) return null;
        return (
          <div className="card resume-picker">
            <h2>开始 JD 诊断</h2>
            <p>选择一份已完成校对的简历，粘贴目标岗位 JD，逐条在事实源中举证：</p>
            <ul className="resume-list">
              {resumes.map((item) => (
                <li key={item.id} data-testid={`resume-item-${item.id}`}>
                  <a className="resume-link" href={`#/diagnose/${item.id}`}>
                    {item.filename}
                  </a>
                  <span className="meta-chip">{item.created_at}</span>
                  {item.mock && <span className="meta-chip">mock 解析</span>}
                </li>
              ))}
            </ul>
          </div>
        );
      })()}
      {warnings.length > 0 && (
        <div className="card warn-card">
          <h3>解析提示</h3>
          <ul>
            {warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
