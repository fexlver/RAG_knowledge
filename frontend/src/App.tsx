import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Panel, Group, Separator } from "react-resizable-panels";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Trash2, X } from "lucide-react";
import {
  api,
  streamRun,
  type ChatMessage,
  type Citation,
  type DocumentRecord,
  type ModelProfile,
  type PreviewData,
  type Provider,
  type RetrievalSettings,
  type Session,
  type TokenUsage,
  type TraceEvent,
} from "./api";
import { ChatWorkbench } from "./components/ChatWorkbench";
import { KnowledgeBase } from "./components/KnowledgeBase";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { SourcePreview } from "./components/SourcePreview";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [retrievalSettings, setRetrievalSettings] = useState<RetrievalSettings | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [sessionTotal, setSessionTotal] = useState<number | null>(null);
  const [view, setView] = useState<"chat" | "knowledge">("chat");
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [running, setRunning] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem("food-rag-sidebar-collapsed") === "true",
  );
  const [renameTarget, setRenameTarget] = useState<Session | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [fatalError, setFatalError] = useState("");
  const [theme, setTheme] = useTheme();
  const abortRef = useRef<AbortController | null>(null);
  const initializedRef = useRef(false);

  const refreshSessions = useCallback(async () => {
    const items = await api.sessions(); setSessions(items); return items;
  }, []);
  const refreshModels = useCallback(async () => {
    const [providerItems, modelItems] = await Promise.all([api.providers(), api.models()]);
    setProviders(providerItems); setModels(modelItems);
  }, []);
  const refreshDocuments = useCallback(async () => setDocuments(await api.documents()), []);
  const refreshRetrievalSettings = useCallback(async () => setRetrievalSettings(await api.retrievalSettings()), []);
  const loadMessages = useCallback(async (sessionId: string) => {
    const data = await api.messages(sessionId);
    setMessages(data.messages); setSessionTotal(data.session_total_tokens);
  }, []);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    Promise.all([refreshSessions(), refreshModels(), refreshDocuments(), refreshRetrievalSettings()])
      .then(async ([items]) => {
        if (items.length) setActiveId(items[0].session_id);
        else { const created = await api.createSession(); setSessions([created]); setActiveId(created.session_id); }
      })
      .catch((error) => setFatalError((error as Error).message));
  }, [refreshDocuments, refreshModels, refreshRetrievalSettings, refreshSessions]);
  useEffect(() => { if (activeId) loadMessages(activeId).catch((error) => setFatalError((error as Error).message)); }, [activeId, loadMessages]);

  const activeSession = sessions.find((item) => item.session_id === activeId) || null;
  const createSession = async () => {
    const session = await api.createSession();
    setSessions((items) => [session, ...items]); setActiveId(session.session_id); setMessages([]); setPreview(null); setView("chat");
  };
  const selectSession = (id: string) => { setActiveId(id); setPreview(null); setView("chat"); };
  const renameSession = async () => {
    if (!renameTarget || !renameTitle.trim()) return;
    const updated = await api.updateSession(renameTarget.session_id, { title: renameTitle });
    setSessions((items) => items.map((item) => item.session_id === updated.session_id ? updated : item)); setRenameTarget(null);
  };
  const deleteSession = async () => {
    if (!deleteTarget) return;
    await api.deleteSession(deleteTarget.session_id);
    const remaining = sessions.filter((item) => item.session_id !== deleteTarget.session_id);
    setSessions(remaining); setDeleteTarget(null);
    if (deleteTarget.session_id === activeId) {
      if (remaining.length) setActiveId(remaining[0].session_id);
      else await createSession();
    }
  };
  const chooseModel = async (profileId: string) => {
    if (!activeId) return;
    const updated = await api.updateSession(activeId, { model_profile_id: profileId });
    setSessions((items) => items.map((item) => item.session_id === activeId ? updated : item));
  };
  const send = async (text: string) => {
    if (!activeId || running) return;
    const temporaryId = `stream-${Date.now()}`;
    const profile = models.find((item) => item.profile_id === (activeSession?.model_profile_id || models[0]?.profile_id));
    const user: ChatMessage = { id: `user-${Date.now()}`, role: "user", content: text, trace: [], citations: [], model_profile_id: profile?.profile_id || null, usage: { input_tokens: null, output_tokens: null, total_tokens: null }, refused: false };
    const assistant: ChatMessage = { id: temporaryId, role: "assistant", content: "", trace: [], citations: [], model_profile_id: profile?.profile_id || null, model_name: profile?.display_name, usage: { input_tokens: null, output_tokens: null, total_tokens: null }, refused: false, streaming: true };
    setMessages((items) => [...items, user, assistant]); setRunning(true); setFatalError("");
    const controller = new AbortController(); abortRef.current = controller;
    let streamFailed = false;
    const updateAssistant = (updater: (message: ChatMessage) => ChatMessage) => setMessages((items) => items.map((item) => item.id === temporaryId ? updater(item) : item));
    try {
      await streamRun(activeId, text, profile?.profile_id || null, (event) => {
        if (event.type === "text_delta") updateAssistant((item) => ({ ...item, content: item.content + String(event.data || "") }));
        else if (event.type === "trace") updateAssistant((item) => {
          const incoming = event.data as TraceEvent;
          const identity = incoming.event_id || incoming.stage;
          const index = item.trace.findIndex((trace) => (trace.event_id || trace.stage) === identity);
          if (index < 0) return { ...item, trace: [...item.trace, incoming] };
          const trace = [...item.trace]; trace[index] = incoming;
          return { ...item, trace };
        });
        else if (event.type === "citation") updateAssistant((item) => ({ ...item, citations: [...item.citations, event.data as Citation] }));
        else if (event.type === "usage") { const usage = event.data as TokenUsage; updateAssistant((item) => ({ ...item, usage })); setSessionTotal(usage.session_total_tokens ?? null); }
        else if (event.type === "done") { const done = event.data as ChatMessage; updateAssistant(() => ({ ...done, streaming: false })); }
        else if (event.type === "error") { const data = event.data as { message: string }; streamFailed = true; updateAssistant((item) => ({ ...item, streaming: false, error: data.message })); }
      }, controller.signal);
      if (streamFailed) await refreshSessions();
      else await Promise.all([loadMessages(activeId), refreshSessions()]);
    } catch (error) {
      if ((error as Error).name !== "AbortError") updateAssistant((item) => ({ ...item, streaming: false, error: (error as Error).message }));
    } finally { setRunning(false); abortRef.current = null; }
  };
  const openCitation = async (citation: Citation) => {
    try { setPreview(await api.preview(citation)); }
    catch (error) { setFatalError((error as Error).message); }
  };

  const toggleSidebar = () => setSidebarCollapsed((value) => {
    localStorage.setItem("food-rag-sidebar-collapsed", String(!value));
    return !value;
  });

  return <div className="app-shell">
    <Sidebar sessions={sessions} activeId={activeId} view={view} onSelect={selectSession} onNew={createSession} onRename={(session) => { setRenameTarget(session); setRenameTitle(session.title); }} onDelete={setDeleteTarget} onKnowledge={() => { setView("knowledge"); setPreview(null); }} onSettings={() => setSettingsOpen(true)} collapsed={sidebarCollapsed} onToggleCollapsed={toggleSidebar} />
    <div className="workspace">
      {view === "knowledge" ? <KnowledgeBase documents={documents} onRefresh={refreshDocuments} /> : <Group orientation="horizontal" className="panel-group">
        <Panel minSize={420} defaultSize={preview ? "58" : "100"}>
          <ChatWorkbench session={activeSession} sessions={sessions} messages={messages} models={models} running={running} sessionTotal={sessionTotal} onSend={send} onAbort={() => abortRef.current?.abort()} onModel={chooseModel} onCitation={openCitation} onSelectSession={selectSession} onRenameSession={async (id, title) => { await api.updateSession(id, { title }); await refreshSessions(); }} onDeleteSession={async (id) => { await api.deleteSession(id); await refreshSessions(); }} />
        </Panel>
        {preview && <><Separator className="panel-resizer" /><Panel minSize={340} defaultSize="42" maxSize="60"><SourcePreview key={`${preview.doc_id}:${preview.chunk_id}`} preview={preview} onClose={() => setPreview(null)} /></Panel></>}
      </Group>}
    </div>
    {fatalError && <div className="toast-error"><AlertCircle size={16} />{fatalError}<button onClick={() => setFatalError("")}><X size={14} /></button></div>}
    {retrievalSettings && <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} providers={providers} models={models} retrieval={retrievalSettings} theme={theme} onTheme={setTheme} onRefresh={refreshModels} onRefreshRetrieval={refreshRetrievalSettings} />}
    <Dialog.Root open={!!renameTarget} onOpenChange={(open) => !open && setRenameTarget(null)}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="small-dialog"><Dialog.Title>重命名会话</Dialog.Title><Dialog.Description>输入一个便于识别的会话名称。</Dialog.Description><input autoFocus value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} onKeyDown={(event) => event.key === "Enter" && renameSession()} /><div className="dialog-buttons"><Dialog.Close className="secondary-button">取消</Dialog.Close><button className="primary-button" onClick={renameSession}>保存</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <AlertDialog.Root open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}><AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay" /><AlertDialog.Content className="small-dialog"><div className="danger-dialog-icon"><Trash2 size={20} /></div><AlertDialog.Title>删除这个会话？</AlertDialog.Title><AlertDialog.Description>“{deleteTarget?.title}”及其全部消息将被永久删除。</AlertDialog.Description><div className="dialog-buttons"><AlertDialog.Cancel className="secondary-button">取消</AlertDialog.Cancel><AlertDialog.Action className="danger-button" onClick={deleteSession}>删除</AlertDialog.Action></div></AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>
  </div>;
}
