import { DashboardApp } from "./ui/dashboard-app";
import type { DashboardInitialData } from "./ui/dashboard-app";
import { getServerWechat } from "./lib/wechat-server";
import type { Chat, Contact, Health, ListResponse, Message, Overview, Session } from "./lib/wechat-api";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Home() {
  const initialData = await loadDashboardInitialData();
  return <DashboardApp initialData={initialData} />;
}

async function loadDashboardInitialData(): Promise<DashboardInitialData> {
  const errors: string[] = [];
  const [healthResult, overviewResult, contactsResult, sessionsResult, chatsResult] = await Promise.allSettled([
    getServerWechat<Health>("health"),
    getServerWechat<Overview>("overview"),
    getServerWechat<ListResponse<Contact>>("contacts", { limit: 12 }),
    getServerWechat<ListResponse<Session>>("sessions", { limit: 12 }),
    getServerWechat<ListResponse<Chat>>("chats", { limit: 80 }),
  ]);

  const health = readResult(healthResult, "health", errors);
  const overview = readResult(overviewResult, "overview", errors);
  const contacts = readResult(contactsResult, "contacts", errors)?.items ?? [];
  const sessions = readResult(sessionsResult, "sessions", errors)?.items ?? [];
  const chats = readResult(chatsResult, "chats", errors)?.items ?? [];
  const selectedChat = chats[0]?.username ?? "";
  let messages: Message[] = [];

  if (selectedChat) {
    try {
      const messageData = await getServerWechat<ListResponse<Message>>("messages", {
        chat: selectedChat,
        limit: 120,
      });
      messages = messageData.items ?? [];
    } catch (error) {
      errors.push(`messages: ${formatError(error)}`);
    }
  }

  return {
    health,
    overview,
    contacts,
    sessions,
    chats,
    messages,
    selectedChat,
    error: errors.length ? errors.join(" | ") : undefined,
  };
}

function readResult<T>(result: PromiseSettledResult<T>, label: string, errors: string[]) {
  if (result.status === "fulfilled") return result.value;
  errors.push(`${label}: ${formatError(result.reason)}`);
  return null;
}

function formatError(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
