import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcePreview } from "../src/components/SourcePreview";

afterEach(() => vi.restoreAllMocks());

describe("SourcePreview", () => {
  it("打开 TXT 原文并高亮引用段落", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("第一行\n食品添加剂应符合标准要求\n第三行"));
    const onClose = vi.fn();
    render(
      <SourcePreview
        preview={{
          doc_id: "doc-1",
          chunk_id: "chunk-1",
          file_name: "食品安全标准.txt",
          mime_type: "text/plain",
          file_url: "/api/documents/doc-1/file",
          excerpt: "食品添加剂应符合标准要求",
          locator: { kind: "text", start_line: 2, end_line: 2, anchor_text: "食品添加剂应符合标准要求" },
        }}
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      const highlighted = screen.getAllByText("食品添加剂应符合标准要求")
        .find((element) => element.tagName === "MARK");
      expect(highlighted).toBeVisible();
    });
    fireEvent.click(screen.getByTitle("关闭预览"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
