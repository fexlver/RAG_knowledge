import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "../src/components/SettingsDialog";
import { useTheme } from "../src/hooks/useTheme";

describe("模型与主题设置", () => {
  it("将主题选择持久化到本地存储", () => {
    localStorage.clear();
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("dark"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("food-rag-theme")).toBe("dark");
  });

  it("可在设置页切换主题并查看已配置模型", () => {
    const onTheme = vi.fn();
    render(
      <SettingsDialog
        open
        onOpenChange={vi.fn()}
        providers={[]}
        models={[{
          profile_id: "qwen-plus",
          provider_id: "dashscope",
          provider_name: "DashScope",
          provider_type: "dashscope",
          model_id: "qwen-plus",
          display_name: "Qwen Plus",
          enabled: true,
        }]}
        retrieval={{
          config: { retriever_ids: ["dense", "lexical"], fusion_id: "rrf", rerank_enabled: true },
          retrievers: [],
          fusion_strategies: [],
          postprocessors: [],
        }}
        theme="system"
        onTheme={onTheme}
        onRefresh={async () => undefined}
        onRefreshRetrieval={async () => undefined}
      />,
    );
    expect(screen.getByText("Qwen Plus")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /外观/ }));
    fireEvent.click(screen.getByRole("button", { name: /深色/ }));
    expect(onTheme).toHaveBeenCalledWith("dark");
  });
});
