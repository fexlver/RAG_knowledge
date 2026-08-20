import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "../src/components/Sidebar";

const session = {
  session_id: "session-1",
  title: "食品添加剂查询",
  updated_at: "2026-08-20T08:00:00Z",
  model_profile_id: "qwen-plus",
};

function renderSidebar() {
  const onRename = vi.fn();
  const onDelete = vi.fn();
  render(
    <Sidebar
      sessions={[session]}
      activeId={session.session_id}
      view="chat"
      onSelect={vi.fn()}
      onNew={vi.fn()}
      onRename={onRename}
      onDelete={onDelete}
      onKnowledge={vi.fn()}
      onSettings={vi.fn()}
      collapsed={false}
      onToggleCollapsed={vi.fn()}
    />,
  );
  return { onRename, onDelete };
}

describe("Sidebar", () => {
  it("支持通过会话右键菜单重命名", async () => {
    const { onRename } = renderSidebar();
    const row = screen.getByText(session.title).closest(".session-row");
    expect(row).not.toBeNull();
    fireEvent.contextMenu(row!);
    fireEvent.click(await screen.findByText("重命名"));
    expect(onRename).toHaveBeenCalledWith(session);
  });

  it("保留省略号菜单并提供删除操作", async () => {
    const { onDelete } = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: /会话操作/ }));
    fireEvent.click(await screen.findByText("删除"));
    expect(onDelete).toHaveBeenCalledWith(session);
  });
});
