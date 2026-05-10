"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Chat, Contact, getWechat, Health, ListResponse, Message, Overview, Session } from "../lib/wechat-api";
import { Icon } from "./icons";

type LoadState = "idle" | "loading" | "ready" | "error";

const messageTypeOptions = [
  { label: "全部类型", value: "" },
  { label: "文本", value: "1" },
  { label: "图片/媒体", value: "3" },
  { label: "链接/卡片", value: "49" },
  { label: "系统", value: "10000" },
];

export function DashboardApp() {
  const [health, setHealth] = useState<Health | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedChat, setSelectedChat] = useState<string>("");
  const [query, setQuery] = useState("");
  const [messageType, setMessageType] = useState("");
  const [status, setStatus] = useState<LoadState>("idle");
  const [error, setError] = useState("");

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
        limit: 80,
      });
      setMessages(data.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    }
  }, [messageType, query, selectedChat]);

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

        <section className="grid min-h-[680px] gap-4 xl:grid-cols-[360px_minmax(0,1fr)_360px]">
          <aside className="min-h-0 overflow-hidden rounded-lg border border-border bg-panel">
            <PanelHeader
              title="会话雷达"
              subtitle={`${filteredChats.length.toLocaleString()} 个可查询聊天表`}
              icon="filter"
            />
            <div className="border-b border-border p-3">
              <SearchBox value={query} onChange={setQuery} onSubmit={() => void loadMessages()} />
            </div>
            <div className="max-h-[560px] overflow-y-auto">
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
            <div className="flex flex-col gap-3 border-b border-border p-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Message Query</p>
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

            <MessageTable messages={messages} />
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

function PanelHeader({ title, subtitle, icon }: { title: string; subtitle: string; icon: "filter" | "server" | "users" }) {
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

function MessageTable({ messages }: { messages: Message[] }) {
  return (
    <div className="max-h-[620px] overflow-auto">
      <table className="w-full min-w-[760px] border-separate border-spacing-0 text-sm">
        <thead className="sticky top-0 z-10 bg-panel-muted text-left text-xs uppercase tracking-[0.14em] text-slate-600 dark:text-slate-300">
          <tr>
            <th className="border-b border-border px-4 py-3 font-semibold">时间</th>
            <th className="border-b border-border px-4 py-3 font-semibold">类型</th>
            <th className="border-b border-border px-4 py-3 font-semibold">发送者</th>
            <th className="border-b border-border px-4 py-3 font-semibold">内容</th>
          </tr>
        </thead>
        <tbody>
          {messages.length ? (
            messages.map((message) => (
              <tr key={`${message.chat_table}-${message.local_id}`} className="transition-colors hover:bg-panel-muted">
                <td className="border-b border-border px-4 py-3 font-mono text-xs text-slate-600 dark:text-slate-300">
                  {formatTime(message.create_time)}
                </td>
                <td className="border-b border-border px-4 py-3">
                  <span className="inline-flex rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                    {message.local_type}
                  </span>
                </td>
                <td className="border-b border-border px-4 py-3 font-mono text-xs text-slate-600 dark:text-slate-300">
                  {message.real_sender_id}
                </td>
                <td className="border-b border-border px-4 py-3 text-slate-800 dark:text-slate-100">
                  <p className="line-clamp-3 break-words leading-6">{message.message_content ?? "内容已隐藏或为空"}</p>
                </td>
              </tr>
            ))
          ) : (
            <tr>
              <td className="px-4 py-12 text-center text-sm text-slate-600 dark:text-slate-300" colSpan={4}>
                选择会话或输入关键词后查询消息。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
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
      <div className="max-h-72 overflow-auto">
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
      <div className="max-h-72 overflow-auto">
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
        <code className="mt-2 block overflow-x-auto rounded-md bg-white/80 px-3 py-2 font-mono text-xs text-slate-900 dark:bg-slate-900 dark:text-slate-100">
          wechat-agent serve --decrypted-dir .wechat-agent\work\20260510-000628\decrypted
        </code>
      </div>
    </div>
  );
}

function formatNumber(value?: number) {
  return typeof value === "number" ? value.toLocaleString() : "—";
}

function formatTime(value?: number) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}
