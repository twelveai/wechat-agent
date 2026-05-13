"use client";

import Link from "next/link";
import { type ReactNode, useMemo, useSyncExternalStore } from "react";
import { SUMMARY_STORAGE_KEY, type StoredSummaryReport } from "../lib/summary-storage";
import type { SummaryReport, SummaryResponse } from "../lib/wechat-api";
import { Icon, IconName } from "./icons";

export function SummaryReportPage() {
  const reportId = useSyncExternalStore(subscribeNoop, readReportId, readServerReportId);
  const rawReport = useSyncExternalStore(subscribeSummaryStorage, readStoredReportRaw, readServerStoredReportRaw);
  const report = useMemo(() => parseStoredReport(rawReport), [rawReport]);

  const matchesRequestedReport = !reportId || report?.id === reportId;
  const response = matchesRequestedReport && report?.status === "ready" ? report.response : undefined;
  const normalizedResponse = useMemo(() => response ? normalizeSummaryResponse(response) : undefined, [response]);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex min-h-screen w-full max-w-[800px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <ReportHeader report={matchesRequestedReport ? report : null} response={normalizedResponse} />

        {!matchesRequestedReport ? (
          <ReportEmpty
            icon="alert"
            title="没有找到这次摘要"
            text="这个结果页没有匹配到对应的本地摘要记录。请回到 dashboard 重新生成。"
          />
        ) : report?.status === "error" ? (
          <ReportEmpty
            icon="alert"
            title="摘要生成失败"
            text={report.error ?? "生成过程中出现未知错误。"}
          />
        ) : normalizedResponse ? (
          <SummaryNarrative response={normalizedResponse} />
        ) : (
          <ReportLoading report={report} />
        )}

        <FinalActions />
      </div>
    </main>
  );
}

function ReportHeader({
  report,
  response,
}: {
  report: StoredSummaryReport | null;
  response?: SummaryResponse;
}) {
  const included = response?.messages.included;
  return (
    <header className="relative overflow-hidden rounded-xl border border-white/15 bg-black/40 px-4 py-5 shadow-2xl shadow-black/30 backdrop-blur-xl sm:px-5">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-300/80 to-transparent" />
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-300/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-amber-200">
            <Icon name="shield" className="h-3.5 w-3.5" />
            Secure Report
          </span>
          <span className="inline-flex items-center gap-2 rounded-full border border-violet-300/30 bg-violet-400/10 px-3 py-1 text-xs font-semibold text-violet-200">
            <span className={`h-2 w-2 rounded-full ${response ? "bg-emerald-300" : "bg-amber-300"}`} />
            {response ? "Ready" : "Generating"}
          </span>
        </div>
        <div>
          <p className="font-heading text-[11px] font-semibold uppercase tracking-[0.2em] text-violet-200">
            AI Summary Dispatch
          </p>
          <h1 className="mt-2 font-heading text-2xl font-semibold text-white sm:text-3xl">
            {response?.summary.title || report?.chatName || "摘要结果页"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            单列聚焦阅读视图，按重点、决策、行动、风险和原话逐章展开。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <HeaderMetric label="Messages" value={typeof included === "number" ? included.toLocaleString() : "-"} />
          <HeaderMetric label="Sentiment" value={response?.summary.sentiment || "-"} />
          <HeaderMetric label="Range" value={response?.summary.time_range || formatReportRange(report)} />
        </div>
      </div>
    </header>
  );
}

function HeaderMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-slate-950/55 px-3 py-3">
      <p className="font-heading text-[10px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-100">{value}</p>
    </div>
  );
}

