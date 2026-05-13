"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { SUMMARY_STORAGE_KEY, type StoredSummaryReport } from "../lib/summary-storage";
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

    const reportId = createReportId();
    const chatName = chats.find((chat) => chat.username === selectedChat)?.display_name ?? selectedChat;
    writeStoredSummaryReport({
      id: reportId,
      status: "loading",
      createdAt: Date.now(),
      chatName,
      range: {
        start: summaryStart,
        end: summaryEnd,
      },
    });
    window.open(`/summary?id=${encodeURIComponent(reportId)}`, "_blank", "noopener,noreferrer");

    setSummaryStatus("loading");
    setSummaryError("");
    try {
      const result = await postWechat<SummaryResponse>("summary", {
        chat: selectedChat,
        after,
        before,
      });
      writeStoredSummaryReport({
        id: reportId,
        status: "ready",
        createdAt: Date.now(),
        chatName,
        range: {
          start: summaryStart,
          end: summaryEnd,
        },
        response: result,
      });
      setSummaryStatus("ready");
    } catch (summaryLoadError) {
      const message = summaryLoadError instanceof Error ? summaryLoadError.message : String(summaryLoadError);
      writeStoredSummaryReport({
        id: reportId,
        status: "error",
        createdAt: Date.now(),
        chatName,
        range: {
          start: summaryStart,
          end: summaryEnd,
        },
        error: message,
      });
      setSummaryStatus("error");
      setSummaryError(message);
    }
  }, [chats, selectedChat, summaryEnd, summaryStart]);

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

  const ready = Boolean(health?.available.messages && health.available.contacts && health.available.sessions);

  return (
    <main className="min-h-screen overflow-x-hidden bg-background text-foreground">
      <div className="mx-auto flex min-h-screen w-full max-w-[1200px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <Header
          chats={overview?.chat_count}
          messages={overview?.message_count}
          ready={ready}
          status={status}
          onRefresh={loadShell}
        />

        {error ? <ErrorBanner message={error} /> : null}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="核心指标">
          <Metric label="会话资产" value={overview?.chat_count} icon="message" tone="gold" trend="+12.8%" />
          <Metric label="消息流量" value={overview?.message_count} icon="activity" tone="violet" trend="+24h" />
          <Metric label="联系人节点" value={overview?.contact_count} icon="users" tone="emerald" trend="+7.4%" />
          <Metric label="活跃会话" value={overview?.session_count} icon="database" tone="blue" trend="LIVE" />
        </section>

        <section className="grid min-h-[740px] gap-5 xl:grid-cols-[330px_minmax(0,1fr)]" aria-label="会话工作台">
          <ConversationList
            chats={filteredChats}
            query={query}
            selectedChat={selectedChat}
            onQueryChange={setQuery}
            onSearch={() => void loadMessages()}
            onSelect={setSelectedChat}
          />

          <section className="min-w-0 overflow-hidden rounded-xl border border-white/15 bg-slate-950/55 shadow-2xl shadow-black/25 backdrop-blur-xl">
            <ConversationToolbar
              chat={selectedChatItem}
              messageType={messageType}
              onMessageTypeChange={setMessageType}
              onSearch={() => void loadMessages()}
              disabled={!selectedChat && !query}
            />

            <SummaryWorkspace
              chatName={selectedChatItem?.display_name ?? "未选择会话"}
              disabled={!selectedChat || summaryStatus === "loading"}
              endValue={summaryEnd}
              error={summaryError}
              startValue={summaryStart}
              status={summaryStatus}
              onEndChange={setSummaryEnd}
              onStartChange={setSummaryStart}
              onSubmit={summarizeMessages}
            />

            <MessageThread messages={messages} />
          </section>
        </section>

        <section className="grid gap-5 lg:grid-cols-3" aria-label="系统状态和样本">
          <StatusPanel health={health} />
          <RecentSessions sessions={sessions} />
          <ContactsPanel contacts={contacts} />
        </section>
      </div>
    </main>
  );
}

