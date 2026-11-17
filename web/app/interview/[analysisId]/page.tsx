import InterviewScreen from "@/components/InterviewScreen";
import { repoForAnalysis } from "@/lib/demo";

// 실제 모드 면접 화면 (#59). 로그인 쿠키로 서버의 진짜 면접 두뇌를 씁니다. 로그인이 없으면 화면이 첫 화면으로 안내합니다.
export const dynamic = "force-dynamic";

export default async function Page({ params }: { params: Promise<{ analysisId: string }> }) {
  const { analysisId } = await params;
  const id = decodeURIComponent(analysisId);
  return <InterviewScreen analysisId={id} repo={repoForAnalysis(id)} />;
}
