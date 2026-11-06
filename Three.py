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

def time_now(_text=""):
    """返回当前本地时间。"""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

def self_edit(code):
    """安全改自己：备份 Three.py → 执行 code 修改 → ast 验证 → 失败回滚。
    code 是一段 Python 代码，可读写 Three.py。返回结果字符串。"""
    import shutil
    self_path = os.path.abspath(__file__)
    bak = self_path + ".bak"
    try:
        shutil.copy2(self_path, bak)
    except Exception as e:
        return f"[备份失败] {e}"
    try:
        env = {"__file__": self_path, "os": os, "re": re, "open": open, "print": print}
        exec(code, env)
    except Exception as e:
        shutil.copy2(bak, self_path)
        return f"[执行失败已回滚] {e}"
    try:
        import ast
        ast.parse(open(self_path, encoding="utf-8").read())
    except Exception as e:
        shutil.copy2(bak, self_path)
        return f"[语法错误已回滚] {e}"
    return f"[已修改并验证] {self_path}，备份在 {bak}"

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
    chat_loop()
