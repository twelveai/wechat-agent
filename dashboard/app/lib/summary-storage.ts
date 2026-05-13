import type { SummaryResponse } from "./wechat-api";

export const SUMMARY_STORAGE_KEY = "wechat-agent:summary-report";

export type StoredSummaryReport = {
  id: string;
  status: "loading" | "ready" | "error";
  createdAt: number;
  chatName: string;
  range: {
    start: string;
    end: string;
  };
  response?: SummaryResponse;
  error?: string;
};
