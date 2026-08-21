import * as Dialog from "@radix-ui/react-dialog";
import { Check, Database, KeyRound, Loader2, Moon, Plus, Search, ServerCog, Sparkles, Sun, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type ModelProfile, type Provider, type RetrievalConfig, type RetrievalSettings, type ThemeMode } from "../api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providers: Provider[];
  models: ModelProfile[];
  retrieval: RetrievalSettings | null;
  theme: ThemeMode;
  onTheme: (theme: ThemeMode) => void;
  onRefresh: () => Promise<void>;
  onRefreshRetrieval: () => Promise<void>;
}

export function SettingsDialog(props: Props) {
  const [tab, setTab] = useState<"models" | "retrieval" | "appearance">("models");
  const [providerForm, setProviderForm] = useState({ name: "", provider_type: "openai_compatible", base_url: "https://api.openai.com/v1", api_key: "" });
  const [modelForm, setModelForm] = useState({ provider_id: "", model_id: "", display_name: "" });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [retrievalForm, setRetrievalForm] = useState<RetrievalConfig>(() => props.retrieval?.config || { retriever_ids: ["dense", "lexical"], fusion_id: "rrf", rerank_enabled: true });

  // 每次打开设置时清空上一轮的保存提示，避免旧 notice 留存到下次打开
  useEffect(() => { if (props.open) setNotice(""); }, [props.open]);

  const saveProvider = async () => {
    setBusy(true); setNotice("");
    try {
      await api.saveProvider(providerForm);
      setProviderForm({ ...providerForm, name: "", api_key: "" });
      setNotice("提供方已保存，API Key 已写入系统凭据库。");
      await props.onRefresh();
    } catch (error) { setNotice((error as Error).message); } finally { setBusy(false); }
  };
  const saveModel = async () => {
    setBusy(true); setNotice("");
    try {
      await api.saveModel(modelForm);
      setModelForm({ provider_id: modelForm.provider_id, model_id: "", display_name: "" });
      setNotice("模型已添加，可在聊天输入框中选择。");
      await props.onRefresh();
    } catch (error) { setNotice((error as Error).message); } finally { setBusy(false); }
  };
  const test = async (model: ModelProfile) => {
    setBusy(true); setNotice(`正在测试 ${model.display_name}…`);
    try { const result = await api.testModel(model.profile_id); setNotice(`连接成功：${result.response || "OK"}`); }
    catch (error) { setNotice(`连接失败：${(error as Error).message}`); }
    finally { setBusy(false); }
  };
  const toggleRetriever = (pluginId: string) => setRetrievalForm((current) => ({
    ...current,
    retriever_ids: current.retriever_ids.includes(pluginId)
      ? current.retriever_ids.filter((item) => item !== pluginId)
      : [...current.retriever_ids, pluginId],
  }));
  const saveRetrieval = async () => {
    setBusy(true); setNotice("");
    try {
      await api.saveRetrievalSettings(retrievalForm);
      await props.onRefreshRetrieval();
      setNotice("检索流水线已更新，将从下一轮问答开始生效。");
    } catch (error) { setNotice((error as Error).message); } finally { setBusy(false); }
  };

  const handleOpenChange = (open: boolean) => {
    // 关闭时清空本轮提示，避免旧状态在下次打开时短暂闪现。
    if (!open) setNotice("");
    props.onOpenChange(open);
  };

  return <Dialog.Root open={props.open} onOpenChange={handleOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="settings-dialog">
        <header>
          <div><Dialog.Title>工作台设置</Dialog.Title><Dialog.Description>模型凭据仅写入系统凭据库，不会保存在 SQLite 或返回给前端。</Dialog.Description></div>
          <Dialog.Close className="icon-button" title="关闭设置"><X size={19} /></Dialog.Close>
        </header>
        <div className="settings-body">
          <nav className="settings-nav">
            <button className={tab === "models" ? "active" : ""} onClick={() => setTab("models")}><ServerCog size={18} />模型服务</button>
            <button className={tab === "retrieval" ? "active" : ""} onClick={() => setTab("retrieval")}><Search size={18} />检索策略</button>
            <button className={tab === "appearance" ? "active" : ""} onClick={() => setTab("appearance")}><Sun size={18} />外观主题</button>
          </nav>
          <div className="settings-content">
            {tab === "models" && <>
              <section>
                <div className="settings-section-heading"><div><h3>已配置模型</h3><p>聊天时可按会话选择下列生成模型。</p></div><span>{props.models.length} 个</span></div>
                <div className="configured-models">
                  {props.models.map((model) => <div className="model-row" key={model.profile_id}>
                    <span className="model-icon"><ServerCog size={18} /></span>
                    <div className="model-details"><strong>{model.display_name}</strong><span><b>提供方</b>{model.provider_name}</span><span><b>模型 ID</b><code>{model.model_id}</code></span></div>
                    <span className={`connection-badge ${model.enabled ? "enabled" : ""}`}>{model.enabled ? "已启用" : "已停用"}</span>
                    <button disabled={busy} onClick={() => test(model)}>测试连接</button>
                  </div>)}
                  {!props.models.length && <p className="settings-empty">尚未配置生成模型。</p>}
                </div>
              </section>
              <section>
                <div className="settings-section-heading"><div><h3><KeyRound size={17} />模型提供方</h3><p>先保存提供方和凭据，再添加具体模型。</p></div></div>
                {!!props.providers.length && <div className="provider-list">{props.providers.map((provider) => <div key={provider.provider_id}><strong>{provider.name}</strong><span>{provider.provider_type}</span><code title={provider.base_url}>{provider.base_url}</code><span>{provider.has_api_key ? "凭据已保存" : "未配置凭据"}</span></div>)}</div>}
                <div className="form-grid">
                  <label>提供方名称<input value={providerForm.name} placeholder="例如：DeepSeek" onChange={(event) => setProviderForm({ ...providerForm, name: event.target.value })} /></label>
                  <label>接口协议<select value={providerForm.provider_type} onChange={(event) => setProviderForm({ ...providerForm, provider_type: event.target.value, base_url: event.target.value === "dashscope" ? "https://dashscope.aliyuncs.com/api/v1" : providerForm.base_url })}><option value="openai_compatible">OpenAI-compatible</option><option value="dashscope">DashScope</option></select></label>
                  <label className="wide">Base URL<input value={providerForm.base_url} onChange={(event) => setProviderForm({ ...providerForm, base_url: event.target.value })} /></label>
                  <label className="wide">API Key<input type="password" autoComplete="new-password" value={providerForm.api_key} placeholder="保存至系统凭据库" onChange={(event) => setProviderForm({ ...providerForm, api_key: event.target.value })} /></label>
                </div>
                <button className="secondary-button" disabled={busy || !providerForm.name || !providerForm.base_url} onClick={saveProvider}><Plus size={16} />保存提供方</button>
              </section>
              <section>
                <div className="settings-section-heading"><div><h3>添加生成模型</h3><p>显示名称用于界面选择，模型 ID 必须与提供方接口一致。</p></div></div>
                <div className="form-grid">
                  <label>提供方<select value={modelForm.provider_id} onChange={(event) => setModelForm({ ...modelForm, provider_id: event.target.value })}><option value="">请选择</option>{props.providers.map((provider) => <option value={provider.provider_id} key={provider.provider_id}>{provider.name}</option>)}</select></label>
                  <label>显示名称<input value={modelForm.display_name} placeholder="例如：DeepSeek Chat" onChange={(event) => setModelForm({ ...modelForm, display_name: event.target.value })} /></label>
                  <label className="wide">模型 ID<input value={modelForm.model_id} placeholder="例如：deepseek-chat" onChange={(event) => setModelForm({ ...modelForm, model_id: event.target.value })} /></label>
                </div>
                <button className="secondary-button" disabled={busy || !modelForm.provider_id || !modelForm.model_id || !modelForm.display_name} onClick={saveModel}>{busy ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}添加模型</button>
              </section>
            </>}
            {tab === "retrieval" && <section className="retrieval-settings">
              <div className="settings-section-heading"><div><h3><Search size={17} />检索流水线</h3><p>自由组合候选召回方式；新增多模态或图检索插件后会自动出现在这里。</p></div><span>{retrievalForm.retriever_ids.length} 路召回</span></div>
              <div className="pipeline-preview" aria-label="当前检索流水线">
                {retrievalForm.retriever_ids.map((pluginId, index) => {
                  const plugin = props.retrieval?.retrievers.find((item) => item.plugin_id === pluginId);
                  return <span key={pluginId}>{index > 0 && <b>+</b>}<em>{plugin?.label || pluginId}</em></span>;
                })}
                {retrievalForm.retriever_ids.length > 1 && <><i>→</i><em>{props.retrieval?.fusion_strategies.find((item) => item.plugin_id === retrievalForm.fusion_id)?.label || "排名融合"}</em></>}
                {retrievalForm.rerank_enabled && <><i>→</i><em>模型重排</em></>}
              </div>
              <div className="retrieval-plugin-list">
                {props.retrieval?.retrievers.map((plugin) => {
                  const active = retrievalForm.retriever_ids.includes(plugin.plugin_id);
                  const Icon = plugin.plugin_id === "dense" ? Sparkles : Database;
                  return <button type="button" className={active ? "active" : ""} key={plugin.plugin_id} onClick={() => toggleRetriever(plugin.plugin_id)}>
                    <span className="retrieval-plugin-icon"><Icon size={20} /></span>
                    <span><strong>{plugin.label}</strong><small>{plugin.description}</small></span>
                    <span className="plugin-check">{active && <Check size={15} />}</span>
                  </button>;
                })}
              </div>
              <div className="rerank-option">
                <div><strong>模型二阶段重排</strong><span>在多路召回和融合后，用相关性模型筛选最终证据。</span></div>
                <button type="button" role="switch" aria-checked={retrievalForm.rerank_enabled} className={retrievalForm.rerank_enabled ? "active" : ""} onClick={() => setRetrievalForm((current) => ({ ...current, rerank_enabled: !current.rerank_enabled }))}><span /></button>
              </div>
              <div className="retrieval-save-row"><p>检索配置为知识库全局设置，不会改变已有向量维度和文档索引。</p><button className="primary-button" disabled={busy || retrievalForm.retriever_ids.length === 0} onClick={saveRetrieval}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />}保存检索策略</button></div>
            </section>}
            {tab === "appearance" && <section>
              <div className="settings-section-heading"><div><h3>工作台主题</h3><p>主题选择保存在当前浏览器中。</p></div></div>
              <div className="theme-options">{([{"id":"light","label":"浅色","icon":Sun},{"id":"dark","label":"深色","icon":Moon},{"id":"system","label":"跟随系统","icon":ServerCog}] as const).map((item) => { const Icon = item.icon; return <button className={props.theme === item.id ? "active" : ""} key={item.id} onClick={() => props.onTheme(item.id)}><Icon size={24} /><span>{item.label}</span>{props.theme === item.id && <Check size={16} />}</button>; })}</div>
            </section>}
            {notice && <p className="settings-notice" role="status">{notice}</p>}
          </div>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
