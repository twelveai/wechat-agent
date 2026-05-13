import type { Metadata } from "next";
import { SummaryReportPage } from "../ui/summary-report-page";

export const metadata: Metadata = {
  title: "AI Summary Report | WeChat Alpha Desk",
  description: "Focused AI summary report view for local WeChat conversations.",
};

export default function Page() {
  return <SummaryReportPage />;
}

