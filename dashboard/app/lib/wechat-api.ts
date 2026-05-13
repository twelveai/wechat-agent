export type ApiResult<T> = T & {
  ok: boolean;
  error?: string;
};

export type Overview = {
  chat_count: number;
  message_count: number;
  contact_count: number;
  session_count: number;
};

export type Contact = {
  id: number;
  username: string;
  alias?: string;
  remark?: string;
  nick_name?: string;
  display_name: string;
  local_type?: number;
  verify_flag?: number;
  is_in_chat_room?: number;
  chat_room_type?: number;
  is_room?: boolean;
};

export type Session = {
  username: string;
  type: number;
  unread_count: number;
  summary?: string;
  last_timestamp?: number;
  sort_timestamp?: number;
  last_msg_type?: number;
  last_msg_sender?: string;
  last_sender_display_name?: string;
  last_time_iso?: string;
  sort_time_iso?: string;
  display_name: string;
  contact?: Contact;
};

export type Chat = {
  username: string;
  table: string;
  display_name: string;
  message_count: number;
  latest_create_time?: number;
  latest_time_iso?: string;
  contact?: Contact;
  session?: Session;
};

export type Message = {
  chat: string;
  chat_table: string;
  chat_display_name: string;
  local_id: number;
  server_id: number;
  local_type: number;
  sort_seq: number;
  real_sender_id: number;
  create_time: number;
  create_time_iso?: string;
  status: number;
  sender_username?: string | null;
  sender_display_name?: string | null;
  sender_contact?: Contact | null;
  is_self?: boolean;
  message_kind?: "text" | "image" | string;
  media?: MessageMedia | null;
  message_content?: string | null;
};

export type MessageMedia = {
  kind: "image" | string;
  detail?: string;
  url?: string;
  width?: number | null;
  height?: number | null;
  size?: number | null;
  md5?: string | null;
  available?: boolean;
  source?: string | null;
  requires_image_key?: boolean;
  unavailable_reason?: string;
};

export type SummaryRange = {
  after?: number | null;
  before?: number | null;
  after_iso?: string | null;
  before_iso?: string | null;
};

export type SummaryReport = {
  title: string;
  executive_summary: string;
  message_count: number;
  time_range: string;
  sentiment: string;
  key_points: Array<{
    point: string;
    importance: string;
    evidence: string;
  }>;
  decisions: Array<{
    decision: string;
    evidence: string;
  }>;
  action_items: Array<{
    task: string;
    owner: string;
    due_time: string;
    priority: string;
    context: string;
  }>;
  risks: Array<{
    risk: string;
    severity: string;
    evidence: string;
  }>;
  open_questions: Array<{
    question: string;
    context: string;
  }>;
  notable_messages: Array<{
    time: string;
    sender: string;
    quote: string;
    reason: string;
  }>;
};

export type SummaryResponse = {
  chat: string;
  chat_display_name: string;
  range: SummaryRange;
  messages: {
    included: number;
    limit: number;
    oldest_create_time?: number | null;
    newest_create_time?: number | null;
  };
  summary: SummaryReport;
  openai?: {
    response_id?: string | null;
    model?: string | null;
  } | null;
};

export type ListResponse<T> = {
  total?: number;
  total_scanned_tables?: number;
  items: T[];
};

export type Health = {
  databases: Record<string, string | null>;
  available: {
    messages: boolean;
    contacts: boolean;
    sessions: boolean;
    media?: boolean;
    image_key?: boolean;
  };
};

export async function getWechat<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  });
  const url = `/api/wechat/${path}${query.size ? `?${query.toString()}` : ""}`;
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "application/json",
    },
  });
  const payload = (await response.json()) as ApiResult<T>;
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error ?? `Request failed: ${response.status}`);
  }
  return payload;
}

export async function postWechat<T>(path: string, body: Record<string, unknown>) {
  const response = await fetch(`/api/wechat/${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = (await response.json()) as ApiResult<T>;
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error ?? `Request failed: ${response.status}`);
  }
  return payload;
}
