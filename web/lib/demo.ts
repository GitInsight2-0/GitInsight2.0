/**
 * 레포 목록과 선택 화면(#18 2단계)이 붙기 전까지 쓰는 데모 분석.
 * 서버의 샘플 분석(fixtures/analysis/gitinsight_agent.json)과 데모 스냅샷이 같은 id를 씁니다.
 */
export const DEMO_ANALYSIS_ID = "demo-gitinsight-agent";
/** 같은 파일의 repo_full_name. 코드 뷰어가 이 레포에서 코드를 받습니다 */
export const DEMO_REPO = "tttae210-sys/gitinsight_agent";

/**
 * 분석 id로 레포 이름을 찾습니다.
 * 지금 계약에는 세션에서 레포 이름을 받을 길이 없어서 데모 분석만 압니다. 모르면 코드 뷰어는 위치만 보여 줍니다.
 */
export function repoForAnalysis(analysisId: string): string | null {
  return analysisId === DEMO_ANALYSIS_ID ? DEMO_REPO : null;
}
