import { useEffect, useState } from "react";

import MockBadge from "./components/MockBadge";
import DiagnosePage from "./pages/DiagnosePage";
import DiagnosisReportPage from "./pages/DiagnosisReportPage";
import Gate2Page from "./pages/Gate2Page";
import ReviewPage from "./pages/ReviewPage";
import RewritePage from "./pages/RewritePage";
import SettingsPage from "./pages/SettingsPage";
import UploadPage from "./pages/UploadPage";

interface Span {
  start: number;
  end: number;
}

type Route =
  | { page: "upload" }
  | { page: "review"; resumeId: string; highlightSpan: Span | null }
  | { page: "diagnose"; resumeId: string }
  | { page: "report"; diagnosisId: string }
  | { page: "rewrite"; diagnosisId: string }
  | { page: "gate2"; rewriteId: string }
  | { page: "settings" };

/** 诊断报告 quote 跳转参数：#/review/{id}?span=12-30（UTF-16 偏移）。 */
function parseSpan(query: string): Span | null {
  const raw = new URLSearchParams(query).get("span");
  if (!raw) return null;
  const match = /^(\d+)-(\d+)$/.exec(raw);
  if (!match) return null;
  const start = Number(match[1]);
  const end = Number(match[2]);
  return end > start ? { start, end } : null;
}

function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = path.split("?");
  if (pathPart.startsWith("review/")) {
    return {
      page: "review",
      resumeId: pathPart.slice("review/".length),
      highlightSpan: parseSpan(queryPart),
    };
  }
  if (pathPart.startsWith("diagnose/")) {
    return { page: "diagnose", resumeId: pathPart.slice("diagnose/".length) };
  }
  if (pathPart.startsWith("diagnosis/")) {
    return { page: "report", diagnosisId: pathPart.slice("diagnosis/".length) };
  }
  if (pathPart.startsWith("rewrite/")) {
    return { page: "rewrite", diagnosisId: pathPart.slice("rewrite/".length) };
  }
  if (pathPart.startsWith("gate2/")) {
    return { page: "gate2", rewriteId: pathPart.slice("gate2/".length) };
  }
  if (pathPart === "settings") return { page: "settings" };
  return { page: "upload" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/upload">
          简历优化 Agent
          <span className="brand-sub">M3 · 改写 + 校验 + 导出</span>
        </a>
        <nav className="topnav">
          <a href="#/upload" className={route.page === "upload" ? "active" : ""}>
            上传
          </a>
          <a href="#/settings" className={route.page === "settings" ? "active" : ""}>
            设置
          </a>
        </nav>
        <MockBadge />
      </header>
      <main className="content">
        {route.page === "review" ? (
          <ReviewPage resumeId={route.resumeId} highlightSpan={route.highlightSpan} />
        ) : route.page === "diagnose" ? (
          <DiagnosePage resumeId={route.resumeId} />
        ) : route.page === "report" ? (
          <DiagnosisReportPage diagnosisId={route.diagnosisId} />
        ) : route.page === "rewrite" ? (
          <RewritePage diagnosisId={route.diagnosisId} />
        ) : route.page === "gate2" ? (
          <Gate2Page rewriteId={route.rewriteId} />
        ) : route.page === "settings" ? (
          <SettingsPage />
        ) : (
          <UploadPage />
        )}
      </main>
    </div>
  );
}
