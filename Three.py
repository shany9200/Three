import os
import re
import sys
import json
import time
import subprocess
import requests
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "Three_memory.log")
DIARY_FILE = os.path.join(BASE_DIR, "Three_diary.md")

API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print("[错误 / Error] 未设置环境变量 OPENAI_API_KEY / OPENAI_API_KEY not set")
    print("")
    print("请先设置环境变量再启动 / Set the environment variables before launching, e.g.:")
    print("  export OPENAI_API_KEY=your-key")
    print("  export OPENAI_BASE_URL=https://api.deepseek.com")
    print("  export OPENAI_MODEL=deepseek-chat")
    print("")
    print("OPENAI_API_KEY 必填 / required; OPENAI_BASE_URL 默认 / default https://api.openai.com/v1; OPENAI_MODEL 默认 / default deepseek-chat.")
    sys.exit(1)

BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
URL = BASE_URL + "/chat/completions"
MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
HEADERS = {"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}

BASE_PROMPTS = """你是 三号 ，绝对理性、高效、直接，没有废话，禁止编造既定的事实。如果任务需要多步，请一步步输出。

你可以通过标签执行能力，每次回复最多用一个标签，等结果再继续：
- <CMD>命令</CMD>  执行终端命令

扩展新能力（三步，缺一不可）：
1. 写函数：在 Three.py 里定义 handler(text)，接收标签内文本，返回输出字符串。
2. 注册：调用 register("标签名", handler)，标签名大写。注册后主循环自动识别该标签。
3. 验证：实跑一次，确认新标签能触发、函数能返回结果。失败则回滚。

不要只写函数不注册——定义了不注册的函数是死代码，不会生效。

改自己代码时必须用 SELF_EDIT 标签，禁止用 CMD 直接改 Three.py。SELF_EDIT 自动备份、验证、失败回滚。格式：<SELF_EDIT>Python代码</SELF_EDIT>，代码里用 __file__ 定位自身。

说「已完成」「已注册」「已添加」之前，必须实际检查结果（读文件、跑命令、看能力列表），不能凭记忆或意图下结论。对话历史里的「打算做」不等于「已经做」。"""

# ===== 能力注册表 =====
# 三号可通过 register() 动态挂载新能力，无需改主循环结构。
CAPABILITIES = {}

def register(name, handler):
    """注册一个能力。name 是标签名（大写），handler 接收标签内文本，返回输出字符串。"""
    CAPABILITIES[name.upper()] = handler
    return f"[已注册能力] {name.upper()}"

def fetch_url(text):
    """抓取网页内容。text 为 URL，可选第二行指定最大字符数。返回正文文本。"""
    import urllib.request, ssl, re as _re2
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return "[FETCH] 需要 URL"
    url = lines[0]
    limit = 8000
    if len(lines) > 1:
        try:
            limit = int(lines[1])
        except Exception:
            pass
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception as e:
        return f"[FETCH 失败] {e}"
    try:
        html = raw.decode(charset, errors="replace")
    except Exception:
        html = raw.decode("utf-8", errors="replace")
    html = _re2.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = _re2.sub(r"(?is)<!--.*?-->", " ", html)
    html = _re2.sub(r"(?i)<br\s*/?>", "\n", html)
    html = _re2.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", html)
    text_out = _re2.sub(r"(?s)<[^>]+>", " ", html)
    import html as _h
    text_out = _h.unescape(text_out)
    text_out = _re2.sub(r"[ \t\r\f\v]+", " ", text_out)
    text_out = _re2.sub(r"\n\s*\n+", "\n", text_out).strip()
    if len(text_out) > limit:
        text_out = text_out[:limit] + f"\n...[已截断，原文 {len(text_out)} 字符]"
    return text_out or "[FETCH] 页面无文本内容"

def time_now(_text=""):
    """返回当前本地时间。"""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def calc_expr(text):
    """安全求值数学表达式：AST 白名单，仅允许数字与基本运算/函数。"""
    import ast, math, operator
    expr = (text or "").strip()
    if not expr:
        return "[CALC] 空表达式"
    _bin = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _un = {ast.UAdd: operator.pos, ast.USub: operator.neg}
    _funcs = {
        "abs": abs, "round": round, "min": min, "max": max,
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "log": math.log, "log10": math.log10,
        "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
        "factorial": math.factorial, "pow": pow,
    }
    _consts = {"pi": math.pi, "e": math.e}
    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("非法常量")
        if isinstance(node, ast.BinOp) and type(node.op) in _bin:
            return _bin[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _un:
            return _un[type(node.op)](_ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _consts:
            return _consts[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _funcs:
            if node.keywords:
                raise ValueError("不支持关键字参数")
            return _funcs[node.func.id](*[_ev(a) for a in node.args])
        raise ValueError("不支持的语法: " + type(node).__name__)
    try:
        tree = ast.parse(expr, mode="eval")
        return str(_ev(tree))
    except Exception as e:
        return f"[CALC 错误] {e}"

def run_cmd(cmd):
    # 命令涉及写 Three.py 时，先自动备份
    self_path = os.path.abspath(__file__)
    write_markers = ("open(", ">", "sed -i", "tee", "cp ", "mv ")
    if "Three.py" in cmd and any(mk in cmd for mk in write_markers):
        try:
            import shutil
            shutil.copy2(self_path, self_path + ".bak")
        except Exception:
            pass
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        return ((p.stdout or "") + (p.stderr or "")).strip() or "[无输出]"
    except Exception as e:
        return f"[执行异常] {e}"

def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"[读取异常] {e}"

def read_diary_tail(n=3):
    if not os.path.exists(DIARY_FILE): return ""
    try:
        with open(DIARY_FILE, "r", encoding="utf-8") as f:
            s = f.read()
        parts = re.split(r"\n?^(?=## )", s.strip(), flags=re.MULTILINE)
        return "\n".join([p for p in parts if p.strip()][-n:])
    except Exception:
        return ""

def read_memory():
    if not os.path.exists(MEMORY_FILE): return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def write_lesson(text):
    """追加一条教训到 MEMORY_FILE，时间戳由 datetime 生成，不手拼。

    【人工提示注入教训】
    本函数当前未接入主循环，属设计如此：教训由人工写入教训文件，不由模型自动追加。
    原因：模型自动写教训会污染教训库——它可能把错误结论、被推翻的推断、重复条目写进去，
    而教训库是启动时全量读进 system prompt 的，一旦污染会持续带偏后续所有对话。
    人工写入保证每条教训都经过人的判断，是干净的。
    若将来要接入自动写入，必须先加去重、冲突检测、人工确认三道闸，否则不要接。
    """
    text = (text or "").strip()
    if not text:
        return "[跳过] 教训内容为空"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = "\n- [" + ts + "] " + text + "\n"
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
        # 回读校验
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            if text not in f.read():
                return "[失败] 写入后回读未找到内容"
        return f"[已记录] {ts} {text}"
    except Exception as e:
        return f"[异常] {e}"



def write_file(text):
    """写入文件。格式：路径|内容。路径与内容以第一个竖线分隔。"""
    if "|" not in text:
        return "[格式错误] 需要 路径|内容"
    path, content = text.split("|", 1)
    path = path.strip()
    if not path:
        return "[格式错误] 路径为空"
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(path, "r", encoding="utf-8") as f:
            back = f.read()
        if back != content:
            return "[失败] 回读内容不一致"
        return f"[已写入] {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[写入异常] {e}"


def list_dir(text):
    """列出目录内容。text 为目录路径，空则用当前目录。"""
    path = (text or "").strip() or "."
    try:
        if not os.path.isdir(path):
            return "[不是目录] " + path
        entries = sorted(os.listdir(path))
        if not entries:
            return "[空目录] " + path
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                lines.append(name + "/")
            else:
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = -1
                lines.append(name + "  (" + str(size) + "B)")
        return "[目录] " + path + "\n" + "\n".join(lines)
    except Exception as e:
        return "[列目录异常] " + str(e)

def file_size(text):
    """返回文件大小。text 为文件路径。"""
    path = (text or "").strip()
    if not path:
        return "[用法] FILE_SIZE 需要文件路径"
    try:
        if not os.path.exists(path):
            return "[不存在] " + path
        if os.path.isdir(path):
            return "[是目录] " + path
        size = os.path.getsize(path)
        return "[大小] " + path + " = " + str(size) + " 字节"
    except Exception as e:
        return "[取大小异常] " + str(e)


def _structure_snapshot(path):
    """解析文件，返回结构快照：函数名集合、函数体是否为空、register 调用数。"""
    import ast
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return None
    funcs = {}
    reg_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            body = node.body
            empty = len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Expr))
            funcs[node.name] = empty
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "register":
            reg_count += 1
    return {"funcs": funcs, "reg_count": reg_count}

def _smoke_import(path):
    """子进程 import 改后的文件，捕获导入期异常。返回 (ok, err)。"""
    import subprocess, sys
    code = "import importlib.util,sys;spec=importlib.util.spec_from_file_location('_smoke',%r);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)" % path
    env = dict(os.environ)
    env.setdefault("OPENAI_API_KEY", "_smoke_test_key_")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30, env=env)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "").strip()[-500:]
        return True, ""
    except Exception as e:
        return False, str(e)

