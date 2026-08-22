import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, FileText, Minus, Plus, X } from "lucide-react";
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
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    if (!containerRef.current) return;
    // 宽度取整且仅在变化时更新，避免亚像素尺寸或滚动条伸缩引发的重复渲染
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.max(300, Math.round(entry.contentRect.width) - 32);
      setWidth((prev) => (prev === next ? prev : next));
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // 稳定 rects 引用，避免父组件渲染时传入新数组导致 PDF 文本层重复重建
  const rects = useMemo(() => preview.locator.rects ?? [], [preview]);

  const isPdf = preview.mime_type?.includes("pdf") || preview.file_name.toLowerCase().endsWith(".pdf");
  const citationPage = preview.locator.page_number ?? null;
  const pageHint = citationPage == null || page === citationPage
    ? "已高亮引用文字"
    : `引用在第 ${citationPage} 页`;
  return (
    <aside className="source-preview" aria-label="原文预览">
      <header className="preview-header">
        <div className="preview-title">
          <FileText size={17} />
          <div>
            <strong>{preview.file_name}</strong>
            <span>{isPdf ? `PDF · 第 ${page} 页 · ${pageHint}` : `TXT · 第 ${preview.locator.start_line || "-"} 行附近`}</span>
          </div>
        </div>
        <div className="preview-actions">
          <a href={preview.file_url} target="_blank" rel="noreferrer" title="新窗口打开"><ExternalLink size={16} /></a>
          <button onClick={onClose} title="关闭预览"><X size={18} /></button>
        </div>
      </header>
      <div className="preview-body" ref={containerRef}>
        {isPdf ? (
          <Suspense fallback={<div className="preview-loading">正在载入 PDF 组件…</div>}>
            <PdfDocument
              fileUrl={preview.file_url}
              page={page}
              width={Math.round(width * zoom)}
              rects={rects}
              rectsPage={preview.locator.page_number ?? null}
              onPages={setPages}
            />
          </Suspense>
        ) : <TextPreview preview={preview} />}
      </div>
      {isPdf && (
        <footer className="preview-pagination">
          <div className="preview-control-group">
            <button disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} title="上一页"><ChevronLeft size={16} /></button>
            <span>第 {page} / {pages} 页</span>
            <button disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))} title="下一页"><ChevronRight size={16} /></button>
          </div>
          <div className="preview-control-group zoom-controls">
            <button disabled={zoom <= 0.8} onClick={() => setZoom((value) => Math.max(0.8, Number((value - 0.1).toFixed(1))))} title="缩小"><Minus size={15} /></button>
            <span>{Math.round(zoom * 100)}%</span>
            <button disabled={zoom >= 1.6} onClick={() => setZoom((value) => Math.min(1.6, Number((value + 0.1).toFixed(1))))} title="放大"><Plus size={15} /></button>
          </div>
        </footer>
      )}
    </aside>
  );
}
