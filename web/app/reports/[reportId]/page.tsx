import ReportScreen from "@/components/ReportScreen";

// 리포트 화면 (#20). 로그인 쿠키로 서버의 GET /api/reports/{id}를 부릅니다. 본인 리포트만 열립니다.
export const dynamic = "force-dynamic";

export default async function Page({ params }: { params: Promise<{ reportId: string }> }) {
  const { reportId } = await params;
  return <ReportScreen reportId={reportId} />;
}