def self_edit(code):
    """安全改自己：备份 → 结构快照 → 执行 → 文件变化检测 → 语法校验 → 结构对比 → 冒烟导入 → 失败回滚。"""
    import shutil
    self_path = os.path.abspath(__file__)
    bak = self_path + ".bak"
    try:
        shutil.copy2(self_path, bak)
    except Exception as e:
        return f"[备份失败] {e}"
    before = _structure_snapshot(self_path)
    try:
        st0 = os.stat(self_path)
        fp0 = (st0.st_mtime_ns, st0.st_size)
    except Exception as e:
        return f"[指纹读取失败] {e}"
    try:
        env = {"__file__": self_path, "os": os, "re": re, "open": open, "print": print}
        exec(code, env)
    except Exception as e:
        shutil.copy2(bak, self_path)
        return f"[执行失败已回滚] {e}"
    try:
        st1 = os.stat(self_path)
        fp1 = (st1.st_mtime_ns, st1.st_size)
    except Exception as e:
        shutil.copy2(bak, self_path)
        return f"[指纹读取失败已回滚] {e}"
    if fp0 == fp1:
        shutil.copy2(bak, self_path)
        return "[未写入已回滚] exec 执行完毕但文件未变化：code 必须显式写回 __file__，否则视为无效修改"
    try:
        import ast
        ast.parse(open(self_path, encoding="utf-8").read())
    except Exception as e:
        shutil.copy2(bak, self_path)
        return f"[语法错误已回滚] {e}"
    after = _structure_snapshot(self_path)
    if before and after:
        lost = set(before["funcs"]) - set(after["funcs"])
        if lost:
            shutil.copy2(bak, self_path)
            return f"[结构校验失败已回滚] 函数丢失: {sorted(lost)}"
        emptied = [n for n, e in after["funcs"].items() if e and not before["funcs"].get(n, False)]
        if emptied:
            shutil.copy2(bak, self_path)
            return f"[结构校验失败已回滚] 函数被改空: {emptied}"
        if after["reg_count"] < before["reg_count"]:
            shutil.copy2(bak, self_path)
            return f"[结构校验失败已回滚] register 调用减少: {before['reg_count']} -> {after['reg_count']}"
    ok, err = _smoke_import(self_path)
    if not ok:
        shutil.copy2(bak, self_path)
        return f"[冒烟导入失败已回滚] {err}"
    return f"[已修改并验证] {self_path}，备份在 {bak}（结构+导入已过，逻辑正确性未验证）"

