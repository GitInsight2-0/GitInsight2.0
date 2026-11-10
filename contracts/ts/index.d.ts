/* 자동 생성 파일입니다. 직접 고치지 말고 contracts/src를 고친 뒤 `pnpm contracts:gen`을 실행하세요. */

/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Action".
 */
export type Action =
  "ASK" | "FOLLOW_UP" | "PROBE" | "HINT" | "NEXT_TOPIC" | "REPEAT" | "CLARIFY" | "WRAP_UP";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisStatus".
 */
export type AnalysisStatus = "queued" | "stage1" | "stage2" | "done" | "failed";
/**
 * git 이력으로 본 기여 정도입니다. 질문 순서와 표현에만 쓰고 점수에는 쓰지 않습니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "ContributionLabel".
 */
export type ContributionLabel = "verified" | "shared" | "unverified";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "SymbolKind".
 */
export type SymbolKind = "function" | "method" | "class";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Competency".
 */
export type Competency =
  | "code_understanding"
  | "design_judgment"
  | "problem_solving"
  | "scalability_operations"
  | "communication"
  | "role_decision";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "CoverageStatus".
 */
export type CoverageStatus = "covered" | "missing" | "wrong";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "FallbackReason".
 */
export type FallbackReason = "timeout" | "invalid" | "refusal" | "error" | "forced";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Intent".
 */
export type Intent = "ANSWER" | "DONT_KNOW" | "CLARIFY" | "REPEAT" | "OFF_TOPIC";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "InterviewMode".
 */
export type InterviewMode = "voice" | "text";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "MetricName".
 */
export type MetricName =
  | "vad_start"
  | "vad_end"
  | "first_audio"
  | "barge_in"
  | "delegation_start"
  | "delegation_end"
  | "filler_start"
  | "error";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "ReportKind".
 */
export type ReportKind = "full" | "turn_table";
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TurnSource".
 */
export type TurnSource = "voice" | "text";

export interface GitInsightContracts {}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisCreate".
 */
