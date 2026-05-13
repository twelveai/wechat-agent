"use client";

import Image from "next/image";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Chat,
  Contact,
  getWechat,
  Health,
  ListResponse,
  Message,
  Overview,
  postWechat,
  Session,
  SummaryResponse,
} from "../lib/wechat-api";
import { Icon, IconName } from "./icons";

type LoadState = "idle" | "loading" | "ready" | "error";
type SummaryState = "idle" | "loading" | "ready" | "error";

const messageTypeOptions = [
  { label: "全部类型", value: "" },
  { label: "文本", value: "1" },
  { label: "图片/媒体", value: "3" },
  { label: "链接/卡片", value: "49" },
  { label: "系统", value: "10000" },
  { label: "拍一拍", value: "266287972401" },
];

export function DashboardApp() {
  const [health, setHealth] = useState<Health | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedChat, setSelectedChat] = useState("");
  const [query, setQuery] = useState("");
  const [messageType, setMessageType] = useState("");
  const [status, setStatus] = useState<LoadState>("idle");
  const [error, setError] = useState("");
  const [summaryStart, setSummaryStart] = useState("");
  const [summaryEnd, setSummaryEnd] = useState("");
  const [summaryStatus, setSummaryStatus] = useState<SummaryState>("idle");
  const [summaryError, setSummaryError] = useState("");
  const [summaryResult, setSummaryResult] = useState<SummaryResponse | null>(null);

  const loadShell = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const [healthData, overviewData, contactsData, sessionsData, chatsData] = await Promise.all([
        getWechat<Health>("health"),
        getWechat<Overview>("overview"),
        getWechat<ListResponse<Contact>>("contacts", { limit: 12 }),
        getWechat<ListResponse<Session>>("sessions", { limit: 12 }),
        getWechat<ListResponse<Chat>>("chats", { limit: 80 }),
      ]);
      setHealth(healthData);
      setOverview(overviewData);
      setContacts(contactsData.items);
      setSessions(sessionsData.items);
      setChats(chatsData.items);
      setSelectedChat((current) => current || chatsData.items[0]?.username || "");
      setStatus("ready");
    } catch (loadError) {
      setStatus("error");
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    }
  }, []);

  const loadMessages = useCallback(async (chat = selectedChat) => {
    if (!chat && !query) return;
    setError("");
    try {
      const data = await getWechat<ListResponse<Message>>("messages", {
        chat: chat || undefined,
        q: query || undefined,
        type: messageType || undefined,
        limit: 120,
      });
      setMessages(data.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    }
  }, [messageType, query, selectedChat]);

  const summarizeMessages = useCallback(async () => {
    if (!selectedChat) return;
    const after = timestampFromInput(summaryStart);
    const before = timestampFromInput(summaryEnd);
    if (after && before && after > before) {
      setSummaryStatus("error");
      setSummaryError("开始时间不能晚于结束时间。");
      return;
    }
    setSummaryStatus("loading");
    setSummaryError("");
    try {
      const result = await postWechat<SummaryResponse>("summary", {
        chat: selectedChat,
        after,
        before,
      });
      setSummaryResult(result);
      setSummaryStatus("ready");
    } catch (summaryLoadError) {
      setSummaryStatus("error");
      setSummaryError(summaryLoadError instanceof Error ? summaryLoadError.message : String(summaryLoadError));
    }
  }, [selectedChat, summaryEnd, summaryStart]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadShell();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadShell]);

  useEffect(() => {
    if (!selectedChat) return;
    const timer = window.setTimeout(() => {
      void loadMessages(selectedChat);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadMessages, selectedChat]);

  const selectedChatItem = useMemo(
    () => chats.find((chat) => chat.username === selectedChat),
    [chats, selectedChat],
  );

  useEffect(() => {
    if (!selectedChat) return;
    const timer = window.setTimeout(() => {
      const end = selectedChatItem?.latest_create_time ?? Math.floor(Date.now() / 1000);
      const start = end - 7 * 24 * 60 * 60;
      setSummaryStart(inputValueFromTimestamp(start));
      setSummaryEnd(inputValueFromTimestamp(end));
      setSummaryResult(null);
      setSummaryError("");
      setSummaryStatus("idle");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedChat, selectedChatItem?.latest_create_time]);

  const filteredChats = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return chats;
    return chats.filter((chat) => {
      return (
        chat.display_name.toLowerCase().includes(needle) ||
        chat.username.toLowerCase().includes(needle) ||
        chat.table.toLowerCase().includes(needle)
      );
    });
  }, [chats, query]);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex min-h-screen w-full max-w-[1680px] flex-col gap-4 px-4 py-4 lg:px-5">
        <Header status={status} onRefresh={loadShell} />

        {error ? <ErrorBanner message={error} /> : null}

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Overview metrics">
          <Metric label="聊天会话" value={overview?.chat_count} icon="message" tone="blue" />
          <Metric label="消息总量" value={overview?.message_count} icon="activity" tone="amber" />
          <Metric label="联系人" value={overview?.contact_count} icon="users" tone="emerald" />
          <Metric label="会话记录" value={overview?.session_count} icon="database" tone="slate" />
        </section>

        <section className="grid min-h-[720px] gap-4 xl:grid-cols-[360px_minmax(0,1fr)_360px]">
          <aside className="min-h-0 overflow-hidden rounded-lg border border-border bg-panel">
            <PanelHeader
              title="会话"
              subtitle={`${filteredChats.length.toLocaleString()} 个可查询聊天`}
              icon="filter"
            />
            <div className="border-b border-border p-3">
              <SearchBox value={query} onChange={setQuery} onSubmit={() => void loadMessages()} />
            </div>
            <div className="scrollbar-subtle max-h-[600px] overflow-y-auto">
              {filteredChats.slice(0, 120).map((chat) => (
                <ChatRow
                  key={chat.username}
                  chat={chat}
                  selected={chat.username === selectedChat}
                  onClick={() => setSelectedChat(chat.username)}
                />
              ))}
            </div>
          </aside>

          <section className="min-w-0 overflow-hidden rounded-lg border border-border bg-panel">
            <div className="flex flex-col gap-3 border-b border-border bg-panel px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Conversation</p>
                <h1 className="mt-1 truncate text-xl font-semibold text-foreground">
                  {selectedChatItem?.display_name ?? "选择一个会话"}
                </h1>
                <p className="mt-1 truncate text-sm text-slate-600 dark:text-slate-300">
                  {selectedChatItem?.username ?? "启动 Python API 后可读取本地解密消息库"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <label className="sr-only" htmlFor="message-type">消息类型</label>
                <select
                  id="message-type"
                  className="h-10 rounded-md border border-border bg-panel-muted px-3 text-sm text-foreground outline-none transition-colors focus:border-primary"
                  value={messageType}
                  onChange={(event) => setMessageType(event.target.value)}
                >
                  {messageTypeOptions.map((option) => (
                    <option key={option.label} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <button
                  className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-3 text-sm font-semibold text-white transition-colors hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:opacity-50 dark:text-slate-950 dark:hover:bg-blue-300"
                  disabled={!selectedChat && !query}
                  onClick={() => void loadMessages()}
                  type="button"
                >
                  <Icon name="search" />
                  查询
                </button>
              </div>
            </div>

            <SummaryWorkspace
              chatName={selectedChatItem?.display_name ?? "未选择会话"}
              disabled={!selectedChat || summaryStatus === "loading"}
              endValue={summaryEnd}
              error={summaryError}
              result={summaryResult}
              startValue={summaryStart}
              status={summaryStatus}
              onEndChange={setSummaryEnd}
              onStartChange={setSummaryStart}
              onSubmit={summarizeMessages}
            />

            <MessageThread messages={messages} />
          </section>

          <aside className="space-y-4">
            <StatusPanel health={health} />
            <RecentSessions sessions={sessions} />
            <ContactsPanel contacts={contacts} />
          </aside>
        </section>
      </div>
    </main>
  );
}

function Header({ status, onRefresh }: { status: LoadState; onRefresh: () => void }) {
  return (
    <header className="flex flex-col gap-3 rounded-lg border border-border bg-panel px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">Local WeChat Intelligence</p>
        <h1 className="mt-1 text-2xl font-semibold text-foreground">WeChat Data AI Dashboard</h1>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600 dark:text-slate-300">
          本地只读查询已解密 SQLite，聚合消息、联系人、会话和素材线索。
        </p>
      </div>
      <button
        className="inline-flex h-10 w-fit items-center gap-2 rounded-md border border-border bg-panel-muted px-3 text-sm font-semibold text-foreground transition-colors hover:border-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
        onClick={onRefresh}
        type="button"
      >
        <Icon name="refresh" />
        {status === "loading" ? "刷新中" : "刷新数据"}
      </button>
    </header>
  );
}

function Metric({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value?: number;
  icon: "message" | "activity" | "users" | "database";
  tone: "blue" | "amber" | "emerald" | "slate";
}) {
  const toneClasses = {
    blue: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-200",
    amber: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-200",
    emerald: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-200",
    slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  };
  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-600 dark:text-slate-300">{label}</span>
        <span className={`inline-flex h-9 w-9 items-center justify-center rounded-md ${toneClasses[tone]}`}>
          <Icon name={icon} />
        </span>
      </div>
      <p className="mt-3 font-mono text-3xl font-semibold text-foreground">{formatNumber(value)}</p>
    </div>
  );
}

function PanelHeader({ title, subtitle, icon }: { title: string; subtitle: string; icon: IconName }) {
  return (
    <div className="flex items-center justify-between border-b border-border px-4 py-3">
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{subtitle}</p>
      </div>
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary-soft text-primary">
        <Icon name={icon} />
      </span>
    </div>
  );
}

function SearchBox({ value, onChange, onSubmit }: { value: string; onChange: (value: string) => void; onSubmit: () => void }) {
  return (
    <div className="flex gap-2">
      <label className="sr-only" htmlFor="chat-search">关键词</label>
      <div className="relative min-w-0 flex-1">
        <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input
          id="chat-search"
          className="h-10 w-full rounded-md border border-border bg-panel-muted pl-9 pr-3 text-sm text-foreground outline-none transition-colors placeholder:text-slate-500 focus:border-primary"
          placeholder="搜索会话或消息关键词"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSubmit();
          }}
        />
      </div>
      <button
        className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-panel-muted text-foreground transition-colors hover:border-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary"
        onClick={onSubmit}
        type="button"
        aria-label="执行搜索"
      >
        <Icon name="arrowRight" />
      </button>
    </div>
  );
}

function ChatRow({ chat, selected, onClick }: { chat: Chat; selected: boolean; onClick: () => void }) {
  return (
    <button
      className={`grid w-full grid-cols-[1fr_auto] gap-2 border-b border-border px-4 py-3 text-left transition-colors hover:bg-panel-muted focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary ${
        selected ? "bg-primary-soft" : "bg-panel"
      }`}
      onClick={onClick}
      type="button"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-foreground">{chat.display_name}</span>
        <span className="mt-1 block truncate font-mono text-[11px] text-slate-500 dark:text-slate-400">{chat.username}</span>
      </span>
      <span className="text-right">
        <span className="block font-mono text-sm font-semibold text-primary">{formatNumber(chat.message_count)}</span>
        <span className="mt-1 block text-[11px] text-slate-500 dark:text-slate-400">{formatTime(chat.latest_create_time)}</span>
      </span>
    </button>
  );
}

function SummaryWorkspace({
  chatName,
  disabled,
  endValue,
  error,
  result,
  startValue,
  status,
  onEndChange,
  onStartChange,
  onSubmit,
}: {
  chatName: string;
  disabled: boolean;
  endValue: string;
  error: string;
  result: SummaryResponse | null;
  startValue: string;
  status: SummaryState;
  onEndChange: (value: string) => void;
  onStartChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <section className="border-b border-border bg-slate-50/80 px-4 py-4 dark:bg-slate-950/30">
      <form
        className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-primary">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary-soft">
              <Icon name="sparkles" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-foreground">微信消息总结</h2>
              <p className="mt-0.5 truncate text-xs text-slate-600 dark:text-slate-300">
                仅汇总所选时间范围内的文本消息：{chatName}
              </p>
            </div>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] xl:min-w-[640px]">
          <DateTimeField id="summary-start" label="开始时间" value={startValue} onChange={onStartChange} />
          <DateTimeField id="summary-end" label="结束时间" value={endValue} onChange={onEndChange} />
          <button
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-accent px-4 text-sm font-semibold text-slate-950 transition-colors hover:bg-amber-400 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:opacity-50"
            disabled={disabled}
            type="submit"
          >
            <Icon name={status === "loading" ? "activity" : "sparkles"} className={status === "loading" ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {status === "loading" ? "总结中" : "开始总结"}
          </button>
        </div>
      </form>

      {error ? <SummaryError message={error} /> : null}
      {result ? <SummaryReportView response={result} /> : <SummaryPlaceholder status={status} />}
    </section>
  );
}

function DateTimeField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
        <Icon name="clock" className="h-3.5 w-3.5" />
        {label}
      </span>
      <input
        id={id}
        className="h-10 w-full rounded-md border border-border bg-panel px-3 text-sm text-foreground outline-none transition-colors focus:border-primary"
        type="datetime-local"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function SummaryPlaceholder({ status }: { status: SummaryState }) {
  if (status === "loading") {
    return (
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        {[0, 1, 2].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-md bg-panel-muted" />
        ))}
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-md border border-dashed border-border bg-panel/70 px-4 py-5 text-sm text-slate-600 dark:text-slate-300">
      选择时间范围后点击开始总结，结果会按重点、决策、待办、风险和关键原话分区展示。
    </div>
  );
}

