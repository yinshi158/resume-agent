import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisDetail, Fact } from "../api/types";
import DiagnosisReportPage from "./DiagnosisReportPage";

const mocks = vi.hoisted(() => ({
  getDiagnosis: vi.fn(),
  getFacts: vi.fn(),
  reviveRequirement: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getDiagnosis: mocks.getDiagnosis,
    getFacts: mocks.getFacts,
    reviveRequirement: mocks.reviveRequirement,
    getResume: vi.fn(),
    listDiagnoses: vi.fn(),
    listResumes: vi.fn(),
  },
  diagnoseStream: vi.fn(),
  uploadResume: vi.fn(),
}));

const detail: DiagnosisDetail = {
  id: "d1",
  resume_id: "r1",
  jd_text: "任职要求：\n1. 精通 Python\n2. 有 5 年团队管理经验",
  created_at: "2026-09-17T10:00:00",
  mock: true,
  summary: {
    must_total: 2,
    must_direct: 1,
    must_nearest: 0,
    must_gap: 1,
    preferred_total: 1,
    preferred_direct: 1,
    worth_applying: true,
    notice: null,
  },
  requirements: [
    {
      id: "q1",
      req_index: 0,
      priority: "must",
      text: "精通 Python",
      keywords: ["Python"],
      status: "direct",
      quote: "负责推荐系统重构",
      quote_span: { start: 0, end: 8 },
      fact_id: "f1",
      nearest_note: null,
      user_revived: false,
    },
    {
      id: "q2",
      req_index: 1,
      priority: "must",
      text: "有 5 年团队管理经验",
      keywords: ["管理经验"],
      status: "gap",
      quote: null,
      quote_span: null,
      fact_id: null,
      nearest_note: "未找到关键词重叠的经历，可从下方选择相关条目捞回",
      user_revived: false,
      candidate_facts: [{ id: "f1", section: "work", raw_quote: "负责推荐系统重构" }],
    },
    {
      id: "q3",
      req_index: 2,
      priority: "preferred",
      text: "有推荐系统经验者优先",
      keywords: ["推荐系统"],
      status: "nearest",
      quote: null,
      quote_span: null,
      fact_id: "f2",
      nearest_note: "无直接证据，最接近的经历：负责推荐系统重构",
      user_revived: false,
    },
  ],
  gaps: ["q2"],
  warnings: [],
};

const facts: Fact[] = [
  {
    id: "f1",
    resume_id: "r1",
    section: "work",
    raw_quote: "负责推荐系统重构",
    span_start: 0,
    span_end: 8,
    payload: { fields: {}, date_interpretations: [], entities: { numbers: [], orgs: [], skills: [] } },
    attribution: "individual",
    attribution_confirmed: true,
    confirmed: true,
    edited_payload: null,
    created_at: "2026-09-17T00:00:00",
    anomaly_flags: [],
  },
];

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DiagnosisReportPage diagnosisId="d1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
  mocks.getDiagnosis.mockResolvedValue(detail);
  mocks.getFacts.mockResolvedValue(facts);
  mocks.reviveRequirement.mockResolvedValue({ requirement: detail.requirements[1], summary: detail.summary });
});

describe("DiagnosisReportPage（诊断报告页，spec §12.8）", () => {
  it("渲染 summary 指标与 must/preferred 分组、状态徽标", async () => {
    renderPage();

    expect(await screen.findByTestId("req-row-q1")).toBeInTheDocument();
    expect(screen.getByText("硬性要求（2）")).toBeInTheDocument();
    expect(screen.getByText("加分项（1）")).toBeInTheDocument();
    // direct 只承诺"有直接引用"（程序验证逐字定位，不承诺语义相关）
    expect(screen.getByTestId("req-row-q1")).toHaveTextContent("有直接引用");
    expect(screen.getByTestId("req-row-q2")).toHaveTextContent("缺口");
    expect(screen.getByTestId("req-row-q3")).toHaveTextContent("最接近");
    // 报告页头部诚实声明（相关性在 M3 核验）
    expect(screen.getByTestId("report-disclaimer")).toHaveTextContent("逐字定位");
    // mock 诊断须标示（ard/0007）
    expect(screen.getByText("mock 诊断")).toBeInTheDocument();
    // worth_applying=true 且存在缺口 → 提示 M3 分母修正
    expect(screen.queryByTestId("worth-warning")).toBeNull();
    expect(screen.getByText(/M3 改写会将其剔除/)).toBeInTheDocument();
  });

  it("全 must 缺口时显著提示 worth_applying=false", async () => {
    mocks.getDiagnosis.mockResolvedValue({
      ...detail,
      summary: { ...detail.summary, worth_applying: false, notice: "该岗位可能不值得投（ard/0004）" },
    });
    renderPage();

    const warning = await screen.findByTestId("worth-warning");
    expect(warning).toHaveTextContent("可能不值得投");
  });

  it("人工捞回的条目显示「人工确认」徽标（与 pipeline「有直接引用」区分）", async () => {
    mocks.getDiagnosis.mockResolvedValue({
      ...detail,
      requirements: [{ ...detail.requirements[0], user_revived: true }],
    });
    renderPage();

    const row = await screen.findByTestId("req-row-q1");
    expect(row).toHaveTextContent("人工确认");
    expect(row).not.toHaveTextContent("有直接引用");
  });

  it("quote 点击跳转校对页定位高亮（?span=）", async () => {
    const user = userEvent.setup();
    renderPage();

    const quote = await screen.findByText("“负责推荐系统重构”");
    await user.click(quote);
    expect(window.location.hash).toBe("#/review/r1?span=0-8");
  });

  it("nearest 行一键捞回（fact_id 已知）", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("revive-q3"));
    await waitFor(() =>
      expect(mocks.reviveRequirement).toHaveBeenCalledWith("q3", "f2"),
    );
  });

  it("纯缺口行从事实源选择经历后捞回", async () => {
    const user = userEvent.setup();
    renderPage();

    const select = await screen.findByLabelText("选择一条事实源经历");
    // 降级候选单独分组，且不声称"最接近"（P2 审查）
    expect(select.querySelector('optgroup[label="可考虑的候选"]')).not.toBeNull();
    // 等事实源列表加载完成出现可选项
    await screen.findByRole("option", { name: /负责推荐系统重构/ });
    await user.selectOptions(select, "f1");
    await user.click(screen.getByTestId("revive-pick-q2"));
    await waitFor(() =>
      expect(mocks.reviveRequirement).toHaveBeenCalledWith("q2", "f1"),
    );
  });
});