export interface AnalysisCreate {
  repo_id: number;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisCreated".
 */
export interface AnalysisCreated {
  analysis_id: string;
  cached: boolean;
  status: AnalysisStatus;
}
/**
 * 면접에 필요한 분석 결과 전체입니다. 원본 코드는 들어가지 않습니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisResult".
 */
export interface AnalysisResult {
  analysis_id: string;
  commit_sha: string;
  languages: LanguageStat[];
  repo_full_name: string;
  symbol_count: number;
  /**
   * @minItems 1
   */
  topics: Topic[];
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "LanguageStat".
 */
export interface LanguageStat {
  file_count: number;
  language: string;
}
/**
 * 면접 주제 하나입니다. 2단계 분석에서 만들어집니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Topic".
 */
export interface Topic {
  base_question: string;
  commits: string[];
  contribution_label: ContributionLabel;
  difficulty: number;
  /**
   * @minItems 3
   * @maxItems 5
   */
  expected_points: ExpectedPoint[];
  hint: string;
  /**
   * @minItems 1
   * @maxItems 3
   */
  prepared_followups: PreparedFollowup[];
  question_intent: string;
  related_symbols: SymbolRef[];
  summary: string;
  symbol: SymbolRef;
  title: string;
  topic_id: string;
}
/**
 * 좋은 답변이라면 다뤄야 할 요점입니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "ExpectedPoint".
 */
export interface ExpectedPoint {
  point_id: string;
  probe_question: string;
  text: string;
}
/**
 * LLM 없이도 쓸 수 있게 미리 만든 꼬리질문입니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "PreparedFollowup".
 */
export interface PreparedFollowup {
  question: string;
  symbol_id: string | null;
}
/**
 * 분석 결과에서 얻은 코드 위치입니다. LLM이 만들지 않습니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "SymbolRef".
 */
export interface SymbolRef {
  commit_sha: string;
  end_line: number;
  kind: SymbolKind;
  name: string;
  path: string;
  start_line: number;
  symbol_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisState".
 */
export interface AnalysisState {
  commit_sha: string | null;
  error: string | null;
  progress: number;
  status: AnalysisStatus;
}
/**
 * GET /api/analyses/{id}/summary 응답입니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "AnalysisSummary".
 */
export interface AnalysisSummary {
  languages: LanguageStat[];
  symbol_count: number;
  topics: TopicPreview[];
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TopicPreview".
 */
export interface TopicPreview {
  contribution_label: ContributionLabel;
  symbol: SymbolRef;
  title: string;
  topic_id: string;
}
/**
 * 면접 두뇌가 정한 다음 발화입니다. 음성 모델은 utterance의 뜻을 바꾸지 않고 말합니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "BrainReply".
 */
export interface BrainReply {
  action: Action;
  depth: number;
  done: boolean;
  highlight: SymbolRef | null;
  remaining_sec: number;
  topic_id: string | null;
  turn_id: string | null;
  utterance: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "CodeLink".
 */
export interface CodeLink {
  symbol: SymbolRef;
  url: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "CompetencyDraft".
 */
export interface CompetencyDraft {
  evidence: EvidenceDraft[];
  level: number;
  name: Competency;
  rationale: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "EvidenceDraft".
 */
export interface EvidenceDraft {
  quote: string;
  symbol_id: string | null;
  turn_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "CompetencyLevel".
 */
export interface CompetencyLevel {
  level: number;
  name: Competency;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "CompetencyScore".
 */
export interface CompetencyScore {
  /**
   * @minItems 1
   */
  evidence: Evidence[];
  level: number;
  name: Competency;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Evidence".
 */
export interface Evidence {
  quote: string;
  symbol: SymbolRef | null;
  turn_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Followup".
 */
export interface Followup {
  question: string;
  symbol_id: string | null;
}
/**
 * JD가 있을 때 주제 순서를 바꾸는 LLM 출력입니다. 허용된 topic_id만 씁니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "InterviewPlanOutput".
 */
export interface InterviewPlanOutput {
  persona_keywords: string[];
  topic_order: string[];
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Judgment".
 */
export interface Judgment {
  coverage: PointCoverage[];
  followup: Followup | null;
  intent: Intent;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "PointCoverage".
 */
export interface PointCoverage {
  evidence_quote: string | null;
  point_id: string;
  status: CoverageStatus;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Me".
 */
export interface Me {
  avatar_url: string;
  login: string;
  user_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "MetricEvent".
 */
export interface MetricEvent {
  at_ms: number;
  error_code?: string | null;
  name: MetricName;
  provider?: string | null;
  scenario?: string | null;
  session_id?: string | null;
  turn_id?: string | null;
  value_ms?: number | null;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "ProgressEntry".
 */
export interface ProgressEntry {
  competencies: CompetencyLevel[];
  date: string;
  session_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Repo".
 */
export interface Repo {
  default_branch: string;
  full_name: string;
  private: boolean;
  repo_id: number;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "Report".
 */
export interface Report {
  competencies: CompetencyScore[];
  improvements: string[];
  kind: ReportKind;
  next_topics: string[];
  report_id: string;
  session_id: string;
  strengths: string[];
  topics: TopicReport[];
  turns: TurnRow[];
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TopicReport".
 */
export interface TopicReport {
  code_links: CodeLink[];
  covered: string[];
  missing: string[];
  model_answer: string | null;
  question: string;
  topic_id: string;
}
/**
 * 턴별 판단 표의 한 줄입니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TurnRow".
 */
export interface TurnRow {
  action: Action;
  covered_points: string[];
  fallback_reason: FallbackReason | null;
  intent: Intent | null;
  question: string;
  topic_id: string | null;
  transcript: string;
  turn_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "ReportDraft".
 */
export interface ReportDraft {
  competencies: CompetencyDraft[];
  improvements: string[];
  next_topics: string[];
  strengths: string[];
  topics: TopicDraft[];
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TopicDraft".
 */
export interface TopicDraft {
  model_answer: string;
  topic_id: string;
}
/**
 * POST /api/sessions 요청입니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "SessionCreate".
 */
export interface SessionCreate {
  analysis_id: string;
  duration_min?: number;
  jd_text?: string | null;
  mode: InterviewMode;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "SessionCreated".
 */
export interface SessionCreated {
  first: BrainReply;
  session_id: string;
}
/**
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "SessionEnded".
 */
export interface SessionEnded {
  report_id: string;
}
/**
 * POST /api/sessions/{id}/turns 요청입니다.
 *
 * turn_id는 클라이언트가 만든 UUID이고, 같은 값이 다시 오면 처음 결과를 그대로 돌려줍니다.
 * 시각은 클라이언트 epoch 밀리초이며 지연 측정에만 씁니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "TurnRequest".
 */
export interface TurnRequest {
  source: TurnSource;
  speech_ended_at?: number | null;
  speech_started_at?: number | null;
  transcript: string;
  turn_id: string;
}
/**
 * POST /api/sessions/{id}/voice-token 응답입니다. 브라우저에는 임시 토큰만 보냅니다.
 *
 * This interface was referenced by `GitInsightContracts`'s JSON-Schema
 * via the `definition` "VoiceToken".
 */
export interface VoiceToken {
  client_secret: string;
  expires_at: string;
  instructions: string;
  model: string;
  provider: "openai" | "gemini";
  tools: {
    [k: string]: unknown;
  }[];
}
