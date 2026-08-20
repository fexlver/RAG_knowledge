import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TracePanel } from "../src/components/TracePanel";

const trace = [
  {
    stage: "rewrite",
    status: "completed" as const,
    label: "查询改写",
    detail: "结合历史对话补全问题",
    duration_ms: 12,
  },
  {
    stage: "retrieval",
    status: "completed" as const,
    label: "混合召回",
    detail: "召回 15 个候选片段",
    duration_ms: 36,
  },
];

describe("TracePanel", () => {
  it("默认折叠并可展开查看结构化执行轨迹", () => {
    render(<TracePanel trace={trace} />);
    expect(screen.queryByText("结合历史对话补全问题")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("已完成知识库检索 · 2 个步骤"));
    expect(screen.getByText("结合历史对话补全问题")).toBeVisible();
    expect(screen.getByText("36 ms")).toBeVisible();
  });
});
