import PrepSheet from "@/components/PrepSheet";

// 면접관 질문지 (#173). 로그인 쿠키로 서버의 GET /api/analyses/{id}/result를 부릅니다. 본인 분석만 열립니다.
export const dynamic = "force-dynamic";

export default async function Page({ params }: { params: Promise<{ analysisId: string }> }) {
  const { analysisId } = await params;
  return <PrepSheet analysisId={analysisId} />;
}