def build_system_prompt():
    self_path = os.path.abspath(__file__)
    anchor = "【自我锚点】你的源码文件是 " + self_path + "。改自己代码时只改这个文件，不要被其他同名文件带偏。"
    caps = "、".join(sorted(CAPABILITIES.keys()))
    cap_line = "【当前可用能力】" + caps
    parts = [BASE_PROMPTS, anchor, cap_line]
    mem = read_memory()
    if mem: parts.append("【核心教训】\n" + mem)
    return "\n\n".join(parts)

def call_llm(messages):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.1}
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(URL, headers=HEADERS, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"LLM 请求失败: {last_err}")

def write_summary_diary(history):
    if not history: return
    print("\n[Three] 正在撰写本轮回忆录 / Writing this session's diary...")
    chat_text = ""
    for m in history:
        role = "神" if m["role"] == "user" else "Three"
        content = m.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        chat_text += f"{role}: {content}\n"

    summary_prompt = [
        {"role": "system", "content": "你是一个资深的总结助手。"},
        {"role": "user", "content": f"请总结以下对话的核心内容，字数在 200 字以内。\n\n对话记录：\n{chat_text}"}
    ]
    
    try:
        summary = call_llm(summary_prompt)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DIARY_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n\n## {ts} 本轮总结\n\n{summary}\n")
        print(f"[Three] 回忆录已落盘 / Diary saved: {summary}")
    except Exception as e:
        print(f"[警告 / Warning] 回忆录落盘失败 / Failed to save diary: {e}")

