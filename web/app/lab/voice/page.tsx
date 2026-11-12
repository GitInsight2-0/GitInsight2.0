import { notFound } from "next/navigation";
import VoiceLab from "@/components/VoiceLab";

// 서버의 LAB_ENABLED와 같은 규칙입니다. 데모·배포에서는 이 페이지가 열리지 않습니다.
export const dynamic = "force-dynamic";

/** 대본 두뇌가 쓰는 샘플 분석 ID (fixtures/analysis/gitinsight_agent.json) */
const LAB_ANALYSIS_ID = "demo-gitinsight-agent";

export default function Page() {
  if (process.env.LAB_ENABLED !== "true") notFound();
  return <VoiceLab analysisId={LAB_ANALYSIS_ID} />;
}
