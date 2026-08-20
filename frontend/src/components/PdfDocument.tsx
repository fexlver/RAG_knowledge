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
  return (
    <Document
      file={fileUrl}
      onLoadSuccess={({ numPages }) => onPages(numPages)}
      loading={<div className="preview-loading">正在载入原文…</div>}
      error={<div className="preview-error">PDF 无法载入，请在新窗口打开。</div>}
      className="pdf-document"
    >
      <div className="pdf-page-wrap">
        <Page pageNumber={page} width={width} renderTextLayer renderAnnotationLayer />
        <div className="highlight-layer" aria-label="引用段落高亮">
          {rects.map((rect, index) => (
            <span
              key={index}
              style={{
                left: `${rect[0] * 100}%`,
                top: `${rect[1] * 100}%`,
                width: `${(rect[2] - rect[0]) * 100}%`,
                height: `${(rect[3] - rect[1]) * 100}%`,
              }}
            />
          ))}
        </div>
      </div>
    </Document>
  );
}