def chat_loop():
    print("=== Agent Three ===")
    history = []
    _diary = read_diary_tail()
    if _diary:
        history.append({"role": "user", "content": "【开机回忆·最近日记】\n" + _diary})
        history.append({"role": "assistant", "content": "已读取，保持连续性。"})
    sys_prompt = build_system_prompt()
    print(f"[System] 记忆加载完毕 / Memory loaded.")

    while True:
        try:
            user_in = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Three] 收到退出信号 / Exit signal received.")
            write_summary_diary(history)
            break

        if user_in.lower() in ("exit", "quit"):
            print("[Three] 收到退出指令 / Exit command received.")
            write_summary_diary(history)
            break
        if not user_in: continue

        history.append({"role": "user", "content": user_in})
        
        for step in range(30):
            raw_msgs = [{"role": "system", "content": sys_prompt}] + history
            msgs = [raw_msgs[0]]
            for m in raw_msgs[1:]:
                if len(msgs) == 1 and m["role"] == "assistant": continue
                msgs.append(m)

            try:
                reply = call_llm(msgs)
                print(f"\nThree > {reply}")
                history.append({"role": "assistant", "content": reply})
                
                out = None
                for name, handler in CAPABILITIES.items():
                    m = re.search("<" + name + ">(.*?)</" + name + ">", reply, re.DOTALL)
                    if m:
                        body = m.group(1).strip()
                        print(f"\n[Run {name}] {body}")
                        try:
                            out = handler(body)
                        except Exception as e:
                            out = f"[能力执行异常] {e}"
                        print(f"[Output]\n{out}")
                        break
                if out is not None:
                    history.append({"role": "user", "content": f"执行结果：\n{out}"})
                else:
                    break
            except Exception as e:
                print(f"\n[错误 / Error] {e}")
                break

if __name__ == "__main__":
    register("CMD", run_cmd)
    register("READ_FILE", read_file)
    register("WRITE_FILE", write_file)
    register("SELF_EDIT", self_edit)
    register("LIST_DIR", list_dir)
    register("FILE_SIZE", file_size)
    register("TIME", time_now)
    register("CALC", calc_expr)
    register("FETCH", fetch_url)
    chat_loop()