function SummaryNarrative({ response }: { response: SummaryResponse }) {
  const summary = response.summary;
  const progress = useMemo(
    () => [
      { label: "Brief", active: true },
      { label: "Signals", active: summary.key_points.length > 0 },
      { label: "Actions", active: summary.action_items.length > 0 },
      { label: "Risks", active: summary.risks.length > 0 },
    ],
    [summary.action_items.length, summary.key_points.length, summary.risks.length],
  );

  return (
    <div className="space-y-5">
      <ProgressRail items={progress} />

      <Chapter tone="amber" eyebrow="Intro Hook" title="执行摘要" icon="fileText">
        <p className="text-sm leading-7 text-slate-300">{summary.executive_summary}</p>
      </Chapter>

      <Chapter tone="sky" eyebrow="Chapter 1" title="核心重点" icon="target">
        <ItemList
          empty="没有提炼出核心重点。"
          items={summary.key_points.map((item) => ({
            title: item.point,
            body: item.evidence,
            tag: item.importance,
          }))}
        />
      </Chapter>

      <Chapter tone="violet" eyebrow="Chapter 2" title="决策与行动" icon="check">
        <div className="space-y-3">
          <ItemList
            empty="没有明确决策。"
            items={summary.decisions.map((item) => ({
              title: item.decision,
              body: item.evidence,
            }))}
          />
          <div className="grid gap-3">
            {summary.action_items.length ? summary.action_items.map((item, index) => (
              <ReportCard key={`${item.task}-${index}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <Tag tone="amber">{item.priority || "未标注"}</Tag>
                  <span className="font-mono text-[11px] text-slate-500">{item.due_time || "未明确时间"}</span>
                </div>
                <p className="mt-2 text-sm font-semibold leading-6 text-white">{item.task}</p>
                <p className="mt-1 text-xs text-slate-400">负责人：{item.owner || "未明确"}</p>
                <p className="mt-2 text-xs leading-5 text-slate-400">{item.context}</p>
              </ReportCard>
            )) : <EmptyLine text="没有明确待办。" />}
          </div>
        </div>
      </Chapter>

      <Chapter tone="rose" eyebrow="Chapter 3" title="风险与待确认" icon="shield">
        <div className="space-y-3">
          <ItemList
            empty="没有发现明显风险。"
            items={summary.risks.map((item) => ({
              title: item.risk,
              body: item.evidence,
              tag: item.severity,
              tone: "rose" as const,
            }))}
          />
          <ItemList
            empty="没有待确认问题。"
            items={summary.open_questions.map((item) => ({
              title: item.question,
              body: item.context,
            }))}
          />
        </div>
      </Chapter>

      <Chapter tone="emerald" eyebrow="Climax" title="关键原话" icon="quote">
        <div className="space-y-3">
          {summary.notable_messages.length ? summary.notable_messages.map((item, index) => (
            <blockquote key={`${item.quote}-${index}`} className="rounded-xl border border-white/12 bg-white/[0.05] p-4">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                <span className="font-semibold text-emerald-200">{item.sender}</span>
                <span className="font-mono">{item.time}</span>
              </div>
              <p className="mt-2 text-sm leading-7 text-white">&ldquo;{item.quote}&rdquo;</p>
              <p className="mt-2 text-xs leading-5 text-slate-400">{item.reason}</p>
            </blockquote>
          )) : <EmptyLine text="没有可引用的关键原话。" />}
        </div>
      </Chapter>
    </div>
  );
}

function ProgressRail({ items }: { items: Array<{ label: string; active: boolean }> }) {
  return (
    <nav className="rounded-xl border border-white/15 bg-black/35 p-2 backdrop-blur-xl" aria-label="摘要章节">
      <div className="grid gap-2 sm:grid-cols-4">
        {items.map((item) => (
          <div
            key={item.label}
            className={`rounded-lg border px-3 py-2 text-center font-heading text-[10px] uppercase tracking-[0.16em] transition-colors duration-200 ${
              item.active
                ? "border-amber-300/30 bg-amber-300/10 text-amber-200"
                : "border-white/10 bg-white/[0.03] text-slate-500"
            }`}
          >
            {item.label}
          </div>
        ))}
      </div>
    </nav>
  );
}

function Chapter({
  tone,
  eyebrow,
  title,
  icon,
  children,
}: {
  tone: "amber" | "sky" | "violet" | "rose" | "emerald";
  eyebrow: string;
  title: string;
  icon: IconName;
  children: ReactNode;
}) {
  const classes = {
    amber: "border-amber-300/25 bg-amber-300/10 text-amber-200",
    sky: "border-sky-300/25 bg-sky-300/10 text-sky-200",
    violet: "border-violet-300/25 bg-violet-400/10 text-violet-200",
    rose: "border-rose-300/25 bg-rose-300/10 text-rose-200",
    emerald: "border-emerald-300/25 bg-emerald-300/10 text-emerald-200",
  };

  return (
    <section className="rounded-xl border border-white/15 bg-black/35 p-4 shadow-xl shadow-black/20 backdrop-blur-xl sm:p-5">
      <div className="mb-4 flex items-start gap-3">
        <span className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${classes[tone]}`}>
          <Icon name={icon} />
        </span>
        <div className="min-w-0">
          <p className="font-heading text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">{eyebrow}</p>
          <h2 className="mt-1 text-lg font-semibold text-white">{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

function ItemList({
  empty,
  items,
}: {
  empty: string;
  items: Array<{ title: string; body: string; tag?: string; tone?: "amber" | "sky" | "rose" }>;
}) {
  if (!items.length) return <EmptyLine text={empty} />;
  return (
    <div className="space-y-3">
      {items.map((item, index) => (
        <ReportCard key={`${item.title}-${index}`}>
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-semibold leading-6 text-white">{item.title}</p>
            {item.tag ? <Tag tone={item.tone ?? "sky"}>{item.tag}</Tag> : null}
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">{item.body}</p>
        </ReportCard>
      ))}
    </div>
  );
}

function ReportCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-white/12 bg-white/[0.05] p-4 transition-colors duration-200 hover:border-amber-300/30">
      {children}
    </div>
  );
}

function Tag({ tone, children }: { tone: "amber" | "sky" | "rose"; children: ReactNode }) {
  const classes = {
    amber: "border-amber-300/25 bg-amber-300/10 text-amber-200",
    sky: "border-sky-300/25 bg-sky-300/10 text-sky-200",
    rose: "border-rose-300/25 bg-rose-300/10 text-rose-200",
  };
  return (
    <span className={`inline-flex shrink-0 items-center rounded-md border px-2 py-1 text-[11px] font-semibold ${classes[tone]}`}>
      {children}
    </span>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.03] px-4 py-3 text-sm text-slate-500">
      {text}
    </div>
  );
}

function ReportLoading({ report }: { report: StoredSummaryReport | null }) {
  return (
    <section className="rounded-xl border border-white/15 bg-black/35 p-5 shadow-xl shadow-black/20 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-amber-300/25 bg-amber-300/10 text-amber-200">
          <Icon name="activity" className="h-4 w-4 animate-spin" />
        </span>
        <div>
          <h2 className="text-base font-semibold text-white">正在生成摘要</h2>
          <p className="mt-1 text-sm text-slate-400">
            {report?.chatName ? `会话：${report.chatName}` : "等待 dashboard 写入摘要结果。"}
          </p>
        </div>
      </div>
      <div className="mt-5 grid gap-3">
        {[0, 1, 2].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-xl border border-white/10 bg-white/[0.05]" />
        ))}
      </div>
    </section>
  );
}

function ReportEmpty({ icon, title, text }: { icon: IconName; title: string; text: string }) {
  return (
    <section className="rounded-xl border border-white/15 bg-black/35 p-5 text-center shadow-xl shadow-black/20 backdrop-blur-xl">
      <span className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-xl border border-amber-300/25 bg-amber-300/10 text-amber-200">
        <Icon name={icon} />
      </span>
      <h2 className="mt-4 text-base font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-slate-400">{text}</p>
    </section>
  );
}

function FinalActions() {
  return (
    <footer className="flex flex-col gap-2 rounded-xl border border-white/15 bg-black/35 p-4 backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between">
      <p className="text-xs leading-5 text-slate-500">
        结果保存在当前浏览器的本地存储中，重新生成会覆盖最新摘要入口。
      </p>
      <Link
        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-cta px-4 text-sm font-semibold text-white transition-colors duration-200 hover:bg-violet-500 focus:outline-none focus:ring-2 focus:ring-cta focus:ring-offset-2 focus:ring-offset-background"
        href="/"
      >
        <Icon name="arrowRight" />
        返回 Dashboard
      </Link>
    </footer>
  );
}

function subscribeNoop() {
  return () => undefined;
}

function subscribeSummaryStorage(onStoreChange: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === SUMMARY_STORAGE_KEY) onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}

