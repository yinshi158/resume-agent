import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Fact, ResumeDetail } from "../api/types";
import ReviewPage from "./ReviewPage";

// 高亮断言基准：span 为 UTF-16 码元偏移，前端直接 slice（spec §8）
const CANONICAL = "负责推荐系统重构，DAU 从 70 万提升到 91 万";

const mocks = vi.hoisted(() => ({
  getResume: vi.fn(),
  getFacts: vi.fn(),
  patchFact: vi.fn(),
  resolveFlag: vi.fn(),
  reparse: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getResume: mocks.getResume,
    getFacts: mocks.getFacts,
    patchFact: mocks.patchFact,
    resolveFlag: mocks.resolveFlag,
    reparse: mocks.reparse,
  },
  uploadResume: vi.fn(),
}));

function makeFact(overrides: Partial<Fact>): Fact {
  return {
    id: "f0",
    resume_id: "r1",
    section: "work",
    raw_quote: "",
    span_start: -1,
    span_end: -1,
    payload: { fields: {}, date_interpretations: [], entities: { numbers: [], orgs: [], skills: [] } },
    attribution: "unknown",
    attribution_confirmed: false,
    confirmed: false,
    edited_payload: null,
    created_at: "2026-09-16T00:00:00",
    anomaly_flags: [],
    ...overrides,
  };
}

const resume: ResumeDetail = {
  id: "r1",
  filename: "resume.pdf",
  parser: "pymupdf4llm",
  normalize_version: 1,
  status: "reviewing",
  created_at: "2026-09-16T00:00:00",
  canonical_text: CANONICAL,
  mock: true,
  allow_marked_only: true,
  mineru_available: false,
};

const facts: Fact[] = [
  makeFact({ id: "f1", raw_quote: "负责推荐系统重构", span_start: 0, span_end: 8 }),
  makeFact({
    id: "f2",
    raw_quote: "DAU 从 70 万提升到 91 万",
    span_start: 9,
    span_end: 26,
    anomaly_flags: [{ id: "a1", rule: "date_ambiguous", detail: { message: "日期格式歧义" }, resolved: false }],
  }),
];

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReviewPage resumeId="r1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getResume.mockResolvedValue(resume);
  mocks.getFacts.mockResolvedValue(facts);
  mocks.patchFact.mockResolvedValue(facts[0]);
});

describe("ReviewPage（gate1 校对页）", () => {
  it("渲染 canonical_text 与事实条目", async () => {
    renderPage();
    expect(await screen.findByTestId("canonical-text")).toBeInTheDocument();
    expect(screen.getByTestId("fact-row-f1")).toBeInTheDocument();
    expect(screen.getByTestId("fact-row-f2")).toBeInTheDocument();
  });

  it("点击条目按 span 直接 slice 高亮原文", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("canonical-text");

    await user.click(screen.getByTestId("fact-row-f1"));
    const selected = document.querySelector('mark.hl-selected[data-fact-id="f1"]');
    expect(selected).not.toBeNull();
    // 高亮文本必须与 canonical_text 的字符偏移切片逐字一致
    expect(selected!.textContent).toBe(CANONICAL.slice(0, 8));
    expect(selected!.textContent).toBe("负责推荐系统重构");
  });

  it("悬停条目时按 span 高亮对应原文", async () => {
    renderPage();
    await screen.findByTestId("canonical-text");

    fireEvent.mouseEnter(screen.getByTestId("fact-row-f2"));
    const hovered = document.querySelector('mark.hl-hovered[data-fact-id="f2"]');
    expect(hovered).not.toBeNull();
    expect(hovered!.textContent).toBe(CANONICAL.slice(9, 26));
  });

  it("只看标记模式仅显示有未处理异象的条目，左栏同步过滤", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("fact-row-f1");

    await user.click(screen.getByRole("tab", { name: /只看标记/ }));
    await waitFor(() => expect(screen.queryByTestId("fact-row-f1")).toBeNull());
    expect(screen.getByTestId("fact-row-f2")).toBeInTheDocument();
    expect(document.querySelector('mark[data-fact-id="f1"]')).toBeNull();
    expect(document.querySelector('mark[data-fact-id="f2"]')).not.toBeNull();
  });

  it("首次导入（allow_marked_only=false）时「只看标记」禁用", async () => {
    mocks.getResume.mockResolvedValue({ ...resume, allow_marked_only: false });
    renderPage();
    await screen.findByTestId("fact-row-f1");
    expect(screen.getByRole("tab", { name: /只看标记/ })).toBeDisabled();
  });

  it("归因选择即表态：PATCH attribution + attribution_confirmed", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = await screen.findByTestId("fact-row-f1");

    await user.click(within(row).getByRole("radio", { name: "个人" }));
    expect(mocks.patchFact).toHaveBeenCalledWith("f1", {
      attribution: "individual",
      attribution_confirmed: true,
    });
  });

  it("归因未表态时显示「待确认归因」，确认框禁用", async () => {
    renderPage();
    const row = await screen.findByTestId("fact-row-f1");
    expect(within(row).getByText("待确认归因")).toBeInTheDocument();
    expect(within(row).getByRole("checkbox")).toBeDisabled();
  });

  it("异象徽标展示 detail 一句话 tooltip", async () => {
    renderPage();
    const badge = await screen.findByTestId("anomaly-a1");
    expect(badge).toHaveAttribute("title", "日期格式歧义");
    expect(badge.textContent).toContain("日期歧义");
  });
});
