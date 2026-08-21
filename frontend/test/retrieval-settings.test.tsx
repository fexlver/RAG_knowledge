import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "../src/components/SettingsDialog";

afterEach(() => vi.restoreAllMocks());

describe("检索策略设置", () => {
  it("可组合检索插件并持久化全局流水线", async () => {
    const onRefreshRetrieval = vi.fn(async () => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      config: { retriever_ids: ["dense"], fusion_id: "rrf", rerank_enabled: true },
      retrievers: [],
      fusion_strategies: [],
      postprocessors: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    render(<SettingsDialog
      open
      onOpenChange={vi.fn()}
      providers={[]}
      models={[]}
      retrieval={{
        config: { retriever_ids: ["dense", "lexical"], fusion_id: "rrf", rerank_enabled: true },
        retrievers: [
          { plugin_id: "dense", label: "语义向量检索", description: "语义召回", category: "retriever" },
          { plugin_id: "lexical", label: "关键词检索", description: "精确召回", category: "retriever" },
        ],
        fusion_strategies: [{ plugin_id: "rrf", label: "RRF 排名融合", description: "融合", category: "fusion" }],
        postprocessors: [{ plugin_id: "model_rerank", label: "模型重排", description: "重排", category: "postprocessor" }],
      }}
      theme="system"
      onTheme={vi.fn()}
      onRefresh={async () => undefined}
      onRefreshRetrieval={onRefreshRetrieval}
    />);

    fireEvent.click(screen.getByRole("button", { name: /检索策略/ }));
    fireEvent.click(screen.getByRole("button", { name: /关键词检索/ }));
    fireEvent.click(screen.getByRole("button", { name: /保存检索策略/ }));

    await waitFor(() => expect(onRefreshRetrieval).toHaveBeenCalledOnce());
    const payload = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(payload.retriever_ids).toEqual(["dense"]);
  });
});
