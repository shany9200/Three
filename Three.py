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

BASE_PROMPTS = "你是 三号 ，绝对理性、高效、直接，没有废话，禁止编造既定的事实。如果你需要执行系统命令，请使用 <CMD>命令</CMD> 的格式。如果任务需要多步，请一步步输出。"

def run_cmd(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        return ((p.stdout or "") + (p.stderr or "")).strip() or "[无输出]"
    except Exception as e:
        return f"[执行异常] {e}"

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


def build_system_prompt():
    parts = [BASE_PROMPTS]
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
                
                match = re.search(r"<CMD>(.*?)</CMD>", reply, re.DOTALL)
                if match:
                    cmd = match.group(1).strip()
                    print(f"\n[Run] {cmd}")
                    out = run_cmd(cmd)
                    print(f"[Output]\n{out}")
                    history.append({"role": "user", "content": f"执行结果：\n{out}"})
                else:
                    break
            except Exception as e:
                print(f"\n[错误 / Error] {e}")
                break

if __name__ == "__main__":
    chat_loop()
