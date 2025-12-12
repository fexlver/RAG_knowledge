import os
import sqlite3
import datetime
import uuid
import time
import gradio as gr
from dotenv import load_dotenv
import dashscope
from pymilvus import connections, Collection, utility

# 导入文档处理相关的库
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.vectorstores import Milvus
from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Tongyi

# ================= 配置常量 =================
DB_FILE = "system_data.db"
RERANK_MODEL = "gte-rerank"

# 全局变量
SESSION_TEMP_CACHE = {}


# 1. 加载环境变量
def load_env_variables():
    load_dotenv()
    # 根据之前的调试，暂时保留这个清空代理的设置
    # 如果你在国内直连阿里云，这通常是必须的；如果无法连接，可以尝试注释掉下面三行
    os.environ['HTTP_PROXY'] = ''
    os.environ['HTTPS_PROXY'] = ''
    os.environ['ALL_PROXY'] = ''
    return {
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY"),
        "milvus_host": os.getenv("MILVUS_HOST"),
        "milvus_port": os.getenv("MILVUS_PORT"),
    }


# ================= 数据库工具 =================
def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            '''CREATE TABLE IF NOT EXISTS operation_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, action_type TEXT, filename TEXT, status TEXT, details TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, name TEXT, created_at TEXT)''')
        cursor.execute(
            '''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp TEXT, FOREIGN KEY(session_id) REFERENCES sessions(id))''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"数据库初始化失败: {e}")


def log_operation(action, filename, status, details=""):
    """
    记录操作日志到数据库
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO operation_logs (timestamp, action_type, filename, status, details) VALUES (?, ?, ?, ?, ?)',
            (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), action, filename, status, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"日志记录失败: {e}")


def get_log_history():
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute(
            'SELECT timestamp, action_type, filename, status, details FROM operation_logs ORDER BY id DESC LIMIT 100')
        rows = cursor.fetchall();
        conn.close()
        return [list(row) for row in rows]
    except:
        return []


# ================= 会话管理函数 =================

def create_new_session(name=None):
    sid = str(uuid.uuid4())
    name = name or f"对话 {datetime.datetime.now().strftime('%m-%d %H:%M')}"
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (id, name, created_at) VALUES (?, ?, ?)",
                       (sid, name, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit();
        conn.close()
        log_operation("会话管理", name, "成功", "新建会话")
        return sid, name
    except:
        return None, None


def delete_session(sid):
    if not sid: return
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit();
        conn.close()
        if sid in SESSION_TEMP_CACHE: del SESSION_TEMP_CACHE[sid]
        log_operation("会话管理", sid, "成功", "删除会话")
    except:
        pass


def rename_session_db(sid, new_name):
    if not sid or not new_name: return False
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("UPDATE sessions SET name = ? WHERE id = ?", (new_name, sid))
        conn.commit();
        conn.close()
        log_operation("会话管理", sid, "成功", f"重命名为: {new_name}")
        return True
    except:
        return False


def get_session_list_raw():
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("SELECT name, created_at, id FROM sessions ORDER BY created_at DESC")
        rows = cursor.fetchall();
        conn.close()
        return [list(row) for row in rows]
    except:
        return []


def get_session_names_only(raw): return [[r[0]] for r in raw]


def save_message(sid, role, content):
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                       (sid, role, content, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit();
        conn.close()
    except:
        pass


def get_session_history(sid):
    if not sid: return []
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (sid,))
        rows = cursor.fetchall();
        conn.close()
        hist = []
        temp_user = None
        for role, content in rows:
            if role == 'user':
                temp_user = content
            elif role == 'assistant':
                hist.append([temp_user, content]);
                temp_user = None
        if temp_user: hist.append([temp_user, None])
        return hist
    except:
        return []


def get_session_history_for_llm(sid):
    if not sid: return []
    try:
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (sid,))
        rows = cursor.fetchall();
        conn.close()
        hist = [];
        temp = None
        for r, c in rows:
            if r == 'user':
                temp = c
            elif r == 'assistant' and temp:
                hist.append((temp, c));
                temp = None
        return hist
    except:
        return []


# ================= RAG Logic =================

def handle_temp_file_upload(files, sid):
    if not files or not sid: return "请先选择会话"
    content = ""
    names = []
    for f in files:
        try:
            c = ""
            if f.name.lower().endswith('.pdf'):
                loader = PyPDFLoader(f.name)
                c = "\n".join([d.page_content for d in loader.load()])
            elif f.name.lower().endswith('.txt'):
                with open(f.name, 'r', encoding='utf-8') as tf:
                    c = tf.read()
            if c:
                content += f"\n=== 临时文件: {os.path.basename(f.name)} ===\n{c}\n"
                names.append(os.path.basename(f.name))
        except:
            pass
    SESSION_TEMP_CACHE[sid] = SESSION_TEMP_CACHE.get(sid, "") + "\n" + content
    return f"已挂载: {', '.join(names)}"


def get_existing_filenames():
    try:
        if not utility.has_collection("food_safety_collection"): return set()
        col = Collection("food_safety_collection");
        col.load()
        return set(r["source"] for r in col.query(expr="pk >= 0", output_fields=["source"]))
    except:
        return set()


def delete_vectors_by_filename(name):
    try:
        Collection("food_safety_collection").delete(f'source == "{name}"');
        return True
    except:
        return False


def initialize_models_and_vector_store(env):
    emb = DashScopeEmbeddings(model="text-embedding-v2", dashscope_api_key=env["dashscope_api_key"])
    try:
        connections.connect(alias="default", host=env["milvus_host"], port=env["milvus_port"])
    except:
        pass
    vs = Milvus(emb, connection_args={"host": env["milvus_host"], "port": env["milvus_port"]},
                collection_name="food_safety_collection", auto_id=True, drop_old=False)
    llm = Tongyi(dashscope_api_key=env["dashscope_api_key"])
    return vs, llm


def rewrite_query(q, h, llm):
    if not h: return q
    hist_str = "\n".join([f"U:{u}\nA:{a}" for u, a in h])
    try:
        return llm.invoke(f"结合历史改写问题:\n{hist_str}\n问题:{q}\n只输出改写后句子。")
    except:
        return q


def rerank_documents(q, docs, k=4):
    if not docs: return []
    try:
        res = dashscope.TextReRank.call(model=RERANK_MODEL, query=q, documents=[d.page_content for d in docs], top_n=k,
                                        return_documents=True)
        if res.status_code == 200: return [docs[i.index] for i in res.output.results]
        return docs[:k]
    except:
        return docs[:k]


def answer_question(question, sid, vs, llm):
    if not question: return
    if sid: save_message(sid, "user", question)

    hist = get_session_history_for_llm(sid)
    search_q = rewrite_query(question, hist, llm)

    # 1. 检索文档 (增加错误处理)
    try:
        init_docs = vs.similarity_search(search_q, k=15)
    except Exception as e:
        print(f"检索出错: {e}")
        init_docs = []

    # 2. Rerank (增加错误处理)
    try:
        final_docs = rerank_documents(search_q, init_docs, k=4)
    except Exception as e:
        print(f"Rerank出错: {e}")
        final_docs = init_docs[:4]

    kb_ctx = "\n".join([d.page_content for d in final_docs])
    temp_ctx = SESSION_TEMP_CACHE.get(sid, "")

    full_ctx = ""
    if kb_ctx: full_ctx += f"【知识库】:\n{kb_ctx}\n"
    if temp_ctx: full_ctx += f"【临时文件(优先)】:\n{temp_ctx[:3000]}\n"
    if not full_ctx: full_ctx = "无参考文档"

    prompt = f"基于上下文回答:\n{full_ctx}\n历史:{hist}\n问题:{question}"

    # 3. LLM 生成 (增加重试机制)
    max_retries = 3
    full_ans = ""

    for attempt in range(max_retries):
        try:
            for chunk in llm.stream(prompt):
                full_ans += chunk
                yield chunk
            break  # 成功则退出循环
        except Exception as e:
            if attempt == max_retries - 1:
                error_msg = f"\n\n[系统错误] 网络连接失败，已重试 {max_retries} 次。详情: {e}"
                full_ans += error_msg
                yield error_msg
            else:
                print(f"连接断开，正在重试 ({attempt + 1}/{max_retries})...")
                yield f"\n[网络波动，正在重试 ({attempt + 1}/{max_retries})...]\n"
                time.sleep(2)

    if sid: save_message(sid, "assistant", full_ans)


def check_files_before_upload(files):
    if not files: return [], [], "空", gr.update(visible=False)
    exist = get_existing_filenames()
    n, d = [], []
    for f in files:
        if os.path.basename(f.name) in exist:
            d.append(f)
        else:
            n.append(f)
    if d: return n, d, "发现重复", gr.update(visible=True)
    return n, [], "准备上传", gr.update(visible=False)


# === 关键修改：增加日志记录的上传函数 ===
def execute_upload(new, dup, strat, vs):
    proc = []

    # 1. 处理覆盖逻辑 (日志点：覆盖旧文件)
    if strat == 'overwrite':
        for f in dup:
            fname = os.path.basename(f.name)
            if delete_vectors_by_filename(fname):
                log_operation("知识库管理", fname, "成功", "删除旧版本(准备覆盖)")
            proc.append(f)

    proc.extend(new)
    if not proc: return "无文件"

    # 2. 加载和切分文档
    docs = []
    for f in proc:
        try:
            l = PyPDFLoader(f.name) if f.name.endswith('.pdf') else TextLoader(f.name, encoding='utf-8')
            d = l.load()
            for i in d: i.metadata["source"] = os.path.basename(f.name)
            docs.extend(d)
        except Exception as e:
            log_operation("知识库管理", os.path.basename(f.name), "失败", f"文件解析错误: {str(e)}")
            pass

    # 3. 入库并记录日志 (日志点：入库成功)
    if docs:
        try:
            s = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
            vs.add_documents(s.split_documents(docs))

            # 对每一个成功处理的文件记录日志
            for f in proc:
                fname = os.path.basename(f.name)
                # 如果是覆盖的，这里会再次记录“入库成功”，形成完整的“删除旧的->写入新的”链条
                log_operation("知识库管理", fname, "成功", "文件入库成功")

            return "成功入库"
        except Exception as e:
            log_operation("知识库管理", "批量上传", "失败", f"向量库写入错误: {str(e)}")
            return f"入库失败: {e}"

    return "无有效内容"


def get_file_list_ui(): return [[s] for s in list(get_existing_filenames())]


# === 关键修改：增加日志记录的删除函数 ===
def delete_file_ui(n):
    if not n: return "未选择文件"
    if delete_vectors_by_filename(n):
        log_operation("知识库管理", n, "成功", "删除文件")
        return "已删除"
    else:
        log_operation("知识库管理", n, "失败", "删除失败")
        return "删除失败"


# ================= 主程序 =================


def main():
    init_db()
    if not get_session_list_raw(): create_new_session("默认会话")
    env = load_env_variables()
    vs, llm = initialize_models_and_vector_store(env)

    # 1. 定义主题
    theme = gr.themes.Soft(primary_hue="blue", secondary_hue="slate")

    # 2. 定义 CSS
    css = """
    body, gradio-app { overflow: hidden !important; }
    .gradio-container { height: 100vh !important; }
    .chat-window { flex-grow: 1; overflow-y: auto; }
    """

    # === 关键修改：theme 和 css 放在这里 ===
    with gr.Blocks(title="食品安全智能问答", fill_height=True, theme=theme, css=css) as demo:

        # 状态变量
        state_sessions = gr.State(get_session_list_raw())
        curr_sid = gr.State(get_session_list_raw()[0][2] if get_session_list_raw() else None)
        state_new, state_dup = gr.State([]), gr.State([])

        with gr.Row():
            gr.Markdown("### 🥗 食品安全智能问答")

        with gr.Tabs():

            # === Tab 1: 智能对话 ===
            with gr.TabItem("💬 智能对话"):
                with gr.Row(elem_classes="main-row"):
                    # --- 左侧：会话侧边栏 ---
                    with gr.Column(scale=1, variant="panel", visible=True, min_width=200) as sidebar_col:
                        with gr.Row():
                            gr.Markdown("**会话列表**")
                            btn_close_sidebar = gr.Button("<<", size="sm", min_width=30)

                        btn_new = gr.Button("➕ 新建", size="sm", variant="primary")

                        # max_rows 控制显示行数
                        session_table = gr.Dataframe(
                            headers=["会话名称"],
                            datatype=["str"],
                            value=get_session_names_only(state_sessions.value),
                            interactive=False,
                            label=None,
                            wrap=True,
                            elem_classes="sidebar-table"
                        )

                        with gr.Group():
                            gr.Markdown("---")
                            with gr.Row():
                                rename_input = gr.Textbox(show_label=False, placeholder="新名称...", scale=2,
                                                          min_width=50, container=False)
                                btn_rename = gr.Button("改名", size="sm", scale=1, min_width=40)
                            btn_del = gr.Button("🗑️ 删除", variant="stop", size="sm")

                    # --- 右侧：聊天主区域 ---
                    with gr.Column(scale=4, min_width=400) as chat_col:
                        with gr.Row():
                            btn_open_sidebar = gr.Button("≡", size="sm", visible=False, scale=0, min_width=30)
                            session_title = gr.Markdown(
                                f"### 🏷️ {state_sessions.value[0][0] if state_sessions.value else '无'}")

                        # min_height 保证聊天框不会缩得太小
                        chatbot = gr.Chatbot(
                            value=get_session_history(curr_sid.value),
                            type="tuples",
                            avatar_images=(None, "https://img.icons8.com/color/48/bot.png"),
                            show_label=False,
                            elem_classes="chat-window",
                            min_height=500
                        )

                        with gr.Row():
                            temp_file_btn = gr.UploadButton("📎", file_types=[".txt", ".pdf"], size="sm", scale=0,
                                                            min_width=40)
                            msg = gr.Textbox(show_label=False, placeholder="输入问题... (📎上传临时文件)", scale=10,
                                             container=False)
                            submit_btn = gr.Button("🚀", variant="primary", scale=0, min_width=60)

                        temp_status = gr.Markdown("", visible=True)

            # === Tab 2: 知识库管理 ===
            with gr.TabItem("📚 知识库管理"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 📂 现有文件")
                        with gr.Row():
                            refresh_btn = gr.Button("🔄 刷新", size="sm")
                            del_file_btn = gr.Button("💥 删除选中", variant="stop", size="sm")
                        del_status = gr.Textbox(label="结果", interactive=False, max_lines=1)
                        target_filename = gr.Textbox(visible=False)
                        file_table = gr.Dataframe(headers=["文件名"], datatype=["str"], interactive=False)

                    with gr.Column(scale=1, variant="panel"):
                        gr.Markdown("### 📤 上传新文档 (永久入库)")
                        file_input = gr.File(label="PDF/TXT", file_count="multiple")
                        check_btn = gr.Button("🔍 检查并上传", variant="primary")
                        with gr.Group(visible=False) as decision_group:
                            gr.Markdown("⚠️ **发现重复**")
                            with gr.Row():
                                overwrite_btn = gr.Button("覆盖", variant="stop", size="sm")
                                skip_btn = gr.Button("跳过", size="sm")
                        log_area = gr.TextArea(label="日志", interactive=False, lines=5)

            # === Tab 3: 日志 ===
            with gr.TabItem("📜 系统日志"):
                refresh_log = gr.Button("🔄 刷新", size="sm")
                log_table = gr.Dataframe(headers=["时间", "操作", "文件", "状态", "详情"], interactive=False)

        # ==================== 逻辑绑定 ====================

        # 1. 侧边栏 收缩/展开
        def close_sidebar():
            return gr.update(visible=False), gr.update(visible=True), gr.update(scale=10)

        def open_sidebar():
            return gr.update(visible=True), gr.update(visible=False), gr.update(scale=4)

        btn_close_sidebar.click(close_sidebar, [], [sidebar_col, btn_open_sidebar, chat_col])
        btn_open_sidebar.click(open_sidebar, [], [sidebar_col, btn_open_sidebar, chat_col])

        # 2. 会话切换
        def on_select(evt: gr.SelectData, all_sess):
            idx = evt.index[0]
            if idx < len(all_sess):
                sid = all_sess[idx][2]
                name = all_sess[idx][0]
                return sid, get_session_history(sid), f"### 🏷️ {name}", name, ""
            return gr.update(), gr.update(), gr.update(), "", ""

        session_table.select(on_select, [state_sessions], [curr_sid, chatbot, session_title, rename_input, temp_status])

        # 3. 新建
        def on_new():
            sid, name = create_new_session()
            raw = get_session_list_raw()
            return raw, get_session_names_only(raw), sid, [], f"### 🏷️ {name}"

        btn_new.click(on_new, [], [state_sessions, session_table, curr_sid, chatbot, session_title])

        # 4. 重命名
        def on_rename(sid, new_name):
            if rename_session_db(sid, new_name):
                raw = get_session_list_raw()
                return raw, get_session_names_only(raw), f"### 🏷️ {new_name}", ""
            return gr.update(), gr.update(), gr.update(), gr.update()

        btn_rename.click(on_rename, [curr_sid, rename_input],
                         [state_sessions, session_table, session_title, rename_input])

        # 5. 删除
        def on_del(sid):
            delete_session(sid)
            raw = get_session_list_raw()
            if raw:
                nsid = raw[0][2];
                nname = raw[0][0]
                return raw, get_session_names_only(raw), nsid, get_session_history(nsid), f"### 🏷️ {nname}"
            else:
                sid, name = create_new_session("默认会话")
                raw = [[name, "", sid]]
                return raw, [[name]], sid, [], f"### 🏷️ {name}"

        btn_del.click(on_del, [curr_sid], [state_sessions, session_table, curr_sid, chatbot, session_title])

        # 6. 聊天与上传
        temp_file_btn.upload(handle_temp_file_upload, [temp_file_btn, curr_sid], [temp_status])

        def chat_fn(q, h, sid):
            h = h + [[q, None]];
            yield "", h
            h[-1][1] = ""
            for c in answer_question(q, sid, vs, llm):
                h[-1][1] += c;
                yield "", h

        msg.submit(chat_fn, [msg, chatbot, curr_sid], [msg, chatbot])
        submit_btn.click(chat_fn, [msg, chatbot, curr_sid], [msg, chatbot])

        # 7. 知识库
        refresh_btn.click(get_file_list_ui, [], file_table)

        def on_select_file(evt: gr.SelectData):
            return evt.value

        file_table.select(on_select_file, None, target_filename)
        # 删除操作增加刷新日志的联动
        del_file_btn.click(delete_file_ui, [target_filename], [del_status]).then(get_file_list_ui, [], file_table).then(
            get_log_history, [], log_table)

        def check_click(files):
            n, d, m, g = check_files_before_upload(files)
            if not d and n: return n, d, g, execute_upload(n, [], 'direct', vs)
            return n, d, g, m

        check_btn.click(check_click, [file_input], [state_new, state_dup, decision_group, log_area])
        # 上传/覆盖操作增加刷新日志的联动
        overwrite_btn.click(lambda n, d: (gr.update(visible=False), execute_upload(n, d, 'overwrite', vs)),
                            [state_new, state_dup], [decision_group, log_area]).then(get_log_history, [], log_table)
        skip_btn.click(lambda n, d: (gr.update(visible=False), execute_upload(n, d, 'skip', vs)),
                       [state_new, state_dup], [decision_group, log_area]).then(get_log_history, [], log_table)

        # 8. 日志
        refresh_log.click(get_log_history, [], log_table)

        demo.load(get_log_history, None, log_table)
        demo.load(get_file_list_ui, None, file_table)

    demo.launch(share=True)


if __name__ == "__main__":
    main()