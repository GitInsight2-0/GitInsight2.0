import { notFound } from "next/navigation";
import InterviewScreen from "@/components/InterviewScreen";
import { DEMO_ANALYSIS_ID, DEMO_REPO } from "@/lib/demo";

// 실험용 면접 화면. 서버도 LAB_ENABLED=true면 대본 두뇌로 답합니다. 실제 모드는 /interview/[analysisId]입니다.
export const dynamic = "force-dynamic";

export default function Page() {
  if (process.env.LAB_ENABLED !== "true") notFound();
  return <InterviewScreen analysisId={DEMO_ANALYSIS_ID} repo={DEMO_REPO} />;
}