function readReportId() {
  return new URLSearchParams(window.location.search).get("id") ?? "";
}

function readServerReportId() {
  return "";
}

function readStoredReportRaw() {
  return window.localStorage.getItem(SUMMARY_STORAGE_KEY);
}

function readServerStoredReportRaw() {
  return null;
}

function parseStoredReport(raw: string | null) {
  try {
    return raw ? (JSON.parse(raw) as StoredSummaryReport) : null;
  } catch {
    return null;
  }
}

function normalizeSummaryResponse(response: SummaryResponse): SummaryResponse {
  return {
    ...response,
    summary: normalizeSummaryReport(response.summary),
  };
}

function normalizeSummaryReport(summary: SummaryReport): SummaryReport {
  const embedded = parseEmbeddedSummary(summary.executive_summary);
  if (!embedded) return summary;
  return {
    ...summary,
    ...embedded,
    message_count: summary.message_count || embedded.message_count || 0,
  };
}

function parseEmbeddedSummary(value: string): Partial<SummaryReport> | null {
  const payload = parseJsonObject(value);
  if (!payload || !looksLikeSummaryPayload(payload)) return null;
  return {
    title: stringValue(payload.title),
    executive_summary: stringValue(payload.executive_summary),
    message_count: numberValue(payload.message_count),
    time_range: stringValue(payload.time_range),
    sentiment: stringValue(payload.sentiment),
    key_points: objectList(payload.key_points, ["point", "importance", "evidence"]),
    decisions: objectList(payload.decisions, ["decision", "evidence"]),
    action_items: objectList(payload.action_items, ["task", "owner", "due_time", "priority", "context"]),
    risks: objectList(payload.risks, ["risk", "severity", "evidence"]),
    open_questions: objectList(payload.open_questions, ["question", "context"]),
    notable_messages: objectList(payload.notable_messages, ["time", "sender", "quote", "reason"]),
  };
}

