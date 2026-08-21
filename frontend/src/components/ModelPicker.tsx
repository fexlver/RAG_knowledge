import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import { useState } from "react";
import type { ModelProfile } from "../api";

interface ModelPickerProps {
  models: ModelProfile[];
  selectedId: string;
  onSelect: (profileId: string) => void;
}

export function ModelPicker({ models, selectedId, onSelect }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const enabledModels = models.filter((model) => Boolean(model.enabled));
  const selected = enabledModels.find((model) => model.profile_id === selectedId);
  const selectModel = (profileId: string) => {
    // 会话模型持久化可能触发父组件刷新，先关闭浮层可避免刷新期间菜单残留。
    setOpen(false);
    onSelect(profileId);
  };

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger className="model-picker-trigger" aria-label="选择生成模型">
        <span>{selected?.display_name || "选择模型"}</span>
        <ChevronDown size={13} aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="model-picker-menu" align="start" side="top" sideOffset={9} collisionPadding={12}>
          <div className="model-picker-heading">生成模型</div>
          {enabledModels.map((model) => {
            const active = model.profile_id === selectedId;
            return (
              <DropdownMenu.Item
                className="model-picker-item"
                key={model.profile_id}
                onSelect={() => selectModel(model.profile_id)}
              >
                <span className="model-picker-check">{active && <Check size={14} />}</span>
                <span className="model-picker-copy">
                  <strong>{model.display_name}</strong>
                  <small>{model.provider_name || model.provider_type}</small>
                </span>
              </DropdownMenu.Item>
            );
          })}
          {!enabledModels.length && <div className="model-picker-empty">暂无可用模型</div>}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
