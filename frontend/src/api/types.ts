/** 与后端 API 契约对应的类型（docs/spec.md §5/§7）。 */

export type Section = "summary" | "work" | "project" | "education" | "skill" | "other";
export type Attribution = "individual" | "team" | "mixed" | "unknown";
export type ResumeStatus = "parsed" | "reviewing" | "confirmed";

export interface DateInterpretation {
  raw: string;
  normalized: string | null;
  ambiguous: boolean;
  candidates: string[];
}

export interface NumberEntity {
  value: number;
  unit: string;
}

export interface Entities {
  numbers: NumberEntity[];
  orgs: string[];
  skills: string[];
}

export interface FactPayload {
  fields: Record<string, unknown>;
  date_interpretations: DateInterpretation[];
  entities: Entities;
}

export interface AnomalyFlag {
  id: string;
  rule: string;
  /** detail.message 为人类可读一句话（gate1 直接展示，spec §6）。 */
  detail: { message?: string } & Record<string, unknown>;
  resolved: boolean;
}

/**
 * 事实源条目。
 * span_start / span_end 为 UTF-16 码元偏移（前端 JS 语义），
 * 可直接对 canonical_text 做 slice 高亮（spec §8，无坐标换算）；
 * -1 = quote 回验失败未定位。
 */
export interface Fact {
  id: string;
  resume_id: string;
  section: Section;
  raw_quote: string;
  span_start: number;
  span_end: number;
  payload: FactPayload;
  attribution: Attribution;
  attribution_confirmed: boolean;
  confirmed: boolean;
  edited_payload: Record<string, unknown> | null;
  created_at: string;
  anomaly_flags: AnomalyFlag[];
}

export interface ResumeDetail {
  id: string;
  filename: string;
  /** 'pymupdf4llm' | 'markitdown' | 'mineru' | 'mock'（mock = 该次解析的抽取来源为 mock）。 */
  parser: string;
  normalize_version: number;
  status: ResumeStatus;
  created_at: string;
  canonical_text: string;
  /** 该版本解析时的来源是否 mock——按行记录，不随当前全局设置变化。 */
  mock: boolean;
  /** 首次导入时"只看标记"模式禁用（ard/0002）。 */
  allow_marked_only: boolean;
  mineru_available: boolean;
}

export interface UploadResult {
  resume_id: string;
  status: string;
  mock: boolean;
  warnings: string[];
}

export interface SettingsPayload {
  llm_base_url: string;
  llm_api_key_set: boolean;
  llm_model: string;
  llm_mode: "auto" | "live" | "mock";
  mock: boolean;
  mineru_available: boolean;
  /** R4：导出唯一渲染路径（缺失时导出按钮置灰并给安装指引） */
  playwright_available: boolean;
}

export interface FactPatch {
  edited_payload?: Record<string, unknown>;
  attribution?: Attribution;
  attribution_confirmed?: boolean;
  confirmed?: boolean;
}

export const SECTION_LABELS: Record<Section, string> = {
  summary: "个人简介",
  work: "工作经历",
  project: "项目经历",
  education: "教育背景",
  skill: "技能",
  other: "其他",
};

export const RULE_LABELS: Record<string, string> = {
  date_order: "日期颠倒",
  date_overlap: "日期重叠",
  date_ambiguous: "日期歧义",
  percent_bound: "百分比超界",
  amount_magnitude: "金额量级异常",
  missing_field: "字段缺失",
};

// ---------------------------------------------------------------------------
// M2：JD 诊断（spec §12.5/§12.7）
// ---------------------------------------------------------------------------

export type ReqPriority = "must" | "preferred";
export type ReqStatus = "direct" | "nearest" | "gap";

export interface QuoteSpan {
  start: number;
  end: number;
}

/** gap 的降级候选（读取时计算，不落库）。 */
export interface CandidateFact {
  id: string;
  section: Section;
  raw_quote: string;
}

export interface DiagnosisRequirement {
  id: string;
  req_index: number;
  priority: ReqPriority;
  text: string;
  /** 关键词（含中英文别名），最近邻检索依据 */
  keywords: string[];
  status: ReqStatus;
  /** 逐字证据（direct 时非空；程序验证唯一定位） */
  quote: string | null;
  /** 证据在 canonical_text 的 UTF-16 偏移（可直接 slice 高亮）；不可定位时 null */
  quote_span: QuoteSpan | null;
  fact_id: string | null;
  /** nearest/gap 时"最接近的是 X"的人类可读说明 */
  nearest_note: string | null;
  /** 人工捞回标记（ard/0004 低成本确认点） */
  user_revived: boolean;
  /**
   * gap 时的降级候选（读取时确定性计算，不落库）；
   * 只作"可考虑的候选"呈现，**不声称"最接近"**（语义相关性是 M3 的 L2 职责）。
   */
  candidate_facts?: CandidateFact[];
}

export interface DiagnosisSummary {
  must_total: number;
  must_direct: number;
  must_nearest: number;
  must_gap: number;
  preferred_total: number;
  preferred_direct: number;
  /** 全部 must 缺口 → false + notice（ard/0004 决策价值） */
  worth_applying: boolean;
  notice: string | null;
}

export interface DiagnosisDetail {
  id: string;
  resume_id: string;
  jd_text: string;
  created_at: string;
  /** 该次诊断的生成来源是否 mock——按行记录，不随当前全局设置变化 */
  mock: boolean;
  summary: DiagnosisSummary;
  /** 呈现顺序：must 前、缺口前（后端已排序） */
  requirements: DiagnosisRequirement[];
  /** 硬性缺口条目 id 清单（M3 改写分母修正依据） */
  gaps: string[];
  warnings: string[];
}

