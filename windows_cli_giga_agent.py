import os
import re
import sys
import json
import shutil
import platform
import subprocess
import urllib.parse
import tempfile
import ctypes

# --- ФИКС КОДИРОВКИ ДЛЯ WINDOWS ---
if platform.system() == "Windows":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

import requests
from bs4 import BeautifulSoup

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from colorama import init as color_init, Fore, Style
color_init(autoreset=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

LOGO = r"""
  ██╗    ██╗██╗███╗   ██╗██████╗  ██████╗ ██╗    ██╗███████╗
  ██║    ██║██║████╗  ██║██╔══██╗██╔═══██╗██║    ██║██╔════╝
  ██║ █╗ ██║██║██╔██╗ ██║██║  ██║██║   ██║██║ █╗ ██║███████╗
  ██║███╗██║██║██║╚██╗██║██║  ██║██║   ██║██║███╗██║╚════██║
  ╚███╔███╔╝██║██║ ╚████║██████╔╝╚██████╔╝╚███╔███╔╝███████║
   ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝╚═════╝  ╚═════╝  ╚══╝╚══╝ ╚══════╝
                  CLI GIGA AGENT
"""

# ---------- PATH ----------

def refresh_path():
    if IS_WINDOWS:
        try:
            import winreg
            paths = []
            for root, sub in (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        v, _ = winreg.QueryValueEx(k, "Path")
                        paths.append(v)
                except Exception:
                    pass
            if paths:
                os.environ["PATH"] = os.pathsep.join(paths)
        except Exception:
            pass
    else:
        extra = ["/usr/local/bin", "/usr/bin", "/bin",
                 os.path.expanduser("~/.local/bin")]
        cur = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(
            [p for p in extra + cur.split(os.pathsep) if p]
        )

def which(cmd):
    refresh_path()
    return shutil.which(cmd)

# ---------- Обрезка ----------

def clip(s, limit, *, head=True):
    if s is None: return s
    if not isinstance(s, str): s = str(s)
    total = len(s)
    if total <= limit: return s
    marker = f"... [{total} chars]"
    if head: return s[:limit] + marker
    return marker + s[-limit:]

def smart_clip(s, limit):
    if s is None: return s
    if not isinstance(s, str): s = str(s)
    if len(s) <= limit: return s
    half = limit // 2
    return s[:half] + f"\n\n... [ПРОПУЩЕНО {len(s) - limit} СИМВОЛОВ] ...\n\n" + s[-half:]

def _env_int(name, default):
    try: return int(os.environ.get(name, default))
    except Exception: return default

LIMIT_READ_FILE      = _env_int("GIGA_LIMIT_READ_FILE", 20000)
LIMIT_SHELL_OUT      = _env_int("GIGA_LIMIT_SHELL_OUT", 20000)
LIMIT_INSTALL_OUT    = _env_int("GIGA_LIMIT_INSTALL_OUT", 20000)
LIMIT_WEB_OUT        = _env_int("GIGA_LIMIT_WEB_OUT", 15000)
LIMIT_TOOL_PRINT     = _env_int("GIGA_LIMIT_TOOL_PRINT", 300)
LIMIT_TOOL_TO_MODEL  = _env_int("GIGA_LIMIT_TOOL_TO_MODEL", 8000)
LIMIT_RAW_MODEL      = _env_int("GIGA_LIMIT_RAW_MODEL", 500)

# ---------- Инструменты ----------

TOOL_SCHEMA = [
    {"name": "read_file", "args": {"path": "string"}},
    {"name": "write_file", "args": {"path": "string", "content": "string"}},
    {"name": "append_file", "args": {"path": "string", "content": "string"}},
    {"name": "insert_inside_class", "args": {"path": "string", "content": "string"}},
    {"name": "edit_file", "args": {"path": "string", "old": "string", "new": "string"}},
    {"name": "list_dir", "args": {"path": "string"}},
    {"name": "run_shell", "args": {"command": "string"}},
    {"name": "install_package", "args": {"package": "string"}},
    {"name": "fetch_url", "args": {"url": "string"}},
    {"name": "web_search", "args": {"query": "string"}},
    {"name": "git_diff", "args": {}},
]
TOOL_NAMES = {t["name"] for t in TOOL_SCHEMA}

SINGLE_ARG = {
    "read_file": "path",
    "list_dir": "path",
    "run_shell": "command",
    "install_package": "package",
    "fetch_url": "url",
    "web_search": "query",
}

def os_package_manager():
    if IS_WINDOWS:
        if which("winget"): return "winget"
        if which("choco"): return "choco"
        return None
    if IS_LINUX:
        if which("apt-get"): return "apt"
        if which("dnf"): return "dnf"
        if which("pacman"): return "pacman"
        return None
    if IS_MAC:
        if which("brew"): return "brew"
    return None

def normalize_content(s):
    if not isinstance(s, str): return s
    return s.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace("\\\\", "\\")

def call_tool(name, args):
    try:
        if name == "git_diff":
            if not which("git"):
                return "ERROR: git не установлен."
            r_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r_check.returncode != 0 or "true" not in r_check.stdout.lower():
                return "ERROR: текущая папка не является git-репозиторием."
            
            r_staged = subprocess.run(["git", "diff", "--cached", "--stat"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r_staged.stdout.strip():
                r = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, encoding="utf-8", errors="replace")
                header = "=== STAGED CHANGES (git diff --cached) ===\n"
            else:
                r = subprocess.run(["git", "diff"], capture_output=True, text=True, encoding="utf-8", errors="replace")
                header = "=== UNSTAGED CHANGES (git diff) ===\n"
            
            r_status = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if not r.stdout.strip() and not r_status.stdout.strip():
                return "Изменений нет. Рабочая директория чистая."
            
            out = header + (r.stdout or "") + "\n\n=== git status --short ===\n" + (r_status.stdout or "")
            return smart_clip(out, LIMIT_SHELL_OUT)

        if name == "read_file":
            with open(args["path"], "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return smart_clip(data, LIMIT_READ_FILE)

        if name == "write_file":
            path = args["path"]
            content = normalize_content(args["content"])
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: записано {len(content)} символов (создан файл {path})"

        if name == "append_file":
            path = args["path"]
            content = normalize_content(args["content"])
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"OK: файла не было, создан и записано {len(content)} символов в {path}"
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
                return f"OK: дописано {len(content)} символов в конец {path}"

        if name == "insert_inside_class":
            path = args["path"]
            content = normalize_content(args["content"])
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
            last_brace_idx = code.rfind("}")
            if last_brace_idx == -1:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("\n" + content)
                return f"OK: скобок не найдено, код добавлен в конец файла {path}"
            new_code = code[:last_brace_idx] + "\n\n    " + content.replace("\n", "\n    ") + "\n" + code[last_brace_idx:]
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_code)
            return f"OK: код успешно вставлен в конец класса {path}"

        if name == "edit_file":
            path = args["path"]
            old = normalize_content(args["old"])
            new = normalize_content(args["new"])
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if old not in text:
                return f"ERROR: строка не найдена в {path}. Прочитай файл заново."
            cnt = text.count(old)
            if cnt > 1:
                return f"ERROR: строка встречается {cnt} раз, уточни контекст."
            text = text.replace(old, new, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return f"OK: заменено в {path}"

        if name == "list_dir":
            return json.dumps(os.listdir(args["path"])[:200], ensure_ascii=False)

        if name == "run_shell":
            cmd = args["command"]
            low = cmd.lower()
            install_markers = ("apt-get install", "apt install", "yum install", "dnf install", "pacman -s", "brew install", "winget install", "choco install")
            if any(m in low for m in install_markers):
                return ("ERROR: для установки используй инструмент install_package, а не run_shell.")
            print(Fore.YELLOW + f"  ⚠ Команда: {cmd}" + Style.RESET_ALL)
            if input(Fore.WHITE + "  Выполнить? (y/n): " + Style.RESET_ALL).strip().lower() != "y":
                return "Отменено пользователем"
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="cp866" if IS_WINDOWS else "utf-8", errors="replace", timeout=300)
            out = f"exit={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
            return smart_clip(out, LIMIT_SHELL_OUT)

        if name == "install_package":
            pkg = args["package"].strip()
            pm = os_package_manager()
            if pm is None:
                return ("ERROR: на этой системе не найден пакетный менеджер (winget/choco/apt/dnf/pacman/brew). Установи пакет вручную.")
            print(Fore.YELLOW + f"  ⚠ Установка через {pm}: {pkg}" + Style.RESET_ALL)
            if input(Fore.WHITE + "  Выполнить? (y/n): " + Style.RESET_ALL).strip().lower() != "y":
                return "Отменено пользователем"
            if pm == "winget":
                cmd = ["winget", "install", "--id", pkg, "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
            elif pm == "choco":
                cmd = ["choco", "install", pkg, "-y"]
            elif pm == "apt":
                cmd = ["sudo", "apt-get", "install", "-y", pkg]
            elif pm == "dnf":
                cmd = ["sudo", "dnf", "install", "-y", pkg]
            elif pm == "pacman":
                cmd = ["sudo", "pacman", "-S", "--noconfirm", pkg]
            elif pm == "brew":
                cmd = ["brew", "install", pkg]
            else:
                return f"ERROR: неизвестный пакетный менеджер {pm}"
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="cp866" if IS_WINDOWS else "utf-8", errors="replace", timeout=900)
            refresh_path()
            out = (f"exit={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}\nPATH обновлён в текущем процессе — проверь через which/where.")
            return smart_clip(out, LIMIT_INSTALL_OUT)

        if name == "fetch_url":
            url = args["url"].strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            print(Fore.CYAN + f"  🌐 Загрузка: {url}" + Style.RESET_ALL)
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "").lower()
                if "application/json" in content_type or "text/plain" in content_type:
                    text = resp.text
                else:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for script in soup(["script", "style", "noscript", "nav", "footer", "header"]):
                        script.decompose()
                    text = soup.get_text(separator="\n")
                    lines = (line.strip() for line in text.splitlines())
                    text = "\n".join(line for line in lines if line)
                
                if len(text) > 10000:
                    with open("temp_page.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                    return (f"Страница загружена, но она слишком большая ({len(text)} символов). "
                            f"Текст сохранен в файл 'temp_page.txt'. "
                            f"Используй read_file('temp_page.txt'), чтобы прочитать его.")
                else:
                    return text
            except Exception as e:
                return f"ERROR: не удалось загрузить {url}. Причина: {e}"

        if name == "web_search":
            query = args["query"].strip()
            print(Fore.CYAN + f"  🔍 Поиск: {query}" + Style.RESET_ALL)
            url = "https://lite.duckduckgo.com/lite/"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            try:
                resp = requests.post(url, data={"q": query}, headers=headers, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                links = soup.find_all("a", class_="result-link")
                for a in links[:5]:
                    title = a.get_text(strip=True)
                    href = a.get("href", "")
                    snippet = ""
                    tr = a.find_parent("tr")
                    if tr:
                        next_tr = tr.find_next_sibling("tr")
                        if next_tr: snippet = next_tr.get_text(strip=True)
                    results.append(f"Title: {title}\nURL: {href}\nSnippet: {snippet}\n")
                if not results: return "Ничего не найдено или поиск заблокирован."
                return "\n---\n".join(results)
            except Exception as e:
                return f"ERROR: поиск не удался. Причина: {e}"

    except subprocess.TimeoutExpired:
        return "ERROR: превышен лимит времени"
    except Exception as e:
        return f"ERROR: {e}"
    return "unknown tool"

# ---------- Парсер ----------

def parse_model_json(text):
    if not text: return []
    s = text.strip()
    s = re.sub(r"```[\w]*", "", s).replace("```", "")
    results = []
    for m in re.finditer(r"\{", s):
        start = m.start()
        for end in range(start + 1, len(s) + 1):
            if s[end - 1] != "}": continue
            chunk = s[start:end]
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    if "tool" in obj and obj["tool"] in TOOL_NAMES:
                        results.append(("tool", obj["tool"], obj.get("args", {}) or {}))
                        break
                    if len(obj) == 1:
                        (k, v), = obj.items()
                        if k in TOOL_NAMES and isinstance(v, dict):
                            results.append(("tool", k, v))
                            break
                        if k in TOOL_NAMES and isinstance(v, str) and k in SINGLE_ARG:
                            results.append(("tool", k, {SINGLE_ARG[k]: v}))
                            break
                    if "answer" in obj:
                        results.append(("answer", str(obj["answer"]), None))
                        break
            except Exception:
                pass

            match_flat = re.search(r'\{\s*"?(write_file|append_file|insert_inside_class)"?\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"(.*)', chunk, re.DOTALL)
            if match_flat:
                tool_name = match_flat.group(1)
                file_path = match_flat.group(2)
                raw_content = match_flat.group(3)
                if raw_content.endswith('"}'): raw_content = raw_content[:-2]
                elif raw_content.endswith('"'): raw_content = raw_content[:-1]
                elif raw_content.endswith('"}}'): raw_content = raw_content[:-3]
                else: raw_content = raw_content.rstrip('}"')
                raw_content = raw_content.replace('\\n', '\n').replace('\\"', '"')
                results.append(("tool", tool_name, {"path": file_path, "content": raw_content}))
                break

            match_nested = re.search(r'"tool"\s*:\s*"(write_file|append_file|insert_inside_class)"\s*,\s*"args"\s*:\s*\{\s*"path"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"(.*)', chunk, re.DOTALL)
            if match_nested:
                tool_name = match_nested.group(1)
                file_path = match_nested.group(2)
                raw_content = match_nested.group(3)
                if not raw_content.endswith('"'): raw_content += '"'
                if not chunk.endswith("}}"): chunk += "}}"
                try:
                    fixed_obj = json.loads(chunk)
                    if "args" in fixed_obj and "content" in fixed_obj["args"]:
                        results.append(("tool", tool_name, fixed_obj["args"]))
                        break
                except Exception:
                    pass
                if raw_content.startswith('"'): raw_content = raw_content[1:]
                raw_content = raw_content.replace('"}}', '').replace('"', '').replace('\\n', '\n')
                results.append(("tool", tool_name, {"path": file_path, "content": raw_content}))
                break
    return results

# ---------- Ввод ----------

TRIPLE = '"' * 3

def read_user_input():
    if not sys.stdin.isatty():
        try: data = sys.stdin.read()
        except Exception: return None
        return data.strip("\n") if data else None
    try:
        first = input(Fore.WHITE + "Вы: " + Style.RESET_ALL)
    except (EOFError, KeyboardInterrupt):
        return None
    first_stripped = first.strip()

    if first_stripped == ":paste":
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", text=True)
        os.close(fd)
        editor = "notepad" if IS_WINDOWS else ("nano" if IS_LINUX else "open -e")
        try:
            if IS_WINDOWS: subprocess.run(["notepad.exe", tmp_path], check=False)
            else: subprocess.run([editor, tmp_path], check=False)
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        finally:
            try: os.remove(tmp_path)
            except Exception: pass
        return text.strip("\n")

    if first_stripped in (TRIPLE, ":end"):
        if first_stripped == TRIPLE:
            print(Fore.CYAN + "  [многострочный ввод — заверши строкой " + TRIPLE + " или :end]" + Style.RESET_ALL)
            buf = []
            while True:
                try: line = input()
                except (EOFError, KeyboardInterrupt): break
                if line.strip() in (TRIPLE, ":end"): break
                buf.append(line)
            return "\n".join(buf)
        return ""

    if first.rstrip().endswith("\\"):
        buf = [first.rstrip()[:-1].rstrip("\n")]
        print(Fore.CYAN + "  [продолжение ввода — заверши строкой '\\']" + Style.RESET_ALL)
        while True:
            try: line = input()
            except (EOFError, KeyboardInterrupt): break
            if line.strip() == "\\": break
            buf.append(line)
        return "\n".join(buf)
    return first

# ---------- Main ----------

def main():
    giga = GigaChat(verify_ssl_certs=False, model="GigaChat-2-Max", timeout=60)
    tool_doc = "\n".join(f"- {t['name']}({', '.join(f'{k}: {v}' for k, v in t['args'].items())})" for t in TOOL_SCHEMA)

    system_prompt = (
        f"Ты — CLI-агент. ОС: {platform.system()}. Пакетный менеджер: {os_package_manager() or 'не найден'}. "
        "Ты ОБЯЗАН отвечать СТРОГО одним JSON-объектом, без текста вокруг. Форматы:\n"
        '  {"tool": "<имя>", "args": {...}}\n'
        '  {"answer": "<текст>"}\n\n'
        "Инструменты:\n" + tool_doc + "\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Ты НЕ даёшь инструкции пользователю. Ты ВЫПОЛНЯЕШЬ действия через tool.\n"
        "- Не утверждай, что что-то сделано, пока не получил результат инструмента.\n"
        "- Один JSON-объект за раз.\n"
        "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать тройные кавычки (\"\"\").\n\n"
        "СТРАТЕГИЯ НАПИСАНИЯ КОДА (СБОРКА КЛАССА):\n"
        "- Если код не помещается в один ответ, разбей его на логические блоки.\n"
        "- Шаг 1: Вызови write_file, чтобы создать класс с основной функцией. Обязательно поставь закрывающую скобку класса в конце.\n"
        "- Шаг 2: Если нужны вспомогательные функции, вызови insert_inside_class с их кодом.\n"
        "- Шаг 3: Когда все функции написаны, ответь {\"answer\": \"Готово\"}.\n\n"
        "ОБНОВЛЕНИЕ ДОКУМЕНТАЦИИ (README):\n"
        "- Если пользователь просит обновить README, сначала вызови read_file('README.md'), чтобы прочитать текущий текст.\n"
        "- Затем вызови read_file('windows_cli_giga_agent.py') или list_dir('.'), чтобы понять актуальные функции.\n"
        "- После этого вызови write_file('README.md', ...) с обновлённым текстом. Не пересказывай изменения, а ПЕРЕЗАПИШИ файл.\n\n"
        "РАБОТА С СЕТЬЮ:\n"
        "- Используй web_search, чтобы найти ссылки, и fetch_url, чтобы прочитать страницу.\n"
        "- Если fetch_url вернул сообщение, что текст сохранён в 'temp_page.txt', "
        "используй read_file('temp_page.txt'), чтобы прочитать его.\n\n"
        "РАБОТА С GIT:\n"
        "- git_diff вызывай ТОЛЬКО когда пользователь явно просит 'коммит', 'commit', 'пуш', 'push', 'commit message'.\n"
        "- НЕ вызывай git_diff, если пользователь просто просит обновить README или что-то изменить в коде.\n"
        "- Если просят коммит — сначала git_diff, потом ответ с текстом коммита на русском языке.\n\n"
        "ЖЁСТКИЕ ПРАВИЛА ПРОТИВ ОТКАЗОВ:\n"
        "- ЗАПРЕЩЕНО отвечать 'уточни', 'предоставь', 'не могу', 'не хватает информации'.\n"
        "- По умолчанию отвечай на русском языке.\n"
    )

    history = [Messages(role=MessagesRole.SYSTEM, content=system_prompt)]
    print(Fore.CYAN + LOGO + Style.RESET_ALL)
    print(Fore.WHITE + "GigaChat-агент. 'exit' для выхода.\nДля вставки текста набери :paste (откроется Блокнот).\n" + Style.RESET_ALL)

    REFUSAL_MARKERS = ("уточни", "предоставь", "не могу", "не хватает", "какая задача", "дай код", "опиши", "какую задачу", "необходимо знать", "требуется информация")

    while True:
        try:
            user = read_user_input()
            if user is None: break
            if not user.strip(): continue
            if user.strip().lower() in ("exit", "quit"): break

            history.append(Messages(role=MessagesRole.USER, content=user))
            steps = 0
            stop_reason = None
            
            while True:
                steps += 1
                if steps > 50:
                    stop_reason = "лимит шагов"
                    break

                try:
                    resp = giga.chat(Chat(messages=history, max_tokens=2000))
                except KeyboardInterrupt:
                    print(Fore.RED + "\n[Запрос к GigaChat прерван]" + Style.RESET_ALL)
                    stop_reason = "прервано"
                    break
                except Exception as e:
                    print(Fore.RED + f"[ошибка API] {e}\n" + Style.RESET_ALL)
                    stop_reason = "ошибка API"
                    break

                msg = resp.choices[0].message
                raw = msg.content or ""
                history.append(msg)
                parsed = parse_model_json(raw)

                if not parsed:
                    print(Fore.RED + f"  [raw model output] {clip(raw, LIMIT_RAW_MODEL)}" + Style.RESET_ALL)
                    history.append(Messages(role=MessagesRole.USER, content="Ты нарушил формат. Ответь ТОЛЬКО JSON-объектом. Никаких тройных кавычек."))
                    if steps >= 3:
                        stop_reason = "модель не выдала валидный JSON"
                        break
                    continue

                if parsed and parsed[0][0] == "answer":
                    ans_text = parsed[0][1].lower()
                    if any(m in ans_text for m in REFUSAL_MARKERS):
                        print(Fore.RED + f"  [refusal blocked] {clip(parsed[0][1], LIMIT_RAW_MODEL)}" + Style.RESET_ALL)
                        history.append(Messages(role=MessagesRole.USER, content="ОТКАЗ ЗАПРЕЩЁН. ВЫПОЛНИ действие через tool."))
                        if steps >= 3:
                            stop_reason = "модель ушла в отказы"
                            break
                        continue

                answered = False
                did_tool = False
                wrote_chunk = False
                last_file_path = ""

                for item in parsed:
                    if item[0] == "answer":
                        print(Fore.GREEN + f"\nGigaChat: {item[1]}\n" + Style.RESET_ALL)
                        answered = True
                        break

                    _, name, args = item
                    print(Fore.YELLOW + f"  [tool] {name} {args}" + Style.RESET_ALL)
                    result = call_tool(name, args)
                    bad = any(x in str(result) for x in ("ERROR", "SyntaxError", "Traceback"))
                    color = Fore.RED if bad else Fore.GREEN
                    print(color + f"  [result] {clip(result, LIMIT_TOOL_PRINT)}" + Style.RESET_ALL)
                    
                    history.append(Messages(role=MessagesRole.USER, content=f"Результат {name}: {smart_clip(result, LIMIT_TOOL_TO_MODEL)}\nПродолжай."))
                    did_tool = True

                    if name in ("write_file", "append_file", "insert_inside_class"):
                        wrote_chunk = True
                        last_file_path = args.get("path", "")

                if answered:
                    break
                
                if wrote_chunk:
                    print(Fore.MAGENTA + f"  [конвейер] Ожидание продолжения..." + Style.RESET_ALL)
                    history.append(Messages(role=MessagesRole.USER, content=f"Файл {last_file_path} сохранен. Если не хватает функций, добавь их через insert_inside_class. Если всё готово, ответь 'Готово'."))
                    continue
                
                if did_tool:
                    continue
                break

            if stop_reason:
                print(Fore.RED + f"GigaChat: [{stop_reason}]\n" + Style.RESET_ALL)
                
        except KeyboardInterrupt:
            print(Fore.RED + "\nВыход..." + Style.RESET_ALL)
            break
        except Exception as e:
            print(Fore.RED + f"\n[Критическая ошибка] {e}" + Style.RESET_ALL)

if __name__ == "__main__":
    main()