function SummaryError({ message }: { message: string }) {
  return (
    <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function SummaryReportView({ response }: { response: SummaryResponse }) {
  const summary = response.summary;
  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1.6fr)_minmax(180px,0.4fr)_minmax(180px,0.4fr)]">
        <div className="rounded-md border border-border bg-panel p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Summary</p>
              <h3 className="mt-1 text-lg font-semibold text-foreground">{summary.title}</h3>
            </div>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary-soft px-2.5 py-1 text-xs font-semibold text-primary">
              <Icon name="message" className="h-3.5 w-3.5" />
              {formatNumber(response.messages.included)}
            </span>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-700 dark:text-slate-200">{summary.executive_summary}</p>
        </div>
        <SummaryStat icon="calendar" label="时间范围" value={summary.time_range} />
        <SummaryStat icon="activity" label="状态判断" value={summary.sentiment} />
      </div>

      <div className="grid gap-4 2xl:grid-cols-2">
        <SummarySection title="核心重点" icon="target" empty="没有提炼出核心重点。" count={summary.key_points.length}>
          {summary.key_points.map((item, index) => (
            <div key={`${item.point}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-semibold leading-6 text-foreground">{item.point}</p>
                <SummaryTag value={item.importance} tone="blue" />
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.evidence}</p>
            </div>
          ))}
        </SummarySection>

        <SummarySection title="决策" icon="check" empty="没有明确决策。" count={summary.decisions.length}>
          {summary.decisions.map((item, index) => (
            <div key={`${item.decision}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <p className="text-sm font-semibold leading-6 text-foreground">{item.decision}</p>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.evidence}</p>
            </div>
          ))}
        </SummarySection>
      </div>

      <SummarySection title="待办事项" icon="list" empty="没有明确待办。" count={summary.action_items.length}>
        <div className="grid gap-3 lg:grid-cols-2">
          {summary.action_items.map((item, index) => (
            <div key={`${item.task}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <div className="flex flex-wrap items-center gap-2">
                <SummaryTag value={item.priority} tone="amber" />
                <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400">{item.due_time || "未明确"}</span>
              </div>
              <p className="mt-2 text-sm font-semibold leading-6 text-foreground">{item.task}</p>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">负责人：{item.owner || "未明确"}</p>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.context}</p>
            </div>
          ))}
        </div>
      </SummarySection>

      <div className="grid gap-4 2xl:grid-cols-2">
        <SummarySection title="风险与阻塞" icon="shield" empty="没有发现明显风险。" count={summary.risks.length}>
          {summary.risks.map((item, index) => (
            <div key={`${item.risk}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-semibold leading-6 text-foreground">{item.risk}</p>
                <SummaryTag value={item.severity} tone="red" />
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.evidence}</p>
            </div>
          ))}
        </SummarySection>

        <SummarySection title="待确认问题" icon="help" empty="没有待确认问题。" count={summary.open_questions.length}>
          {summary.open_questions.map((item, index) => (
            <div key={`${item.question}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <p className="text-sm font-semibold leading-6 text-foreground">{item.question}</p>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.context}</p>
            </div>
          ))}
        </SummarySection>
      </div>

      <SummarySection title="关键原话" icon="quote" empty="没有可引用的关键原话。" count={summary.notable_messages.length}>
        <div className="grid gap-3 xl:grid-cols-2">
          {summary.notable_messages.map((item, index) => (
            <blockquote key={`${item.quote}-${index}`} className="rounded-md border border-border bg-panel p-3">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                <span className="font-semibold text-primary">{item.sender}</span>
                <span className="font-mono">{item.time}</span>
              </div>
              <p className="mt-2 text-sm leading-6 text-foreground">“{item.quote}”</p>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{item.reason}</p>
            </blockquote>
          ))}
        </div>
      </SummarySection>
    </div>
  );
}

function SummaryStat({ icon, label, value }: { icon: IconName; label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-panel p-4">
      <div className="flex items-center gap-2 text-primary">
        <Icon name={icon} />
        <span className="text-xs font-semibold uppercase tracking-[0.16em]">{label}</span>
      </div>
      <p className="mt-3 text-sm font-semibold leading-6 text-foreground">{value || "-"}</p>
    </div>
  );
}

function SummarySection({
  title,
  icon,
  empty,
  count,
  children,
}: {
  title: string;
  icon: IconName;
  empty: string;
  count?: number;
  children: ReactNode;
}) {
  const hasChildren = count === undefined ? Boolean(children) && !(Array.isArray(children) && children.length === 0) : count > 0;
  return (
    <section>
      <div className="mb-2 flex items-center gap-2 text-primary">
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary-soft">
          <Icon name={icon} className="h-3.5 w-3.5" />
        </span>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      {hasChildren ? <div className="space-y-3">{children}</div> : <EmptySummarySection message={empty} />}
    </section>
  );
}

function EmptySummarySection({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-panel/70 px-3 py-3 text-sm text-slate-600 dark:text-slate-300">
      {message}
    </div>
  );
}

function SummaryTag({ value, tone }: { value: string; tone: "blue" | "amber" | "red" }) {
  const toneClasses = {
    blue: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-200",
    amber: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    red: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-200",
  };
  return (
    <span className={`inline-flex shrink-0 items-center rounded-md px-2 py-1 text-[11px] font-semibold ${toneClasses[tone]}`}>
      {value || "未标注"}
    </span>
  );
}

function MessageThread({ messages }: { messages: Message[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const orderedMessages = useMemo(
    () =>
      [...messages].sort((left, right) => {
        const byTime = (left.create_time ?? 0) - (right.create_time ?? 0);
        return byTime || left.local_id - right.local_id;
      }),
    [messages],
  );
  const lastMessageKey = orderedMessages.length
    ? `${orderedMessages[orderedMessages.length - 1].chat_table}-${orderedMessages[orderedMessages.length - 1].local_id}`
    : "";

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lastMessageKey]);

  if (!orderedMessages.length) {
    return (
      <div className="flex h-[640px] items-center justify-center bg-[#edf1f5] px-4 text-center text-sm text-slate-600 dark:bg-slate-950 dark:text-slate-300">
        选择会话或输入关键词后查看聊天记录
      </div>
    );
  }

  return (
    <div className="scrollbar-subtle h-[640px] overflow-y-auto bg-[#edf1f5] px-4 py-4 dark:bg-slate-950 sm:px-6">
      <div className="mx-auto flex max-w-4xl flex-col gap-3">
        {orderedMessages.map((message, index) => {
          const previous = orderedMessages[index - 1];
          const showTime = !previous || shouldShowTimeDivider(previous.create_time, message.create_time);
          return (
            <div key={`${message.chat_table}-${message.local_id}`} className="space-y-3">
              {showTime ? <TimeDivider value={message.create_time} /> : null}
              <MessageBubble message={message} />
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function TimeDivider({ value }: { value?: number }) {
  return (
    <div className="flex justify-center">
      <span className="rounded-md bg-slate-300/80 px-2 py-1 text-[11px] font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
        {formatMessageTime(value)}
      </span>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const self = Boolean(message.is_self);
  const sender = self ? "我" : formatSender(message);
  const isImage = message.media?.kind === "image";
  return (
    <article className={`flex w-full items-start gap-2 ${self ? "justify-end" : "justify-start"}`}>
      {!self ? <Avatar name={sender} /> : null}
      <div className={`flex max-w-[78%] flex-col ${self ? "items-end" : "items-start"}`}>
        {!self ? (
          <div className="mb-1 max-w-full truncate px-1 text-xs font-medium text-slate-500 dark:text-slate-400">
            {sender}
          </div>
        ) : null}
        <div
          className={
            isImage
              ? "overflow-hidden rounded-lg border border-black/5 bg-white p-1 shadow-sm dark:border-slate-700 dark:bg-slate-900"
              : `rounded-lg px-3 py-2 text-sm leading-6 shadow-sm ${
                  self
                    ? "bg-[#95ec69] text-slate-950"
                    : "border border-slate-200 bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                }`
          }
          title={`${formatSender(message)} · ${message.local_type}`}
        >
          {isImage && message.media ? (
            <ImageMessage media={message.media} />
          ) : (
            <p className="whitespace-pre-wrap break-words">{message.message_content ?? "内容为空"}</p>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2 px-1 font-mono text-[10px] text-slate-500 dark:text-slate-500">
          <span>{formatTime(message.create_time)}</span>
          <span>{message.local_type}</span>
        </div>
      </div>
      {self ? <Avatar name={sender} self /> : null}
    </article>
  );
}

function ImageMessage({ media }: { media: NonNullable<Message["media"]> }) {
  const width = clampImageSize(media.width ?? undefined, 120, 260);
  const height = clampImageSize(media.height ?? undefined, 90, 320);
  const style = media.width && media.height ? { width, aspectRatio: `${media.width} / ${media.height}` } : { width, height };

  if (!media.available || !media.url) {
    return (
      <div
        className="flex min-h-28 items-center justify-center rounded-md bg-slate-100 px-4 text-center text-xs leading-5 text-slate-500 dark:bg-slate-800 dark:text-slate-300"
        style={style}
      >
        {media.requires_image_key ? "本地图片需要 image key" : "图片文件未找到"}
      </div>
    );
  }

  return (
    <Image
      src={media.url}
      alt="聊天图片"
      className="block max-h-80 max-w-[260px] rounded-md object-contain"
      width={Math.round(width)}
      height={Math.round(height)}
      loading="lazy"
      style={style}
      unoptimized
    />
  );
}

function Avatar({ name, self = false }: { name: string; self?: boolean }) {
  return (
    <span
      className={`mt-5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-xs font-semibold ${
        self ? "bg-green-200 text-green-900" : "bg-white text-slate-700 shadow-sm dark:bg-slate-800 dark:text-slate-200"
      }`}
      title={name}
    >
      {avatarText(name)}
    </span>
  );
}

function StatusPanel({ health }: { health: Health | null }) {
  const ready = health?.available.messages && health.available.contacts && health.available.sessions;
  return (
    <section className="rounded-lg border border-border bg-panel">
      <PanelHeader title="API 状态" subtitle={ready ? "本地只读 API 已连接" : "等待后端服务"} icon="server" />
      <div className="space-y-2 p-4">
        {["messages", "contacts", "sessions"].map((key) => {
          const ok = Boolean(health?.available[key as keyof Health["available"]]);
          return (
            <div key={key} className="flex items-center justify-between rounded-md bg-panel-muted px-3 py-2">
              <span className="text-sm capitalize text-slate-700 dark:text-slate-200">{key}</span>
              <span className={`inline-flex items-center gap-1 text-xs font-semibold ${ok ? "text-success" : "text-danger"}`}>
                <Icon name={ok ? "check" : "alert"} />
                {ok ? "ready" : "offline"}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function RecentSessions({ sessions }: { sessions: Session[] }) {
  return (
    <section className="rounded-lg border border-border bg-panel">
      <PanelHeader title="最近会话" subtitle={`${sessions.length} 条摘要`} icon="filter" />
      <div className="scrollbar-subtle max-h-72 overflow-auto">
        {sessions.slice(0, 8).map((session) => (
          <div key={session.username} className="border-b border-border px-4 py-3 last:border-b-0">
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-sm font-semibold text-foreground">{session.display_name}</p>
              <span className="font-mono text-xs text-slate-500">{formatNumber(session.unread_count)}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{session.summary || "无摘要"}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function ContactsPanel({ contacts }: { contacts: Contact[] }) {
  return (
    <section className="rounded-lg border border-border bg-panel">
      <PanelHeader title="联系人映射" subtitle={`${contacts.length} 个样本`} icon="users" />
      <div className="scrollbar-subtle max-h-72 overflow-auto">
        {contacts.slice(0, 10).map((contact) => (
          <div key={contact.username} className="border-b border-border px-4 py-3 last:border-b-0">
            <p className="truncate text-sm font-semibold text-foreground">{contact.display_name}</p>
            <p className="mt-1 truncate font-mono text-[11px] text-slate-500 dark:text-slate-400">{contact.username}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
      <Icon name="alert" className="mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <p className="text-sm font-semibold">本地 API 暂不可用</p>
        <p className="mt-1 text-sm leading-6">{message}</p>
        <code className="scrollbar-subtle mt-2 block overflow-x-auto rounded-md bg-white/80 px-3 py-2 font-mono text-xs text-slate-900 dark:bg-slate-900 dark:text-slate-100">
          wechat-agent serve --decrypted-dir .wechat-agent\work\20260510-000628\decrypted
        </code>
      </div>
    </div>
  );
}

function clampImageSize(value: number | undefined, min: number, max: number) {
  if (!value || Number.isNaN(value)) return max;
  return Math.min(Math.max(value, min), max);
}

function formatSender(message: Message) {
  return message.sender_display_name || message.sender_username || String(message.real_sender_id);
}

function avatarText(name: string) {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  const letters = Array.from(trimmed.replace(/^wxid_/i, ""));
  return letters.slice(0, 2).join("").toUpperCase();
}

function shouldShowTimeDivider(previous?: number, current?: number) {
  if (!previous || !current) return true;
  return Math.abs(current - previous) >= 300;
}

function formatNumber(value?: number) {
  return typeof value === "number" ? value.toLocaleString() : "-";
}

function timestampFromInput(value: string) {
  if (!value) return undefined;
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? undefined : Math.floor(timestamp / 1000);
}

function inputValueFromTimestamp(value?: number) {
  const date = new Date((value ?? Math.floor(Date.now() / 1000)) * 1000);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function formatTime(value?: number) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function formatMessageTime(value?: number) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}
