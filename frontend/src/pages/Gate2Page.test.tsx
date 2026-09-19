import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportEvent, RewriteDetail, RewriteSentence } from "../api/types";
import Gate2Page from "./Gate2Page";

const mocks = vi.hoisted(() => ({
  getRewrite: vi.fn(),
  getSettings: vi.fn(),
  gateSentence: vi.fn(),
  createApplication: vi.fn(),
  exportStream: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getRewrite: mocks.getRewrite,
    getSettings: mocks.getSettings,
    gateSentence: mocks.gateSentence,
    createApplication: mocks.createApplication,
    getResume: vi.fn(),
    listDiagnoses: vi.fn(),
    getDiagnosis: vi.fn(),
    getFacts: vi.fn(),
    reviveRequirement: vi.fn(),
    listResumes: vi.fn(),
    listRewrites: vi.fn(),
    listApplications: vi.fn(),
  },
  diagnoseStream: vi.fn(),
  rewriteStream: vi.fn(),
  exportStream: mocks.exportStream,
  uploadResume: vi.fn(),
}));

function makeSentence(overrides: Partial<RewriteSentence>): RewriteSentence {
  return {
    id: "s1",
    section: "work",
    seq: 0,
    original_text: "负责推荐系统重构，DAU 从 70 万提升到 91 万",
    text: "负责推荐系统重构，DAU 从 70 万提升到 91 万",
    source_fact_ids: ["f1"],
    derived: [],
    verbs: [],
    requirement_ids: ["q1"],
    gate_status: "pending",
    validations: [
      {
        round: 0,
        layers: [
          { layer: "L0", verdict: "pass", detail: { message: "数字均有出处" } },
          { layer: "verb", verdict: "pass", detail: { message: "未命中主导级动词" } },
          { layer: "L1", verdict: "pass", detail: { message: "无衍生数字" } },
          { layer: "L2", verdict: "pass", detail: { message: "声明出处蕴含该句" } },
        ],
      },
    ],
    source_facts: [
      {
        id: "f1",
        section: "work",
        raw_quote: "负责推荐系统重构，DAU 从 70 万提升到 91 万",
        attribution: "individual",
        confirmed: true,
        span_start: 10,
        span_end: 30,
        locatable: true,
      },
    ],
    ...overrides,
  };
}

const passSentence = makeSentence({ id: "s1", seq: 0 });
const redSentence = makeSentence({
  id: "s2",
  seq: 1,
  section: "summary",
  text: "在长期实践中形成了沉稳细致的工作风格，获得同事与合作伙伴的普遍认可",
  original_text: "在长期实践中形成了沉稳细致的工作风格，获得同事与合作伙伴的普遍认可",
  requirement_ids: [],
  validations: [
    {
      round: 0,
      layers: [
        { layer: "L0", verdict: "pass", detail: { message: "无数字" } },
        { layer: "verb", verdict: "pass", detail: { message: "无主导级动词" } },
        { layer: "L1", verdict: "pass", detail: { message: "无衍生数字" } },
        {
          layer: "L2",
          verdict: "fail",
          detail: { message: "L2：句子的断言未映射到任何声明出处（映射不上即标红）", reason: "无支撑" },
        },
      ],
    },
  ],
});
const retriedSentence = makeSentence({
  id: "s3",
  seq: 2,
  text: "引入 Redis 缓存，缓存命中率 98%",
  original_text: "引入 Redis 缓存，缓存命中率 98%",
  validations: [
    {
      round: 0,
      layers: [
        { layer: "L0", verdict: "fail", detail: { message: "数字「3.7%」在事实源中无出处" } },
        { layer: "verb", verdict: "pass", detail: { message: "" } },
        { layer: "L1", verdict: "pass", detail: { message: "" } },
        { layer: "L2", verdict: "pass", detail: { message: "" } },
      ],
    },
    {
      round: 1,
      layers: [
        { layer: "L0", verdict: "pass", detail: { message: "数字均有出处" } },
        { layer: "verb", verdict: "pass", detail: { message: "" } },
        { layer: "L1", verdict: "pass", detail: { message: "" } },
        { layer: "L2", verdict: "pass", detail: { message: "" } },
      ],
    },
  ],
});

const baseRewrite: RewriteDetail = {
  id: "w1",
  diagnosis_id: "d1",
  resume_id: "r1",
  status: "escalated",
  rounds: 1,
  created_at: "2026-09-18T11:00:00",
  mock: true,
  target_requirement_ids: ["q1"],
  escalations: [
    {
      kind: "requirement_gap",
      message: "该岗位要求「会员体系运营经验」，经历库中没有对应经历",
      requirement_id: "q2",
      sentence_id: null,
      detail: {},
    },
    {
      kind: "l2_unentailed",
      message: "改写句「在长期实践中…」声明出处无法支撑",
      requirement_id: null,
      sentence_id: "s2",
      detail: {},
    },
  ],
  requirements: [
    {
      id: "q1",
      req_index: 0,
      priority: "must",
      text: "负责推荐系统的后端服务开发",
      keywords: ["推荐系统"],
      status: "direct",
      quote: "负责推荐系统重构",
      user_revived: false,
    },
  ],
  sentences: [passSentence, redSentence, retriedSentence],
};

