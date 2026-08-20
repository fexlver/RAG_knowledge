import * as Collapsible from "@radix-ui/react-collapsible";
import { CheckCircle2, ChevronDown, GitBranch, Search, Sparkles } from "lucide-react";
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

export function TracePanel({ trace }: { trace: TraceEvent[] }) {
  if (!trace.length) return null;
  return (
    <Collapsible.Root className="trace-card">
      <Collapsible.Trigger className="trace-trigger">
        <span className="trace-status-dot" />
        <span>已完成知识库检索 · {trace.length} 个步骤</span>
        <ChevronDown size={15} className="trace-chevron" />
      </Collapsible.Trigger>
      <Collapsible.Content className="trace-content">
        {trace.map((item, index) => {
          const Icon = stageIcons[item.stage] || CheckCircle2;
          return (
            <div className="trace-step" key={`${item.stage}-${index}`}>
              <Icon size={14} />
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
