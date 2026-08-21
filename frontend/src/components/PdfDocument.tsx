import { useCallback, useEffect, useRef } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

interface Props {
  fileUrl: string;
  page: number;
  width: number;
  rects: number[][];
  onPages: (pages: number) => void;
}

export default function PdfDocument({ fileUrl, page, width, rects, onPages }: Props) {
  const pageRef = useRef<HTMLDivElement>(null);

  const highlightTextLines = useCallback(() => {
    const root = pageRef.current;
    if (!root || !rects.length) return;
    const pageBox = root.getBoundingClientRect();
    if (!pageBox.width || !pageBox.height) return;
    const textSpans = root.querySelectorAll<HTMLElement>(".react-pdf__Page__textContent span");
    textSpans.forEach((span) => {
      const box = span.getBoundingClientRect();
      const normalized = [
        (box.left - pageBox.left) / pageBox.width,
        (box.top - pageBox.top) / pageBox.height,
        (box.right - pageBox.left) / pageBox.width,
        (box.bottom - pageBox.top) / pageBox.height,
      ];
      const matches = rects.some((rect) => (
        normalized[2] > rect[0]
        && normalized[0] < rect[2]
        && normalized[3] > rect[1]
        && normalized[1] < rect[3]
      ));
      // toggle 只在命中状态变化时触碰 DOM；先 remove 再 add 会持续触发样式失效
      span.classList.toggle("citation-text-hit", matches);
    });
  }, [rects]);

  // react-pdf 把该回调列入文本层重建依赖：内联函数 + setState 会形成
  // “文本层重建 → 回调触发 → 再重建”的循环，高亮类名被反复擦除，表现为持续闪烁。
  const handleTextLayerRender = useCallback(() => {
    requestAnimationFrame(highlightTextLines);
  }, [highlightTextLines]);

  useEffect(() => {
    const frame = requestAnimationFrame(highlightTextLines);
    return () => cancelAnimationFrame(frame);
  }, [highlightTextLines, page, width]);

  const handleLoadSuccess = useCallback((pdf: { numPages: number }) => onPages(pdf.numPages), [onPages]);

  return (
    <Document
      file={fileUrl}
      onLoadSuccess={handleLoadSuccess}
      loading={<div className="preview-loading">正在载入原文…</div>}
      error={<div className="preview-error">PDF 无法载入，请在新窗口打开。</div>}
      className="pdf-document"
    >
      <div className="pdf-page-wrap" ref={pageRef} aria-label="已高亮引用原文">
        <Page pageNumber={page} width={width} renderTextLayer renderAnnotationLayer onRenderTextLayerSuccess={handleTextLayerRender} />
      </div>
    </Document>
  );
}