function Header({
  chats,
  messages,
  ready,
  status,
  onRefresh,
}: {
  chats?: number;
  messages?: number;
  ready: boolean;
  status: LoadState;
  onRefresh: () => void;
}) {
  return (
    <header className="relative overflow-hidden rounded-xl border border-white/15 bg-white/[0.07] px-4 py-4 shadow-2xl shadow-black/20 backdrop-blur-xl sm:px-5">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-300/80 to-transparent" />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-300/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-amber-200">
              <Icon name="shield" className="h-3.5 w-3.5" />
              Local Intel
            </span>
            <StatusPill ready={ready} status={status} />
          </div>
          <h1 className="mt-3 font-heading text-2xl font-semibold text-white sm:text-3xl">
            WeChat Alpha Desk
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            本地解密消息的资产视图、AI 摘要与会话检索。数据只读，所有查询经由本机 API。
          </p>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row lg:items-end">
          <div className="grid grid-cols-2 gap-2 text-xs text-slate-300 sm:w-56">
            <HeaderStat label="CHATS" value={formatNumber(chats)} />
            <HeaderStat label="FLOW" value={formatCompact(messages)} />
          </div>
          <button
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-cta px-4 text-sm font-semibold text-white shadow-lg shadow-violet-950/40 transition-all duration-200 ease-out hover:bg-violet-500 focus:outline-none focus:ring-2 focus:ring-cta focus:ring-offset-2 focus:ring-offset-background disabled:opacity-60"
            onClick={onRefresh}
            type="button"
            disabled={status === "loading"}
          >
            <Icon name={status === "loading" ? "activity" : "refresh"} className={status === "loading" ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {status === "loading" ? "同步中" : "刷新数据"}
          </button>
        </div>
      </div>
    </header>
  );
}

function HeaderStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-slate-950/45 px-3 py-2">
      <p className="font-heading text-[10px] tracking-[0.18em] text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-amber-200">{value}</p>
    </div>
  );
}

function StatusPill({ ready, status }: { ready: boolean; status: LoadState }) {
  const label = status === "loading" ? "Syncing" : ready ? "Secure Ready" : "API Standby";
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${
      ready
        ? "border-emerald-300/30 bg-emerald-300/10 text-emerald-200"
        : "border-white/15 bg-white/5 text-slate-300"
    }`}>
      <span className={`h-2 w-2 rounded-full ${ready ? "bg-emerald-300" : "bg-amber-300"}`} />
      {label}
    </span>
  );
}

function Metric({
  label,
  value,
  icon,
  tone,
  trend,
}: {
  label: string;
  value?: number;
  icon: "message" | "activity" | "users" | "database";
  tone: "gold" | "violet" | "emerald" | "blue";
  trend: string;
}) {
  const toneClasses = {
    gold: "from-amber-300/20 text-amber-200",
    violet: "from-violet-400/20 text-violet-200",
    emerald: "from-emerald-300/20 text-emerald-200",
    blue: "from-sky-300/20 text-sky-200",
  };
  return (
    <article className="group relative overflow-hidden rounded-xl border border-white/15 bg-white/[0.06] p-4 shadow-xl shadow-black/15 backdrop-blur-xl transition-all duration-200 ease-out hover:border-amber-300/45 hover:bg-white/[0.09]">
      <div className={`absolute inset-x-0 top-0 h-20 bg-gradient-to-b ${toneClasses[tone].split(" ")[0]} to-transparent`} />
      <div className="relative flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{label}</p>
          <p className="mt-3 font-heading text-3xl font-semibold text-white">{formatNumber(value)}</p>
        </div>
        <span className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/15 bg-slate-950/50 ${toneClasses[tone].split(" ").slice(1).join(" ")}`}>
          <Icon name={icon} />
        </span>
      </div>
      <div className="relative mt-4 flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-200">
          <Icon name="trendUp" className="h-3.5 w-3.5" />
          {trend}
        </span>
        <MiniLineChart tone={tone} />
      </div>
    </article>
  );
}

function MiniLineChart({ tone }: { tone: "gold" | "violet" | "emerald" | "blue" }) {
  const color = {
    gold: "#F59E0B",
    violet: "#8B5CF6",
    emerald: "#34D399",
    blue: "#38BDF8",
  }[tone];
  return (
    <svg aria-hidden="true" className="h-9 w-24" viewBox="0 0 96 36">
      <path d="M2 30C14 28 16 10 28 14C42 19 44 31 58 21C70 12 75 6 94 9" fill="none" stroke={color} strokeLinecap="round" strokeWidth="2.4" />
      <path d="M2 30C14 28 16 10 28 14C42 19 44 31 58 21C70 12 75 6 94 9V36H2Z" fill={color} opacity="0.14" />
    </svg>
  );
}

