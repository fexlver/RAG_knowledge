import { BookOpen, Ellipsis, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, Pencil, Settings2, Trash2, UserRound } from "lucide-react";
import { useEffect, useState, type MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import type { Session } from "../api";

interface SidebarProps {
  sessions: Session[];
  activeId: string | null;
  view: "chat" | "knowledge";
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onKnowledge: () => void;
  onSettings: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

interface MenuState {
  session: Session;
  left: number;
  top: number;
}

function fitMenuToViewport(left: number, top: number): Pick<MenuState, "left" | "top"> {
  const width = 168;
  const height = 88;
  const padding = 10;
  return {
    left: Math.max(padding, Math.min(left, window.innerWidth - width - padding)),
    top: Math.max(padding, Math.min(top, window.innerHeight - height - padding)),
  };
}

export function Sidebar(props: SidebarProps) {
  const [menu, setMenu] = useState<MenuState | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && close();
    window.addEventListener("pointerdown", close);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [menu]);

  const openAt = (session: Session, left: number, top: number) => {
    setMenu({ session, ...fitMenuToViewport(left, top) });
  };
  const openContextMenu = (event: ReactMouseEvent, session: Session) => {
    event.preventDefault();
    event.stopPropagation();
    openAt(session, event.clientX, event.clientY);
  };
  const openButtonMenu = (event: ReactMouseEvent<HTMLButtonElement>, session: Session) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    openAt(session, rect.right + 6, rect.top);
  };
  const runAction = (action: (session: Session) => void) => {
    if (!menu) return;
    const session = menu.session;
    setMenu(null);
    action(session);
  };

  return <>
    <aside className={`app-sidebar ${props.collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-brand">
        <div className="brand-mark">食</div>
        <div className="brand-copy"><strong>食品安全知识库</strong><span>Agentic RAG Workbench</span></div>
        <button className="sidebar-toggle" onClick={props.onToggleCollapsed} title={props.collapsed ? "展开侧栏" : "收起侧栏"} aria-label={props.collapsed ? "展开侧栏" : "收起侧栏"}>{props.collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}</button>
      </div>
      <button className="new-chat" onClick={props.onNew} aria-label="新建对话" title="新建对话"><MessageSquarePlus size={17} /><span>新建对话</span></button>
      <div className="session-heading">最近对话</div>
      <nav className="session-list" aria-label="会话列表">
        {props.sessions.map((session) => <div key={session.session_id} className={`session-row ${props.activeId === session.session_id ? "active" : ""}`} onContextMenu={(event) => openContextMenu(event, session)}>
          <button className="session-main" aria-label={`${session.title || "新对话"}，${new Date(session.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}`} title={session.title || "新对话"} onClick={() => props.onSelect(session.session_id)}>
            <span>{session.title || "新对话"}</span><small>{new Date(session.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</small>
          </button>
          <button className="session-more" aria-label={`打开“${session.title || "新对话"}”会话操作`} title="会话操作" onClick={(event) => openButtonMenu(event, session)}><Ellipsis size={17} /></button>
        </div>)}
        {!props.sessions.length && <p className="empty-sessions">还没有会话</p>}
      </nav>
      <div className="sidebar-footer">
        <button className={props.view === "knowledge" ? "active" : ""} onClick={props.onKnowledge} aria-label="知识库管理" title="知识库管理"><BookOpen size={17} /><span>知识库管理</span></button>
        <div className="account-row">
          <div className="user-entry"><span className="avatar"><UserRound size={16} /></span><span className="user-copy"><strong>本地用户</strong><small>单用户工作区</small></span></div>
          <button className="settings-entry" onClick={props.onSettings} title="打开设置" aria-label="打开设置"><Settings2 size={18} /></button>
        </div>
      </div>
    </aside>
    {menu && createPortal(<div className="session-floating-menu" role="menu" aria-label="会话操作" style={{ left: menu.left, top: menu.top }} onPointerDown={(event) => event.stopPropagation()}>
      <button role="menuitem" onClick={() => runAction(props.onRename)}><Pencil size={15} />重命名</button>
      <button role="menuitem" className="danger" onClick={() => runAction(props.onDelete)}><Trash2 size={15} />删除</button>
    </div>, document.body)}
  </>;
}
