import * as Collapsible from "@radix-ui/react-collapsible";
import { CheckCircle2, ChevronDown, GitBranch, Loader2, Search, Sparkles } from "lucide-react";
import { useState } from "react";
import type { TraceEvent } from "../api";

const stageIcons: Record<string, typeof Search> = {
  rewrite: Sparkles,
  route: GitBranch,
  retrieval: Search,
  rerank: Sparkles,
  fusion: Search,
  confidence: CheckCircle2,
  generation: Sparkles,
};

export function TracePanel({ trace, running = false }: { trace: TraceEvent[]; running?: boolean }) {
  const [open, setOpen] = useState(running);
  if (!trace.length) return null;
  const completed = trace.filter((item) => item.status === "completed").length;
  return (
    <Collapsible.Root className={`trace-card ${running ? "streaming" : ""}`} open={running || open} onOpenChange={(value) => !running && setOpen(value)}>
      <Collapsible.Trigger className="trace-trigger">
        {running ? <Loader2 size={15} className="spin trace-live-icon" /> : <span className="trace-status-dot" />}
        <span>{running ? `正在执行 · ${completed}/${trace.length} 个步骤完成` : `已完成知识库检索 · ${trace.length} 个步骤`}</span>
        <ChevronDown size={15} className="trace-chevron" />
      </Collapsible.Trigger>
      <Collapsible.Content className="trace-content">
        {trace.map((item, index) => {
          const Icon = stageIcons[item.stage] || CheckCircle2;
          return (
            <div className={`trace-step ${item.status}`} key={item.event_id || `${item.stage}-${index}`}>
              <span className="trace-step-icon">{item.status === "running" ? <Loader2 size={14} className="spin" /> : item.status === "completed" ? <CheckCircle2 size={14} /> : <Icon size={14} />}</span>
              <div>
                <div className="trace-step-heading">
                  <strong>{item.label}</strong>
                  {item.duration_ms != null && <span>{item.duration_ms} ms</span>}
                </div>
                <p>{item.detail}</p>
              </div>
            </div>
          );
        })}
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
