import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArchiveRestore, ChevronDown, ChevronRight, Database, FileArchive, FileClock, FileUp, RefreshCw, Search, Trash2, UploadCloud, X } from "lucide-react";
import { api, type DocumentRecord, type OperationLog, type UploadJob } from "../api";

interface Props {
  documents: DocumentRecord[];
  onRefresh: () => Promise<void>;
}

const formatSize = (bytes: number) => {
  if (!bytes) return "未知大小";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

export function KnowledgeBase({ documents, onRefresh }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<"documents" | "logs">("documents");
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState("skip");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [logs, setLogs] = useState<OperationLog[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [versions, setVersions] = useState<Record<string, DocumentRecord[]>>({});
  const [job, setJob] = useState<UploadJob | null>(null);
  const [uploadError, setUploadError] = useState("");
  const pollTimer = useRef<number | null>(null);

  useEffect(() => () => { if (pollTimer.current !== null) window.clearInterval(pollTimer.current); }, []);

  const refreshLogs = async () => setLogs(await api.operationLogs());
  useEffect(() => {
    let active = true;
    api.operationLogs().then((items) => active && setLogs(items)).catch(() => undefined);
    return () => { active = false; };
  }, []);
  const refreshAll = async () => { await Promise.all([onRefresh(), refreshLogs()]); };

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return documents;
    return documents.filter((document) => [document.file_name, document.standard_code, document.document_type, document.validity_status].some((value) => String(value || "").toLowerCase().includes(keyword)));
  }, [documents, query]);
  const totalChunks = documents.reduce((sum, document) => sum + Number(document.chunk_count || 0), 0);
  const versionedCount = documents.filter((document) => Number(document.version_count || 1) > 1).length;

  const fileMeta = (item: UploadJob["files"][number]) => {
    if (item.stage === "success") {
      const parts = [`${item.chunk_count} 个文本块`];
      if (item.parser) parts.push(item.parser);
      if (item.duration_seconds) parts.push(`${Math.round(item.duration_seconds)}s`);
      if (item.detail) parts.push(item.detail);
      return parts.join(" · ");
    }
    return item.detail;
  };

  const stopPolling = () => {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  };

  const upload = async () => {
    if (!files.length) return;
    setBusy(true);
    setUploadError("");
    try {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      // 后台任务立即返回 job_id；之后轮询每个文件的阶段直到任务结束。
      const started = await api.uploadDocuments(form, mode);
      setJob(started);
      setFiles([]);
      if (inputRef.current) inputRef.current.value = "";
      await new Promise<void>((resolve) => {
        pollTimer.current = window.setInterval(async () => {
          try {
            const current = await api.uploadJob(started.job_id);
            setJob(current);
            if (current.status === "done") {
              stopPolling();
              await refreshAll();
              resolve();
            }
          } catch {
            // 轮询失败（任务过期/网络断开）不再重试，保留最后一次进度。
            stopPolling();
            resolve();
          }
        }, 1500);
      });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "上传失败，请重试。");
    } finally { setBusy(false); }
  };
  const remove = async (document: DocumentRecord) => {
    if (!window.confirm(`确定删除“${document.file_name}”第 ${document.version_number || 1} 版及其索引吗？`)) return;
    await api.deleteDocument(document.doc_id);
    setVersions((items) => ({ ...items, [document.series_id]: (items[document.series_id] || []).filter((item) => item.doc_id !== document.doc_id) }));
    await refreshAll();
  };
  const toggleVersions = async (document: DocumentRecord) => {
    if (expanded === document.series_id) { setExpanded(null); return; }
    setExpanded(document.series_id);
    setVersions((items) => ({ ...items, [document.series_id]: items[document.series_id] || [] }));
    const items = await api.documentVersions(document.doc_id);
    setVersions((value) => ({ ...value, [document.series_id]: items }));
  };
  const activate = async (document: DocumentRecord) => {
    setBusy(true);
    try {
      await api.activateDocument(document.doc_id);
      const items = await api.documentVersions(document.doc_id);
      setVersions((value) => ({ ...value, [document.series_id]: items }));
      await refreshAll();
    } finally { setBusy(false); }
  };

  return <main className="knowledge-page">
    <header className="page-heading">
      <div><span className="eyebrow">KNOWLEDGE BASE</span><h1>知识库管理</h1><p>管理上市公司年报语料的入库、版本和检索状态。</p></div>
      <button className="secondary-button" onClick={refreshAll}><RefreshCw size={16} />刷新数据</button>
    </header>

    <section className="knowledge-stats" aria-label="知识库概览">
      <div><span><Database size={19} /></span><div><strong>{documents.length}</strong><small>当前文档</small></div></div>
      <div><span><FileClock size={19} /></span><div><strong>{versionedCount}</strong><small>含历史版本</small></div></div>
      <div><span><FileArchive size={19} /></span><div><strong>{totalChunks.toLocaleString()}</strong><small>可检索文本块</small></div></div>
      <div><span><Activity size={19} /></span><div><strong>{logs.length}</strong><small>近期操作记录</small></div></div>
    </section>

    <div className="knowledge-tabs" role="tablist">
      <button role="tab" aria-selected={tab === "documents"} className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>文档与版本</button>
      <button role="tab" aria-selected={tab === "logs"} className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>操作日志</button>
    </div>

    {tab === "documents" ? <>
      <section className="upload-card">
        <div className="section-title"><div><FileUp size={19} /><div><h2>文档入库</h2><p>支持 PDF/TXT，多文件在后台逐个解析并建立索引，进度实时可见。</p></div></div></div>
        <button className="upload-dropzone" onClick={() => inputRef.current?.click()}>
          <UploadCloud size={30} /><strong>选择或拖入待入库文件</strong><span>单击选择 PDF / TXT 文件；索引会保留页码、行号与段落定位。</span>
        </button>
        <input ref={inputRef} hidden multiple type="file" accept=".pdf,.txt" onChange={(event) => setFiles(Array.from(event.target.files || []))} />
        {!!files.length && <div className="upload-queue">{files.map((file, index) => <div key={`${file.name}-${index}`}><FileArchive size={16} /><span><strong>{file.name}</strong><small>{formatSize(file.size)}</small></span><button title="移出队列" onClick={() => setFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))}><X size={15} /></button></div>)}</div>}
        <div className="upload-toolbar">
          <div className="duplicate-policy"><strong>同名文件策略</strong><label><input type="radio" checked={mode === "skip"} onChange={() => setMode("skip")} />跳过并提示</label><label><input type="radio" checked={mode === "overwrite"} onChange={() => setMode("overwrite")} />保存为新版本</label></div>
          <button className="primary-button" disabled={!files.length || busy} onClick={upload}><FileUp size={16} />{busy ? "正在处理…" : files.length ? `上传 ${files.length} 个文件` : "上传文件"}</button>
        </div>
        {job && <div className="upload-progress">
          <div className="upload-progress-head">
            <div className="upload-progress-track"><div className="upload-progress-fill" style={{ width: `${job.total_files ? Math.round((job.finished_files / job.total_files) * 100) : 0}%` }} /></div>
            <span>{job.finished_files}/{job.total_files} 个文件{job.status === "running" ? " · 正在处理…" : " · 已完成"}</span>
          </div>
          <div className="upload-results">{job.files.map((item, index) => <div key={`${item.name}-${index}`}>
            <span className={`stage-badge ${item.stage}`}>{item.stage_label}{["parsing", "embedding", "writing"].includes(item.stage) ? "…" : ""}</span>
            <strong title={item.name}>{item.name}</strong>
            <span className="upload-file-meta" title={fileMeta(item)}>{fileMeta(item) || "等待处理"}</span>
          </div>)}</div>
        </div>}
        {uploadError && <p className="upload-error">{uploadError}</p>}
      </section>

      <section className="document-section">
        <div className="document-toolbar"><div className="section-title"><div><FileArchive size={19} /><div><h2>已入库文档</h2><p>列表仅展示当前检索版本，展开可管理历史版本。</p></div></div></div><label className="document-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名或类型" /></label></div>
        <div className="document-table-wrap"><table className="document-table">
          <thead><tr><th>文件</th><th>文档信息</th><th>索引</th><th>当前版本</th><th>入库时间</th><th>操作</th></tr></thead>
          <tbody>{filtered.map((document) => <Fragment key={document.doc_id}>
            <tr key={document.doc_id}>
              <td><div className="document-name"><button title="展开版本历史" onClick={() => toggleVersions(document)}>{expanded === document.series_id ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button><span><strong>{document.file_name}</strong><small>{formatSize(document.file_size)} · {document.mime_type || "未知类型"}</small></span></div></td>
              <td><strong>{document.standard_code || "年报语料"}</strong><small>{document.document_type || "其他"} · {document.validity_status || "未知"}</small></td>
              <td><strong>{Number(document.chunk_count || 0).toLocaleString()} 块</strong><small>{document.storage_path ? "原文可追溯" : "原文件缺失"}</small></td>
              <td><span className="version-badge">v{document.version_number || 1}</span><small>共 {document.version_count || 1} 版</small></td>
              <td>{new Date(document.created_at).toLocaleString("zh-CN")}</td>
              <td><div className="table-actions"><a className="icon-button" href={`/api/documents/${document.doc_id}/file`} target="_blank" rel="noreferrer" title="打开原文"><FileArchive size={16} /></a><button className="icon-button danger" title="删除当前版本" onClick={() => remove(document)}><Trash2 size={16} /></button></div></td>
            </tr>
            {expanded === document.series_id && <tr className="version-row" key={`${document.doc_id}-versions`}><td colSpan={6}><div className="version-history"><header><strong>版本历史</strong><span>切换版本后，仅当前版本参与问答检索。</span></header>{(versions[document.series_id] || []).map((version) => <div key={version.doc_id}><span className={`version-dot ${version.is_current ? "current" : ""}`} /><div><strong>第 {version.version_number} 版 {version.is_current ? "· 当前" : ""}</strong><small>{new Date(version.created_at).toLocaleString("zh-CN")} · {version.chunk_count} 个文本块 · {formatSize(version.file_size)}</small></div><code>{version.content_hash.slice(0, 10)}</code>{!version.is_current && <button className="secondary-button" disabled={busy} onClick={() => activate(version)}><ArchiveRestore size={15} />设为当前</button>}<button className="icon-button danger" title="删除此版本" onClick={() => remove(version)}><Trash2 size={15} /></button></div>)}{!(versions[document.series_id] || []).length && <p>正在加载版本记录…</p>}</div></td></tr>}
          </Fragment>)}{!filtered.length && <tr><td colSpan={6} className="empty-table">没有匹配的文档。</td></tr>}</tbody>
        </table></div>
      </section>
    </> : <section className="log-section">
      <div className="section-title"><div><Activity size={19} /><div><h2>操作日志</h2><p>记录文档入库、重复跳过、版本切换和删除操作。</p></div></div><span>最近 {logs.length} 条</span></div>
      <div className="log-list">{logs.map((log) => <div key={log.id}><span className="log-icon"><Activity size={15} /></span><div><strong>{log.action}</strong><p>{log.detail}</p><small>{log.target}</small></div><time>{new Date(log.created_at).toLocaleString("zh-CN")}</time></div>)}{!logs.length && <p className="empty-table">暂无操作日志。</p>}</div>
    </section>}
  </main>;
}
