import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ModelProfile } from "../src/api";
import { ModelPicker } from "../src/components/ModelPicker";

const models: ModelProfile[] = [
  {
    profile_id: "qwen-plus",
    provider_id: "dashscope",
    provider_name: "DashScope",
    provider_type: "dashscope",
    model_id: "qwen-plus",
    display_name: "Qwen Plus",
    enabled: true,
  },
  {
    profile_id: "deepseek-v4-flash",
    provider_id: "deepseek",
    provider_name: "DeepSeek",
    provider_type: "openai_compatible",
    model_id: "deepseek-v4-flash",
    display_name: "DeepSeek V4 Flash",
    enabled: true,
  },
];

describe("ModelPicker", () => {
  it("使用轻量触发器打开模型菜单并切换模型", async () => {
    const onSelect = vi.fn();
    render(<ModelPicker models={models} selectedId="qwen-plus" onSelect={onSelect} />);

    fireEvent.pointerDown(screen.getByRole("button", { name: "选择生成模型" }), { button: 0, ctrlKey: false });
    const deepSeek = await screen.findByRole("menuitem", { name: /DeepSeek V4 Flash/ });
    fireEvent.click(deepSeek);

    expect(onSelect).toHaveBeenCalledWith("deepseek-v4-flash");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });
});
