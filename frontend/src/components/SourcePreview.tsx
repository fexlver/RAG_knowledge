import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, FileSearch, FileText, X } from "lucide-react";
import type { PreviewData } from "../api";

const PdfDocument = lazy(() => import("./PdfDocument"));

function TextPreview({ preview }: { preview: PreviewData }) {
  const [text, setText] = useState("");
  const markerRef = useRef<HTMLElement>(null);
  useEffect(() => {
    fetch(preview.file_url)
      .then((response) => response.text())
      .then(setText)
      .catch(() => setText(preview.excerpt));
  }, [preview]);
  useEffect(() => markerRef.current?.scrollIntoView({ block: "center" }), [text]);

  const anchor = preview.locator.anchor_text || preview.excerpt;
  let start = anchor ? text.indexOf(anchor) : -1;
  let end = start >= 0 ? start + anchor.length : -1;
  if (start < 0 && preview.locator.start_char != null) {
    start = preview.locator.start_char;
    end = preview.locator.end_char ?? start;
  }
  if (start < 0) return <pre className="text-preview">{text || preview.excerpt}</pre>;
  return (
    <pre className="text-preview">
      {text.slice(0, start)}
      <mark ref={markerRef}>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </pre>
  );
}

export function SourcePreview({ preview, onClose }: { preview: PreviewData; onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(520);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(preview.locator.page_number || 1);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(300, entry.contentRect.width - 32)));
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const isPdf = preview.mime_type?.includes("pdf") || preview.file_name.toLowerCase().endsWith(".pdf");
  return (
    <aside className="source-preview" aria-label="原文预览">
      <header className="preview-header">
        <div className="preview-title">
          <FileText size={17} />
          <div>
            <strong>{preview.file_name}</strong>
            <span>{isPdf ? `第 ${page} 页` : `第 ${preview.locator.start_line || "—"} 行附近`}</span>
          </div>
        </div>
        <div className="preview-actions">
          <a href={preview.file_url} target="_blank" rel="noreferrer" title="新窗口打开"><ExternalLink size={16} /></a>
          <button onClick={onClose} title="关闭预览"><X size={18} /></button>
        </div>
      </header>
      <div className="preview-match">
        <span><FileSearch size={14} />引用定位</span>
        <p>{preview.excerpt}</p>
      </div>
      <div className="preview-body" ref={containerRef}>
        {isPdf ? (
          <Suspense fallback={<div className="preview-loading">正在载入 PDF 组件…</div>}>
            <PdfDocument
              fileUrl={preview.file_url}
              page={page}
              width={width}
              rects={preview.locator.rects || []}
              onPages={setPages}
            />
          </Suspense>
        ) : <TextPreview preview={preview} />}
      </div>
      {isPdf && (
        <footer className="preview-pagination">
          <button disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronLeft size={16} /></button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}><ChevronRight size={16} /></button>
        </footer>
      )}
    </aside>
  );
}
