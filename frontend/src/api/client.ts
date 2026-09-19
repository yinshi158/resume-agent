/** 后端 API 客户端（本地单体，/api 由 Vite 代理到 FastAPI）。 */

import type {
  Application,
  DiagnoseEvent,
  DiagnosisDetail,
  DiagnosisListItem,
  DiagnosisRequirement,
  DiagnosisSummary,
  ExportEvent,
  Fact,
  FactPatch,
  GateStatus,
  ResumeDetail,
  ResumeListItem,
  RewriteDetail,
  RewriteEvent,
  RewriteListItem,
  SettingsPayload,
  UploadResult,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!resp.ok) {
    throw new Error(await extractError(resp));
  }
  return (await resp.json()) as T;
}

async function extractError(resp: Response): Promise<string> {
  try {
    const data = (await resp.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail) return JSON.stringify(data.detail);
  } catch {
    // 忽略解析失败，用默认信息
  }
  return `请求失败（HTTP ${resp.status}）`;
}

export const api = {
  getResume: (id: string) => request<ResumeDetail>(`/api/resumes/${id}`),

  async getFacts(resumeId: string): Promise<Fact[]> {
    const data = await request<{ resume_id: string; facts: Fact[] }>(`/api/resumes/${resumeId}/facts`);
    return data.facts;
  },

  patchFact: (factId: string, patch: FactPatch) =>
    request<Fact>(`/api/facts/${factId}`, { method: "PATCH", body: JSON.stringify(patch) }),

  resolveFlag: (flagId: string) =>
    request<{ id: string; resolved: boolean }>(`/api/anomaly-flags/${flagId}/resolve`, { method: "POST" }),

  reparse: (resumeId: string, parser = "mineru") =>
    request<{ resume_id: string; parser: string }>(`/api/resumes/${resumeId}/reparse?parser=${parser}`, {
      method: "POST",
    }),

  getSettings: () => request<SettingsPayload>("/api/settings"),

  putSettings: (patch: Partial<Pick<SettingsPayload, "llm_base_url" | "llm_model" | "llm_mode">> & { llm_api_key?: string }) =>
    request<SettingsPayload>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),

  // ---------- M2：JD 诊断 ----------

  listResumes: (status?: string) =>
    request<{ resumes: ResumeListItem[] }>(`/api/resumes${status ? `?status=${status}` : ""}`),

  async listDiagnoses(resumeId: string): Promise<DiagnosisListItem[]> {
    const data = await request<{ diagnoses: DiagnosisListItem[] }>(
      `/api/resumes/${resumeId}/diagnoses`,
    );
    return data.diagnoses;
  },

  getDiagnosis: (diagnosisId: string) => request<DiagnosisDetail>(`/api/diagnoses/${diagnosisId}`),

  reviveRequirement: (requirementId: string, factId: string) =>
    request<{ requirement: DiagnosisRequirement; summary: DiagnosisSummary }>(
      `/api/requirements/${requirementId}/revive`,
      { method: "POST", body: JSON.stringify({ fact_id: factId }) },
    ),

  // ---------- M3：改写 / gate2 / 导出 / ledger ----------

  async listRewrites(diagnosisId: string): Promise<RewriteListItem[]> {
    const data = await request<{ rewrites: RewriteListItem[] }>(
      `/api/diagnoses/${diagnosisId}/rewrites`,
    );
    return data.rewrites;
  },

  getRewrite: (rewriteId: string) => request<RewriteDetail>(`/api/rewrites/${rewriteId}`),

  gateSentence: (sentenceId: string, action: "confirm" | "reject" | "edit", text?: string) =>
    request<{ sentence_id: string; gate_status: GateStatus; text: string; l2_rerun: boolean }>(
      `/api/sentences/${sentenceId}/gate`,
      { method: "POST", body: JSON.stringify({ action, ...(text !== undefined ? { text } : {}) }) },
    ),

  listApplications: () =>
    request<{ applications: Application[] }>("/api/applications"),

  createApplication: (payload: {
    version_id: string;
    company: string;
    position: string;
    channel?: string;
  }) =>
    request<{ application: Application }>("/api/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

/**
 * POST + SSE 流式读取的公共实现（spec §12.7/§15.4/§18）。
 *
 * POST 无法使用 EventSource：fetch + ReadableStream 解析 `data: {JSON}` 行；
 * 前置校验失败（400/404/422）返回普通 JSON 错误，按 Error 抛出。
 */
async function postSse<T>(
  path: string,
  body: unknown,
  onEvent: (event: T) => void,
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(await extractError(resp));
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      onEvent(JSON.parse(dataLine.slice("data:".length).trim()) as T);
    }
  }
}

/** 新建诊断（SSE 流式，spec §12.7）。 */
export function diagnoseStream(
  body: { resume_id: string; jd_text: string },
  onEvent: (event: DiagnoseEvent) => void,
): Promise<void> {
  return postSse("/api/diagnoses", body, onEvent);
}

/** 开始改写（SSE 分步进度：writing/validating round n/escalated → done）。 */
export function rewriteStream(
  body: { diagnosis_id: string },
  onEvent: (event: RewriteEvent) => void,
): Promise<void> {
  return postSse("/api/rewrites", body, onEvent);
}

/** 导出 PDF（SSE：rendering → done/error；pending 句 422 同步返回）。 */
export function exportStream(
  body: { rewrite_id: string },
  onEvent: (event: ExportEvent) => void,
): Promise<void> {
  return postSse("/api/exports", body, onEvent);
}

/**
 * 上传简历（XHR 提供上传进度）。
 * M1 解析为同步任务：上传进度 0–100%，随后为"解析中"不确定态（M2 换 SSE）。
 */
export function uploadResume(file: File, onProgress: (percent: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/resumes");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResult);
        return;
      }
      let detail = `上传失败（HTTP ${xhr.status}）`;
      try {
        const data = JSON.parse(xhr.responseText) as { detail?: string };
        if (data.detail) detail = data.detail;
      } catch {
        // 保持默认信息
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("网络错误：无法连接后端（请确认后端已启动）"));
    xhr.send(form);
  });
}
