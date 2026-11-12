import { notFound } from "next/navigation";
import InterviewScreen from "@/components/InterviewScreen";

// 면접 화면 중간 단계 (#30). 레포 선택과 로그인(T4)이 붙기 전까지 데모 분석 결과로만 엽니다.
export const dynamic = "force-dynamic";

/** 대본 두뇌가 쓰는 샘플 분석 (fixtures/analysis/gitinsight_agent.json) */
const LAB_ANALYSIS_ID = "demo-gitinsight-agent";
/**
 * 코드 뷰어가 코드를 받을 레포. 같은 파일의 repo_full_name입니다.
 * 지금 계약에는 세션에서 레포 이름을 받을 길이 없어 여기 적어 둡니다 (PR 리뷰 포인트 참고).
 */
const LAB_REPO = "tttae210-sys/gitinsight_agent";

export default function Page() {
  if (process.env.LAB_ENABLED !== "true") notFound();
  return <InterviewScreen analysisId={LAB_ANALYSIS_ID} repo={LAB_REPO} />;
}