export interface DiagnosisListItem {
  id: string;
  resume_id: string;
  created_at: string;
  mock: boolean;
  summary: DiagnosisSummary | null;
}

export interface ResumeListItem {
  id: string;
  filename: string;
  parser: string;
  status: ResumeStatus;
  created_at: string;
  mock: boolean;
}

/** SSE 事件（`data: {JSON}` 的 type 判别联合，spec §9）。 */
export type DiagnoseEvent =
  | { type: "step"; step: "parsing" | "reporting"; message?: string }
  | { type: "step"; step: "evidencing"; index: number; total: number; message?: string }
  | { type: "done"; diagnosis_id: string; mock: boolean; warnings: string[] }
  | { type: "error"; message: string };

// 注意：direct 只承诺"有直接引用"——程序验证的是逐字定位，不是语义相关
// （相关性在 M3 改写校验阶段核验，避免过度断言）
export const REQ_STATUS_LABELS: Record<ReqStatus, string> = {
  direct: "有直接引用",
  nearest: "最接近",
  gap: "缺口",
};

export const REQ_PRIORITY_LABELS: Record<ReqPriority, string> = {
  must: "硬性要求",
  preferred: "加分项",
};

// ---------------------------------------------------------------------------
// M3：改写 / 校验 / gate2 / 导出 / ledger（spec §15–§21）
// ---------------------------------------------------------------------------

export type GateStatus = "pending" | "confirmed" | "rejected" | "edited";
export type ValidationLayer = "L0" | "verb" | "L1" | "L2";
export type LayerVerdict = "pass" | "fail";

export interface LayerValidation {
  layer: ValidationLayer;
  verdict: LayerVerdict;
  detail: {
    message?: string;
    /** edit 后沿用上一轮 L2 判定（R2 未触发重跑） */
    reused?: boolean;
    /** 调用/解析故障（不是"真实缺口"，升级清单不收录） */
    error?: boolean;
    [key: string]: unknown;
  };
}

export interface ValidationRound {
  round: number;
  layers: LayerValidation[];
}

export interface RewriteSourceFact {
  id: string;
  section?: Section;
  raw_quote?: string;
  attribution?: Attribution;
  confirmed?: boolean;
  /** UTF-16 偏移（可直接用于 #/review/{resumeId}?span=s-e 定位） */
  span_start?: number;
  span_end?: number;
  locatable?: boolean;
  missing?: boolean;
}

export interface DerivedNumberPayload {
  value: string;
  formula: string;
  source_ids: string[];
}

export interface RewriteSentence {
  id: string;
  section: Section;
  seq: number;
  original_text: string;
  text: string;
  source_fact_ids: string[];
  derived: DerivedNumberPayload[];
  verbs: string[];
  requirement_ids: string[];
  gate_status: GateStatus;
  /** 判定按轮返回：末轮为当前生效判定（gate_events 快照同源） */
  validations: ValidationRound[];
  source_facts: RewriteSourceFact[];
}

export interface RewriteEscalation {
  kind: "requirement_gap" | "l2_unentailed" | string;
  message: string;
  requirement_id: string | null;
  sentence_id: string | null;
  detail: Record<string, unknown>;
}

export interface RewriteRequirementBrief {
  id: string;
  req_index: number;
  priority: ReqPriority;
  text: string;
  keywords: string[];
  status: ReqStatus;
  quote: string | null;
  user_revived: boolean;
}

export interface RewriteDetail {
  id: string;
  diagnosis_id: string;
  resume_id: string;
  status: string;
  rounds: number;
  created_at: string;
  /** 该次改写是否使用过 mock（按行记录，含 L2 校验调用） */
  mock: boolean;
  target_requirement_ids: string[];
  escalations: RewriteEscalation[];
  requirements: RewriteRequirementBrief[];
  sentences: RewriteSentence[];
}

export interface RewriteListItem {
  id: string;
  diagnosis_id: string;
  status: string;
  rounds: number;
  created_at: string;
  mock: boolean;
  escalations: number;
  sentence_count: number;
}

/** 改写 SSE 事件（`data: {JSON}` 的 type 判别联合，spec §9/§15.4）。 */
export type RewriteEvent =
  | { type: "step"; step: "writing"; round: number; message?: string }
  | { type: "step"; step: "validating"; round: number; message?: string }
  | { type: "step"; step: "escalated"; items: RewriteEscalation[]; message?: string }
  | {
      type: "done";
      rewrite_id: string;
      mock: boolean;
      rounds: number;
      warnings: string[];
      escalations: number;
    }
  | { type: "error"; message: string };

/** 导出 SSE 事件（spec §18）。 */
export type ExportEvent =
  | { type: "step"; step: "rendering"; message?: string }
  | {
      type: "done";
      version_id: string;
      template: string;
      file_url: string;
      sentence_count: number;
    }
  | { type: "error"; message: string };

export type OutcomeStage = "viewed" | "written_test" | "interview" | "offer" | "rejected";

export interface Outcome {
  id: string;
  application_id: string;
  stage: OutcomeStage;
  note: string | null;
  created_at: string;
}

export interface Application {
  id: string;
  version_id: string;
  company: string;
  position: string;
  channel: string | null;
  applied_at: string;
  outcomes: Outcome[];
}

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  pending: "待处置",
  confirmed: "已确认",
  rejected: "已拒绝",
  edited: "已编辑",
};

export const LAYER_LABELS: Record<ValidationLayer, string> = {
  L0: "数字/实体",
  verb: "归因动词",
  L1: "衍生数字",
  L2: "语义蕴含",
};

export const REWRITE_STATUS_LABELS: Record<string, string> = {
  writing: "改写中",
  validating: "校验中",
  done: "已完成",
  escalated: "有升级项",
  exported: "已导出",
};