/** 内存态模拟后端：gate 动作修改状态后，重取返回最新。 */
function installStatefulMocks() {
  const state = { rewrite: structuredClone(baseRewrite) as RewriteDetail };
  mocks.getRewrite.mockImplementation(async () => structuredClone(state.rewrite));
  mocks.gateSentence.mockImplementation(
    async (sentenceId: string, action: "confirm" | "reject" | "edit", text?: string) => {
      const sentence = state.rewrite.sentences.find((item) => item.id === sentenceId)!;
      const gateStatus =
        action === "confirm" ? "confirmed" : action === "reject" ? "rejected" : "edited";
      sentence.gate_status = gateStatus;
      if (action === "edit" && text !== undefined) sentence.text = text;
      return { sentence_id: sentenceId, gate_status: gateStatus, text: sentence.text, l2_rerun: false };
    },
  );
  return state;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Gate2Page rewriteId="w1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
  installStatefulMocks();
  mocks.getSettings.mockResolvedValue({
    llm_base_url: "",
    llm_api_key_set: true,
    llm_model: "deepseek-chat",
    llm_mode: "auto",
    mock: true,
    mineru_available: false,
    playwright_available: true,
  });
  mocks.createApplication.mockResolvedValue({
    application: {
      id: "a1",
      version_id: "v1",
      company: "某公司",
      position: "后端",
      channel: null,
      applied_at: "2026-09-18T12:00:00",
      outcomes: [],
    },
  });
});

describe("Gate2Page（gate2 逐句确认，spec §17.4）", () => {
  it("标红句置前、层徽标 tooltip、升级清单与 mock 标示", async () => {
    renderPage();

    expect(await screen.findByTestId("escalation-banner")).toHaveTextContent("升级清单（2）");
    expect(screen.getByText(/会员体系运营经验/)).toBeInTheDocument();
    expect(screen.getByText("mock 改写")).toBeInTheDocument();

    // 标红句（s2）排在最前
    const cards = document.querySelectorAll("[data-testid^='sentence-']");
    expect(cards[0]).toHaveAttribute("data-testid", "sentence-s2");

    // 层徽标：L2 fail 带 message tooltip；重写句显示"校验 2 轮"
    const l2Badge = screen.getByTestId("layer-s2-L2");
    expect(l2Badge).toHaveTextContent("✗");
    expect(l2Badge).toHaveAttribute("title", expect.stringContaining("映射不上"));
    expect(screen.getByTestId("sentence-s3")).toHaveTextContent("校验 2 轮");
  });

  it("确认/拒绝写入并即时更新已处置计数", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByTestId("handled-count")).toHaveTextContent("已处置 0/3");
    await user.click(screen.getByTestId("confirm-s1"));
    await waitFor(() => expect(mocks.gateSentence).toHaveBeenCalledWith("s1", "confirm", undefined));
    await waitFor(() => expect(screen.getByTestId("handled-count")).toHaveTextContent("已处置 1/3"));

    await user.click(screen.getByTestId("reject-s2"));
    await waitFor(() => expect(mocks.gateSentence).toHaveBeenCalledWith("s2", "reject", undefined));
    await waitFor(() => expect(screen.getByTestId("handled-count")).toHaveTextContent("已处置 2/3"));
  });

  it("内联编辑：保存时提交 edit 动作与新文本", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("edit-s1"));
    const area = screen.getByTestId("edit-area-s1");
    await user.clear(area);
    await user.type(area, "负责推荐系统的重构与日常迭代");
    await user.click(screen.getByTestId("save-s1"));

    await waitFor(() =>
      expect(mocks.gateSentence).toHaveBeenCalledWith("s1", "edit", "负责推荐系统的重构与日常迭代"),
    );
    expect(await screen.findByText("负责推荐系统的重构与日常迭代")).toBeInTheDocument();
  });

  it("出处点击跳校对页定位高亮（?span=）", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("source-s1-f1"));
    expect(window.location.hash).toBe("#/review/r1?span=10-30");
  });

  it("导出锁定 → 全部处置后解锁，导出完成展示下载与记录投递", async () => {
    const user = userEvent.setup();
    mocks.exportStream.mockImplementation(
      async (_body: unknown, onEvent: (event: ExportEvent) => void) => {
        onEvent({ type: "step", step: "rendering" });
        onEvent({
          type: "done",
          version_id: "v1",
          template: "ats-v1",
          file_url: "/api/exports/v1/file",
          sentence_count: 2,
        });
      },
    );
    renderPage();

    const exportButton = await screen.findByTestId("export-pdf");
    expect(exportButton).toBeDisabled();
    expect(screen.getByTestId("export-locked-hint")).toHaveTextContent("还有 3 句待处置");

    for (const id of ["s1", "s2", "s3"]) {
      await user.click(screen.getByTestId(`confirm-${id}`));
    }
    await waitFor(() => expect(screen.getByTestId("export-pdf")).toBeEnabled());

    await user.click(screen.getByTestId("export-pdf"));
    const result = await screen.findByTestId("export-result");
    expect(within(result).getByText("下载 PDF")).toHaveAttribute("href", "/api/exports/v1/file");

    await user.type(screen.getByLabelText("公司"), "某科技公司");
    await user.type(screen.getByLabelText("岗位"), "后端工程师");
    await user.click(screen.getByTestId("record-application"));
    await waitFor(() =>
      expect(mocks.createApplication).toHaveBeenCalledWith({
        version_id: "v1",
        company: "某科技公司",
        position: "后端工程师",
        channel: "",
      }),
    );
    expect(await screen.findByTestId("application-recorded")).toBeInTheDocument();
  });

  it("Playwright 缺失：导出按钮置灰并给出安装指引（R4）", async () => {
    mocks.getSettings.mockResolvedValue({
      llm_base_url: "",
      llm_api_key_set: true,
      llm_model: "deepseek-chat",
      llm_mode: "auto",
      mock: true,
      mineru_available: false,
      playwright_available: false,
    });
    renderPage();

    expect(await screen.findByTestId("playwright-missing")).toHaveTextContent(
      "playwright install chromium",
    );
    expect(screen.getByTestId("export-pdf")).toBeDisabled();
  });
});
