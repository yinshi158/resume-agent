import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisDetail, RewriteEvent, RewriteListItem } from "../api/types";
import RewritePage from "./RewritePage";

const mocks = vi.hoisted(() => ({
  getDiagnosis: vi.fn(),
  listRewrites: vi.fn(),
  rewriteStream: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getDiagnosis: mocks.getDiagnosis,
    listRewrites: mocks.listRewrites,
    getRewrite: vi.fn(),
    gateSentence: vi.fn(),
    getResume: vi.fn(),
    listDiagnoses: vi.fn(),
    getFacts: vi.fn(),
    reviveRequirement: vi.fn(),
    listResumes: vi.fn(),
    getSettings: vi.fn(),
    createApplication: vi.fn(),
  },
  diagnoseStream: vi.fn(),
  rewriteStream: mocks.rewriteStream,
  exportStream: vi.fn(),
  uploadResume: vi.fn(),
}));

const diagnosis: DiagnosisDetail = {
  id: "d1",
  resume_id: "r1",
  jd_text: "任职要求：精通 Python",
  created_at: "2026-09-18T10:00:00",
  mock: true,
  summary: {
    must_total: 3,
    must_direct: 2,
    must_nearest: 1,
    must_gap: 1,
    preferred_total: 0,
    preferred_direct: 0,
    worth_applying: true,
    notice: null,
  },
  requirements: [],
  gaps: [],
  warnings: [],
};

const historyItem: RewriteListItem = {
  id: "w0",
  diagnosis_id: "d1",
  status: "escalated",
  rounds: 1,
  created_at: "2026-09-18T10:05:00",
  mock: true,
  escalations: 2,
  sentence_count: 9,
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RewritePage diagnosisId="d1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
  mocks.getDiagnosis.mockResolvedValue(diagnosis);
  mocks.listRewrites.mockResolvedValue([]);
});

describe("RewritePage（改写页，spec §21）", () => {
  it("渲染目标清单摘要与历史改写", async () => {
    mocks.listRewrites.mockResolvedValue([historyItem]);
    renderPage();

    expect(await screen.findByTestId("start-rewrite")).toBeInTheDocument();
    expect(screen.getByText(/缺口 1（剔除出改写）/)).toBeInTheDocument();
    expect(screen.getByTestId("rewrite-item-w0")).toHaveTextContent("有升级项");
    expect(screen.getByTestId("rewrite-item-w0")).toHaveTextContent("句 9 · 重写 1 轮 · 升级 2");
  });

  it("SSE 分步进度渲染，done 后跳转 gate2", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    mocks.rewriteStream.mockImplementation(
      async (_body: unknown, onEvent: (event: RewriteEvent) => void) => {
        onEvent({ type: "step", step: "writing", round: 0, message: "改写第 1 轮" });
        onEvent({ type: "step", step: "validating", round: 0 });
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        onEvent({
          type: "done",
          rewrite_id: "w123",
          mock: true,
          rounds: 1,
          warnings: [],
          escalations: 2,
        });
      },
    );
    renderPage();

    await user.click(await screen.findByTestId("start-rewrite"));
    const progress = await screen.findByTestId("rewrite-progress");
    expect(progress).toHaveTextContent("校验第 1 轮");

    release();
    await waitFor(() => expect(window.location.hash).toBe("#/gate2/w123"));
  });

  it("error 事件展示在错误卡中且不跳转", async () => {
    const user = userEvent.setup();
    mocks.rewriteStream.mockImplementation(
      async (_body: unknown, onEvent: (event: RewriteEvent) => void) => {
        onEvent({ type: "error", message: "LLM 调用失败：连接超时" });
      },
    );
    renderPage();

    await user.click(await screen.findByTestId("start-rewrite"));
    expect(await screen.findByTestId("rewrite-error")).toHaveTextContent("LLM 调用失败");
    expect(window.location.hash).not.toContain("gate2");
  });
});
