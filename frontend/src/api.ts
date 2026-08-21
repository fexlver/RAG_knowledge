export type ThemeMode = "light" | "dark" | "system";

export interface Session {
  session_id: string;
  title: string;
  updated_at: string;
  model_profile_id: string | null;
}

export interface TraceEvent {
  event_id?: string;
  stage: string;
  status: "pending" | "running" | "completed" | "failed";
  label: string;
  detail: string;
  duration_ms: number | null;
  score?: number;
}

export interface RetrievalPlugin {
  plugin_id: string;
  label: string;
  description: string;
  category: "retriever" | "fusion" | "postprocessor";
}

export interface RetrievalConfig {
  retriever_ids: string[];
  fusion_id: string;
  rerank_enabled: boolean;
}

export interface RetrievalSettings {
  config: RetrievalConfig;
  retrievers: RetrievalPlugin[];
  fusion_strategies: RetrievalPlugin[];
  postprocessors: RetrievalPlugin[];
}

export interface Citation {
  label: number;
  doc_id: string;
  chunk_id: string;
  source: string;
  standard_code: string;
  page_number: number | null;
  section: string;
  excerpt: string;
  locator: Record<string, unknown>;
}

export interface TokenUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  session_total_tokens?: number | null;
}

export interface ChatMessage {
  id: string | number;
  role: "user" | "assistant";
  content: string;
  trace: TraceEvent[];
  citations: Citation[];
  model_profile_id: string | null;
  model_name?: string;
  usage: TokenUsage;
  refused: boolean;
  created_at?: string;
  streaming?: boolean;
  error?: string;
}

export interface Provider {
  provider_id: string;
  name: string;
  provider_type: "dashscope" | "openai_compatible";
  base_url: string;
  enabled: boolean;
  has_api_key: boolean;
}

export interface ModelProfile {
  profile_id: string;
  provider_id: string;
  provider_name: string;
  provider_type: string;
  model_id: string;
  display_name: string;
  enabled: boolean | number;
}

export interface DocumentRecord {
  doc_id: string;
  content_hash: string;
  file_name: string;
  source: string;
  standard_code: string;
  document_type: string;
  validity_status: string;
  created_at: string;
  mime_type: string | null;
  storage_path: string | null;
  series_id: string;
  version_number: number;
  version_count: number;
  is_current: boolean | number;
  file_size: number;
  chunk_count: number;
}

export interface OperationLog {
  id: number;
  action: string;
  target: string;
  detail: string;
  created_at: string;
}

export interface PreviewData {
  doc_id: string;
  chunk_id: string;
  file_name: string;
  mime_type: string | null;
  file_url: string;
  excerpt: string;
  locator: {
    kind?: "pdf" | "text";
    page_number?: number | null;
    anchor_text?: string;
    rects?: number[][];
    start_line?: number | null;
    end_line?: number | null;
    start_char?: number;
    end_char?: number;
  };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || "请求失败");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  sessions: () => request<Session[]>("/api/sessions"),
  createSession: () => request<Session>("/api/sessions", { method: "POST" }),
  updateSession: (id: string, data: { title?: string; model_profile_id?: string }) =>
    request<Session>(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  messages: (id: string) =>
    request<{ messages: ChatMessage[]; session_total_tokens: number | null }>(
      `/api/sessions/${id}/messages`,
    ),
  providers: () => request<Provider[]>("/api/providers"),
  saveProvider: (data: Record<string, unknown>) =>
    request<Provider>("/api/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  models: () => request<ModelProfile[]>("/api/models"),
  saveModel: (data: Record<string, unknown>) =>
    request<ModelProfile>("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  testModel: (id: string) => request<{ ok: boolean; response: string }>(`/api/models/${id}/test`, { method: "POST" }),
  retrievalSettings: () => request<RetrievalSettings>("/api/retrieval/config"),
  saveRetrievalSettings: (data: RetrievalConfig) =>
    request<RetrievalSettings>("/api/retrieval/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  documents: () => request<DocumentRecord[]>("/api/documents"),
  uploadDocuments: (form: FormData, duplicateMode: string) =>
    request<Array<{ file_name: string; status: string; chunk_count: number; detail: string }>>(
      `/api/documents?duplicate_mode=${encodeURIComponent(duplicateMode)}`,
      { method: "POST", body: form },
    ),
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  documentVersions: (id: string) => request<DocumentRecord[]>(`/api/documents/${id}/versions`),
  activateDocument: (id: string) => request<DocumentRecord>(`/api/documents/${id}/activate`, { method: "POST" }),
  operationLogs: (limit = 100) => request<OperationLog[]>(`/api/operation-logs?limit=${limit}`),
  preview: (citation: Citation) =>
    request<PreviewData>(
      `/api/documents/${citation.doc_id}/preview?chunk_id=${encodeURIComponent(citation.chunk_id)}`,
    ),
};

export async function streamRun(
  sessionId: string,
  message: string,
  modelProfileId: string | null,
  onEvent: (event: { type: string; data?: unknown; [key: string]: unknown }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, model_profile_id: modelProfileId }),
    signal,
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || "无法开始问答");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const line = frame.split("\n").find((item) => item.startsWith("data:"));
      if (line) onEvent(JSON.parse(line.slice(5).trim()));
    }
    if (done) break;
  }
}