function ConversationList({
  chats,
  query,
  selectedChat,
  onQueryChange,
  onSearch,
  onSelect,
}: {
  chats: Chat[];
  query: string;
  selectedChat: string;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onSelect: (username: string) => void;
}) {
  return (
    <aside className="min-h-0 overflow-hidden rounded-xl border border-white/15 bg-white/[0.06] shadow-xl shadow-black/15 backdrop-blur-xl">
      <PanelHeader
        eyebrow="Watchlist"
        title="会话雷达"
        subtitle={`${chats.length.toLocaleString()} 个可查询聊天`}
        icon="filter"
      />
      <div className="border-b border-white/10 p-3">
        <SearchBox value={query} onChange={onQueryChange} onSubmit={onSearch} />
      </div>
      <div className="scrollbar-subtle max-h-[615px] overflow-y-auto">
        {chats.slice(0, 120).map((chat) => (
          <ChatRow
            key={chat.username}
            chat={chat}
            selected={chat.username === selectedChat}
            onClick={() => onSelect(chat.username)}
          />
        ))}
        {!chats.length ? <EmptyState icon="search" title="没有匹配会话" text="调整关键词后重新查询。" /> : null}
      </div>
    </aside>
  );
}

function ConversationToolbar({
  chat,
  disabled,
  messageType,
  onMessageTypeChange,
  onSearch,
}: {
  chat?: Chat;
  disabled: boolean;
  messageType: string;
  onMessageTypeChange: (value: string) => void;
  onSearch: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 border-b border-white/10 bg-white/[0.04] px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
      <div className="min-w-0">
        <p className="font-heading text-[11px] font-semibold uppercase tracking-[0.2em] text-amber-200">
          Conversation Terminal
        </p>
        <h2 className="mt-1 truncate text-xl font-semibold text-white">
          {chat?.display_name ?? "选择一个会话"}
        </h2>
        <p className="mt-1 truncate font-mono text-xs text-slate-400">
          {chat?.username ?? "启动 Python API 后可读取本地解密消息库"}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="message-type">消息类型</label>
        <select
          id="message-type"
          className="h-10 rounded-lg border border-white/15 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none transition-colors duration-200 focus:border-primary focus:ring-2 focus:ring-primary/30"
          value={messageType}
          onChange={(event) => onMessageTypeChange(event.target.value)}
        >
          {messageTypeOptions.map((option) => (
            <option key={option.label} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-3 text-sm font-semibold text-slate-950 shadow-lg shadow-amber-950/30 transition-colors duration-200 hover:bg-secondary focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
          disabled={disabled}
          onClick={onSearch}
          type="button"
        >
          <Icon name="search" />
          查询
        </button>
      </div>
    </div>
  );
}

function PanelHeader({
  eyebrow,
  title,
  subtitle,
  icon,
}: {
  eyebrow?: string;
  title: string;
  subtitle: string;
  icon: IconName;
}) {
  return (
    <div className="flex items-center justify-between border-b border-white/10 px-4 py-4">
      <div className="min-w-0">
        {eyebrow ? <p className="font-heading text-[10px] uppercase tracking-[0.2em] text-amber-200">{eyebrow}</p> : null}
        <h2 className="mt-1 text-sm font-semibold text-white">{title}</h2>
        <p className="mt-1 truncate text-xs text-slate-400">{subtitle}</p>
      </div>
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-300/25 bg-amber-300/10 text-amber-200">
        <Icon name={icon} />
      </span>
    </div>
  );
}

function SearchBox({
  value,
  onChange,
  onSubmit,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="flex gap-2">
      <label className="sr-only" htmlFor="chat-search">关键词</label>
      <div className="relative min-w-0 flex-1">
        <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input
          id="chat-search"
          className="h-10 w-full rounded-lg border border-white/15 bg-slate-950/60 pl-9 pr-3 text-sm text-slate-100 outline-none transition-colors duration-200 placeholder:text-slate-500 focus:border-primary focus:ring-2 focus:ring-primary/30"
          placeholder="搜索会话或消息关键词"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSubmit();
          }}
        />
      </div>
      <button
        className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/15 bg-white/[0.06] text-slate-200 transition-colors duration-200 hover:border-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary"
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
      className={`grid w-full grid-cols-[1fr_auto] gap-3 border-b border-white/10 px-4 py-3 text-left transition-colors duration-200 hover:bg-white/[0.07] focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary ${
        selected ? "bg-amber-300/10 text-white" : "bg-transparent text-slate-200"
      }`}
      onClick={onClick}
      type="button"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold">{chat.display_name}</span>
        <span className="mt-1 block truncate font-mono text-[11px] text-slate-500">{chat.username}</span>
      </span>
      <span className="text-right">
        <span className="block font-mono text-sm font-semibold text-amber-200">{formatNumber(chat.message_count)}</span>
        <span className="mt-1 block text-[11px] text-slate-500">{formatTime(chat.latest_create_time)}</span>
      </span>
    </button>
  );
}

function SummaryWorkspace({
  chatName,
  disabled,
  endValue,
  error,
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
  startValue: string;
  status: SummaryState;
  onEndChange: (value: string) => void;
  onStartChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <section className="border-b border-white/10 bg-slate-950/30 px-4 py-4">
      <form
        className="flex flex-col gap-4 2xl:flex-row 2xl:items-end 2xl:justify-between"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-violet-300/30 bg-violet-400/10 text-violet-200">
              <Icon name="sparkles" />
            </span>
            <div className="min-w-0">
              <p className="font-heading text-[11px] font-semibold uppercase tracking-[0.2em] text-violet-200">AI Signal Brief</p>
              <h3 className="mt-1 truncate text-sm font-semibold text-white">为 {chatName} 生成消息摘要</h3>
            </div>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] 2xl:min-w-[620px]">
          <DateTimeField id="summary-start" label="开始时间" value={startValue} onChange={onStartChange} />
          <DateTimeField id="summary-end" label="结束时间" value={endValue} onChange={onEndChange} />
          <button
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-cta px-4 text-sm font-semibold text-white shadow-lg shadow-violet-950/35 transition-colors duration-200 hover:bg-violet-500 focus:outline-none focus:ring-2 focus:ring-cta focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled}
            type="submit"
          >
            <Icon name={status === "loading" ? "activity" : "sparkles"} className={status === "loading" ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {status === "loading" ? "总结中" : "生成摘要"}
          </button>
        </div>
      </form>

      {error ? <SummaryError message={error} /> : null}
      <SummaryPlaceholder status={status} />
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
      <span className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-400">
        <Icon name="clock" className="h-3.5 w-3.5" />
        {label}
      </span>
      <input
        id={id}
        className="h-10 w-full rounded-lg border border-white/15 bg-slate-950/60 px-3 text-sm text-slate-100 outline-none transition-colors duration-200 focus:border-primary focus:ring-2 focus:ring-primary/30"
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
          <div key={item} className="h-24 animate-pulse rounded-lg border border-white/10 bg-white/[0.06]" />
        ))}
      </div>
    );
  }
  if (status === "ready") {
    return (
      <div className="mt-4 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-5 text-sm leading-6 text-emerald-100">
        摘要已生成，结果已发送到新打开的摘要报告页。
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-lg border border-dashed border-white/15 bg-white/[0.04] px-4 py-5 text-sm leading-6 text-slate-400">
      选择时间窗口后生成摘要，结果会在独立报告页按重点、决策、待办、风险和关键原话展示。
    </div>
  );
}

