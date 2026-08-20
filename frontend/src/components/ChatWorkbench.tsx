import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { ArrowDown, Copy, Loader2, Send, Square } from "lucide-react";
import { createContext, useContext, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import type { ChatMessage, Citation, ModelProfile, Session, TokenUsage, TraceEvent } from "../api";
import { TracePanel } from "./TracePanel";

interface ChatUiContextValue { onCitation: (citation: Citation) => void; }
const ChatUiContext = createContext<ChatUiContextValue>({ onCitation: () => {} });

function CitationMarkdown() {
  const rawText = useAuiState((state) => state.part.type === "text" ? state.part.text : "");
  const custom = useAuiState((state) => state.message.metadata.custom) as { citations?: Citation[] } | undefined;
  const { onCitation } = useContext(ChatUiContext);
  // 兼容旧消息：历史版本曾在回答末尾追加来源清单，新界面仅保留正文内的 [1] 引用。
  const text = rawText.replace(/\n{2,}参考依据：\s*\n(?:\[\d+\]\s*[^\n]*(?:\n|$))+$/u, "");
  const markdown = text.replace(/\[(\d+)]/g, "[$1](#citation-$1)");
  return <ReactMarkdown components={{
    a: ({ href, children }) => {
      const label = href?.startsWith("#citation-") ? Number(href.slice(10)) : NaN;
      const citation = custom?.citations?.find((item) => item.label === label);
      if (citation) return <button className="inline-citation" onClick={() => onCitation(citation)}>[{children}]</button>;
      return <a href={href}>{children}</a>;
    },
  }}>{markdown}</ReactMarkdown>;
}

function UserMessage() {
  return <MessagePrimitive.Root className="message user-message"><div className="message-bubble"><MessagePrimitive.Content /></div></MessagePrimitive.Root>;
}

function AssistantMessage() {
  const custom = useAuiState((state) => state.message.metadata.custom) as {
    trace?: TraceEvent[]; citations?: Citation[]; usage?: TokenUsage; modelName?: string; error?: string;
  } | undefined;
  const text = useAuiState((state) => state.message.content.filter((part) => part.type === "text").map((part) => part.type === "text" ? part.text : "").join(""));
  const messageId = useAuiState((state) => state.message.id);
  return <MessagePrimitive.Root className="message assistant-message">
    <div className="assistant-avatar">食</div>
    <div className="assistant-content">
      <MessagePrimitive.Content components={{ Text: CitationMarkdown }} />
      {custom?.error && <p className="message-error">{custom.error}</p>}
      <TracePanel key={String(messageId)} trace={custom?.trace || []} />
      <div className="message-meta"><span>{custom?.modelName || "知识库助手"}</span>{custom?.usage?.total_tokens != null && <span>{custom.usage.total_tokens.toLocaleString()} tokens</span>}<button title="复制回答" onClick={() => navigator.clipboard?.writeText(text)}><Copy size={13} /></button></div>
    </div>
  </MessagePrimitive.Root>;
}

const messageComponents = { UserMessage, AssistantMessage };

interface Props {
  session: Session | null;
  sessions: Session[];
  messages: ChatMessage[];
  models: ModelProfile[];
  running: boolean;
  sessionTotal: number | null;
  onSend: (message: string) => Promise<void>;
  onAbort: () => void;
  onModel: (profileId: string) => void;
  onCitation: (citation: Citation) => void;
  onSelectSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
}

const convertMessage = (message: ChatMessage): ThreadMessageLike => ({
  id: String(message.id),
  role: message.role,
  content: [{ type: "text", text: message.content || (message.streaming ? "正在检索知识库…" : "") }],
  createdAt: message.created_at ? new Date(message.created_at) : new Date(),
  metadata: {
    custom: {
      trace: message.trace,
      citations: message.citations,
      usage: message.usage,
      modelName: message.model_name,
      refused: message.refused,
      error: message.error,
    },
  },
});

export function ChatWorkbench(props: Props) {
  const runtime = useExternalStoreRuntime({
    messages: props.messages,
    convertMessage,
    isRunning: props.running,
    isDisabled: !props.session,
    onNew: async (message: AppendMessage) => {
      const text = message.content.filter((part) => part.type === "text").map((part) => part.type === "text" ? part.text : "").join("\n").trim();
      if (text) await props.onSend(text);
    },
    onCancel: async () => props.onAbort(),
    adapters: {
      threadList: {
        threadId: props.session?.session_id,
        threads: props.sessions.map((session) => ({ status: "regular" as const, id: session.session_id, remoteId: session.session_id, title: session.title })),
        onSwitchToThread: props.onSelectSession,
        onRename: props.onRenameSession,
        onDelete: props.onDeleteSession,
      },
    },
  });
  const selectedModel = props.session?.model_profile_id || props.models[0]?.profile_id || "";
  const lastUsage = useMemo(() => [...props.messages].reverse().find((item) => item.role === "assistant")?.usage, [props.messages]);
  return <ChatUiContext.Provider value={{ onCitation: props.onCitation }}>
    <AssistantRuntimeProvider runtime={runtime}>
      <main className="chat-workbench">
        <header className="chat-header"><div><strong>{props.session?.title || "新对话"}</strong><span>食品安全资料检索助手</span></div><div className={`run-indicator ${props.running ? "running" : ""}`}>{props.running ? <><Loader2 size={13} className="spin" />正在执行</> : "就绪"}</div></header>
        <ThreadPrimitive.Root className="thread-root">
          <div className="thread-scroll-region">
            <ThreadPrimitive.Viewport className="thread-viewport">
              <ThreadPrimitive.Empty><div className="welcome"><div className="welcome-mark">食</div><h1>今天想查询什么食品安全资料？</h1><p>我会执行混合检索、重排序与置信控制，并将每项关键结论链接到原始文件。</p><div className="suggestions"><ThreadPrimitive.Suggestion prompt="GB 2760 的适用范围是什么？">查询标准适用范围</ThreadPrimitive.Suggestion><ThreadPrimitive.Suggestion prompt="比较同一标准的新旧版本差异">比较标准版本差异</ThreadPrimitive.Suggestion><ThreadPrimitive.Suggestion prompt="当前知识库有哪些现行标准？">检查标准有效性</ThreadPrimitive.Suggestion></div></div></ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={messageComponents} />
              <ThreadPrimitive.ScrollToBottom className="scroll-bottom"><ArrowDown size={16} /></ThreadPrimitive.ScrollToBottom>
            </ThreadPrimitive.Viewport>
          </div>
          <div className="composer-dock">
            <div className="composer-footer">
              <ComposerPrimitive.Root className="composer-root">
                <ComposerPrimitive.Input className="composer-input" placeholder="询问食品安全标准、法规或公告…" rows={2} />
                <div className="composer-toolbar">
                  <select aria-label="选择生成模型" value={selectedModel} onChange={(event) => props.onModel(event.target.value)}>{props.models.filter((item) => Boolean(item.enabled)).map((model) => <option key={model.profile_id} value={model.profile_id}>{model.display_name}</option>)}</select>
                  <div className="token-usage">{lastUsage?.total_tokens != null ? <span>本轮 {lastUsage.input_tokens ?? "—"} / {lastUsage.output_tokens ?? "—"} tokens</span> : <span>提供方未返回用量</span>}<span>会话累计 {props.sessionTotal?.toLocaleString() ?? "—"}</span></div>
                  <AuiIf condition={(state) => !state.thread.isRunning}><ComposerPrimitive.Send className="send-button" title="发送"><Send size={17} /></ComposerPrimitive.Send></AuiIf>
                  <AuiIf condition={(state) => state.thread.isRunning}><ComposerPrimitive.Cancel className="send-button stop" title="停止"><Square size={15} /></ComposerPrimitive.Cancel></AuiIf>
                </div>
              </ComposerPrimitive.Root>
            </div>
          </div>
        </ThreadPrimitive.Root>
      </main>
    </AssistantRuntimeProvider>
  </ChatUiContext.Provider>;
}
