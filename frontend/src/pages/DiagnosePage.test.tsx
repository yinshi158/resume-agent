import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnoseEvent, DiagnosisListItem, ResumeDetail } from "../api/types";
import DiagnosePage from "./DiagnosePage";

const mocks = vi.hoisted(() => ({
  getResume: vi.fn(),
  listDiagnoses: vi.fn(),
  diagnoseStream: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getResume: mocks.getResume,
    listDiagnoses: mocks.listDiagnoses,
    getDiagnosis: vi.fn(),
    getFacts: vi.fn(),
    reviveRequirement: vi.fn(),
    listResumes: vi.fn(),
  },
  diagnoseStream: mocks.diagnoseStream,
  uploadResume: vi.fn(),
}));

const resume: ResumeDetail = {
  id: "r1",
  filename: "resume.pdf",
  parser: "mock",
  normalize_version: 1,
  status: "confirmed",
  created_at: "2026-09-17T00:00:00",
  canonical_text: "负责推荐系统重构",
  mock: true,
  allow_marked_only: true,
  mineru_available: false,
};

const gapHistory: DiagnosisListItem = {
  id: "d0",
  resume_id: "r1",
  created_at: "2026-09-17T09:00:00",
  mock: true,
  summary: {
    must_total: 2,
    must_direct: 0,
    must_nearest: 0,
    must_gap: 2,
    preferred_total: 0,
    preferred_direct: 0,
    worth_applying: false,
    notice: "该岗位 2 条硬性要求在事实源中均无对应经历",
  },
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DiagnosePage resumeId="r1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
  mocks.getResume.mockResolvedValue(resume);
  mocks.listDiagnoses.mockResolvedValue([]);
});

describe("DiagnosePage（JD 诊断页，spec §12.8）", () => {
  it("渲染简历信息与 JD 输入框，历史诊断展示 summary", async () => {
    mocks.listDiagnoses.mockResolvedValue([gapHistory]);
    renderPage();

    expect(await screen.findByTestId("jd-input")).toBeInTheDocument();
    expect(screen.getByText("resume.pdf")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-item-d0")).toHaveTextContent("硬性要求 2");
    expect(screen.getByTestId("diagnosis-item-d0")).toHaveTextContent("全 must 缺口");
  });

  it("未 confirmed 简历：输入框与开始按钮禁用并给出提示", async () => {
    mocks.getResume.mockResolvedValue({ ...resume, status: "reviewing" });
    renderPage();

    expect(await screen.findByTestId("jd-input")).toBeDisabled();
    expect(screen.getByTestId("start-diagnose")).toBeDisabled();
    expect(screen.getByText(/尚未完成事实源校对/)).toBeInTheDocument();
  });

  it("SSE 分步进度渲染，done 后跳转报告页", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    mocks.diagnoseStream.mockImplementation(
      async (_body: unknown, onEvent: (event: DiagnoseEvent) => void) => {
        onEvent({ type: "step", step: "parsing" });
        onEvent({ type: "step", step: "evidencing", index: 0, total: 3 });
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        onEvent({ type: "done", diagnosis_id: "d123", mock: true, warnings: [] });
      },
    );
    renderPage();

    await user.type(await screen.findByTestId("jd-input"), "任职要求：精通 Python");
    await user.click(screen.getByTestId("start-diagnose"));

    const progress = await screen.findByTestId("diagnose-progress");
    expect(progress).toHaveTextContent("逐条举证 1/3");
    expect(progress).toHaveTextContent("（1/3）");

    release();
    await waitFor(() => expect(window.location.hash).toBe("#/diagnosis/d123"));
  });

  it("error 事件展示在错误卡中", async () => {
    const user = userEvent.setup();
    mocks.diagnoseStream.mockImplementation(
      async (_body: unknown, onEvent: (event: DiagnoseEvent) => void) => {
        onEvent({ type: "error", message: "LLM 调用失败：连接超时" });
      },
    );
    renderPage();

    await user.type(await screen.findByTestId("jd-input"), "任职要求：精通 Python");
    await user.click(screen.getByTestId("start-diagnose"));

    expect(await screen.findByTestId("diagnose-error")).toHaveTextContent("LLM 调用失败");
    // 未完成时不跳转
    expect(window.location.hash).not.toContain("diagnosis");
  });
});