function SummaryError({ message }: { message: string }) {
  return (
    <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-sm text-amber-100">
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
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
      <div className="flex h-[620px] items-center justify-center bg-slate-950/65 px-4 text-center">
        <EmptyState icon="message" title="消息终端待机" text="选择会话或输入关键词后查看聊天记录。" />
      </div>
    );
  }

  return (
    <div className="scrollbar-subtle h-[620px] overflow-y-auto bg-slate-950/65 px-4 py-4 sm:px-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
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
      <span className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1 font-mono text-[11px] font-medium text-slate-400">
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
      <div className={`flex max-w-[80%] flex-col ${self ? "items-end" : "items-start"}`}>
        {!self ? (
          <div className="mb-1 max-w-full truncate px-1 text-xs font-medium text-slate-500">
            {sender}
          </div>
        ) : null}
        <div
          className={
            isImage
              ? "overflow-hidden rounded-xl border border-white/10 bg-white/[0.06] p-1 shadow-lg shadow-black/20"
              : `rounded-xl px-3 py-2 text-sm leading-6 shadow-lg shadow-black/15 ${
                  self
                    ? "bg-primary text-slate-950"
                    : "border border-white/10 bg-white/[0.08] text-slate-100"
                }`
          }
          title={`${formatSender(message)} / ${message.local_type}`}
        >
          {isImage && message.media ? (
            <ImageMessage media={message.media} />
          ) : (
            <p className="whitespace-pre-wrap break-words">{message.message_content ?? "内容为空"}</p>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2 px-1 font-mono text-[10px] text-slate-600">
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
        className="flex min-h-28 items-center justify-center rounded-lg bg-slate-900 px-4 text-center text-xs leading-5 text-slate-400"
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
      className="block max-h-80 max-w-[260px] rounded-lg object-contain"
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
      className={`mt-5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border text-xs font-semibold ${
        self
          ? "border-amber-300/30 bg-amber-300/15 text-amber-100"
          : "border-white/10 bg-white/[0.06] text-slate-200"
      }`}
      title={name}
    >
      {avatarText(name)}
    </span>
  );
}

function StatusPanel({ health }: { health: Health | null }) {
  const services: Array<{ key: keyof Health["available"]; label: string }> = [
    { key: "messages", label: "Messages" },
    { key: "contacts", label: "Contacts" },
    { key: "sessions", label: "Sessions" },
    { key: "media", label: "Media" },
    { key: "image_key", label: "Image Key" },
  ];

  return (
    <section className="overflow-hidden rounded-xl border border-white/15 bg-white/[0.06] shadow-xl shadow-black/15 backdrop-blur-xl">
      <PanelHeader
        eyebrow="Security"
        title="API 状态"
        subtitle={health ? "本地只读服务状态" : "等待后端服务"}
        icon="server"
      />
      <div className="space-y-2 p-4">
        {services.map((service) => {
          const ok = Boolean(health?.available[service.key]);
          return (
            <div key={service.key} className="flex items-center justify-between rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2">
              <span className="text-sm text-slate-300">{service.label}</span>
              <span className={`inline-flex items-center gap-1 text-xs font-semibold ${ok ? "text-emerald-200" : "text-rose-200"}`}>
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
    <section className="overflow-hidden rounded-xl border border-white/15 bg-white/[0.06] shadow-xl shadow-black/15 backdrop-blur-xl">
      <PanelHeader eyebrow="Momentum" title="最近会话" subtitle={`${sessions.length} 条样本`} icon="trendUp" />
      <div className="scrollbar-subtle max-h-80 overflow-auto">
        {sessions.slice(0, 8).map((session) => (
          <div key={session.username} className="border-b border-white/10 px-4 py-3 last:border-b-0">
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-sm font-semibold text-white">{session.display_name}</p>
              <span className="font-mono text-xs text-amber-200">{formatNumber(session.unread_count)}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{session.summary || "无摘要"}</p>
          </div>
        ))}
        {!sessions.length ? <EmptyState icon="list" title="暂无会话样本" text="同步数据后显示最近会话。" /> : null}
      </div>
    </section>
  );
}

function ContactsPanel({ contacts }: { contacts: Contact[] }) {
  return (
    <section className="overflow-hidden rounded-xl border border-white/15 bg-white/[0.06] shadow-xl shadow-black/15 backdrop-blur-xl">
      <PanelHeader eyebrow="Network" title="联系人映射" subtitle={`${contacts.length} 个样本`} icon="users" />
      <div className="scrollbar-subtle max-h-80 overflow-auto">
        {contacts.slice(0, 10).map((contact) => (
          <div key={contact.username} className="border-b border-white/10 px-4 py-3 last:border-b-0">
            <p className="truncate text-sm font-semibold text-white">{contact.display_name}</p>
            <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{contact.username}</p>
          </div>
        ))}
        {!contacts.length ? <EmptyState icon="users" title="暂无联系人样本" text="同步数据后显示联系人节点。" /> : null}
      </div>
    </section>
  );
}

function EmptyState({ icon, title, text }: { icon: IconName; title: string; text: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-8 text-center">
      <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-white/10 bg-white/[0.06] text-slate-400">
        <Icon name={icon} />
      </span>
      <p className="mt-3 text-sm font-semibold text-slate-200">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-500">{text}</p>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-300/35 bg-amber-300/10 px-4 py-3 text-amber-100 shadow-lg shadow-black/15 backdrop-blur-xl">
      <Icon name="alert" className="mt-0.5 h-5 w-5 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">本地 API 暂不可用</p>
        <p className="mt-1 text-sm leading-6">{message}</p>
        <code className="scrollbar-subtle mt-2 block overflow-x-auto rounded-lg border border-white/10 bg-slate-950/75 px-3 py-2 font-mono text-xs text-slate-200">
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

function formatCompact(value?: number) {
  if (typeof value !== "number") return "-";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function createReportId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function writeStoredSummaryReport(report: StoredSummaryReport) {
  window.localStorage.setItem(SUMMARY_STORAGE_KEY, JSON.stringify(report));
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