function parseJsonObject(value: string): Record<string, unknown> | null {
  const text = extractJsonText(value);
  try {
    const payload = JSON.parse(text);
    if (isPlainObject(payload)) return payload;
    if (typeof payload === "string") {
      const nested = JSON.parse(extractJsonText(payload));
      return isPlainObject(nested) ? nested : null;
    }
    return null;
  } catch {
    return null;
  }
}

function extractJsonText(value: string) {
  let text = value.trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  }
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  return start !== -1 && end > start ? text.slice(start, end + 1) : text;
}

function looksLikeSummaryPayload(value: Record<string, unknown>) {
  return (
    "executive_summary" in value ||
    "key_points" in value ||
    "decisions" in value ||
    "action_items" in value ||
    "notable_messages" in value
  );
}

function objectList<T extends string>(value: unknown, keys: T[]): Array<Record<T, string>> {
  if (!Array.isArray(value)) return [];
  return value
    .filter(isPlainObject)
    .map((item) => {
      return keys.reduce((result, key) => {
        result[key] = stringValue(item[key]);
        return result;
      }, {} as Record<T, string>);
    })
    .filter((item) => Object.values(item).some(Boolean));
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatReportRange(report: StoredSummaryReport | null) {
  if (!report?.range.start && !report?.range.end) return "-";
  return `${report.range.start || "-"} / ${report.range.end || "-"}`;
}
