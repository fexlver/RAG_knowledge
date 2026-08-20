import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// 单元测试不需要启动 PDF.js worker，真实 PDF 渲染由 Playwright 验收。
vi.mock("react-pdf", () => ({
  Document: ({ children }: { children: unknown }) => children,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: {} },
}));

afterEach(() => cleanup());

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: ResizeObserverMock,
});

Object.defineProperty(globalThis, "PointerEvent", {
  configurable: true,
  value: MouseEvent,
});

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

HTMLElement.prototype.scrollIntoView = () => undefined;
