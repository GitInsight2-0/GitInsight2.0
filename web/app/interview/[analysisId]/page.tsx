import InterviewScreen from "@/components/InterviewScreen";
import { repoForAnalysis } from "@/lib/demo";
import { isValidRepo } from "@/lib/github/code";

// 실제 모드 면접 화면 (#59). 로그인 쿠키로 서버의 진짜 면접 두뇌를 씁니다. 로그인이 없으면 화면이 첫 화면으로 안내합니다.
export const dynamic = "force-dynamic";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ analysisId: string }>;
  searchParams: Promise<{ repo?: string | string[] }>;
}) {
  const { analysisId } = await params;
  const { repo } = await searchParams;
  const id = decodeURIComponent(analysisId);
  // 코드 뷰어가 코드를 받을 레포. 첫 화면에서 고른 레포 이름이 ?repo=로 옵니다 (계약에 세션의 레포 이름이 없어서).
  const fromQuery = typeof repo === "string" && isValidRepo(repo) ? repo : null;
  return <InterviewScreen analysisId={id} repo={fromQuery ?? repoForAnalysis(id)} />;
}
