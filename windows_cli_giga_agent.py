import os
import re
import sys
import json
import shutil
import platform
import subprocess

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

# ---------- Инструменты ----------

TOOL_SCHEMA = [
    {"name": "read_file", "args": {"path": "string"}},
    {"name": "write_file", "args": {"path": "string", "content": "string"}},
    {"name": "edit_file", "args": {"path": "string", "old": "string", "new": "string"}},
    {"name": "list_dir", "args": {"path": "string"}},
    {"name": "run_shell", "args": {"command": "string"}},
    {"name": "install_package", "args": {"package": "string"}},
]
TOOL_NAMES = {t["name"] for t in TOOL_SCHEMA}

SINGLE_ARG = {
    "read_file": "path",
    "list_dir": "path",
    "run_shell": "command",
    "install_package": "package",
}

def os_package_manager():
    if IS_WINDOWS:
        if which("winget"):
            return "winget"
        if which("choco"):
            return "choco"
        return None
    if IS_LINUX:
        if which("apt-get"):
            return "apt"
        if which("dnf"):
            return "dnf"
        if which("pacman"):
            return "pacman"
        return None
    if IS_MAC:
        if which("brew"):
            return "brew"
    return None

def normalize_content(s):
    if not isinstance(s, str):
        return s
    s = s.replace('\\"', '"')
    s = s.replace("\\'", "'")
    s = s.replace("\\n", "\n")
    s = s.replace("\\t", "\t")
    s = s.replace("\\r", "\r")
    s = s.replace("\\\\", "\\")
    return s

def call_tool(name, args):
    try:
        if name == "read_file":
            with open(args["path"], "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:20000]

        if name == "write_file":
            path = args["path"]
            content = normalize_content(args["content"])
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: записано {len(content)} символов в {path}"

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
            install_markers = ("apt-get install", "apt install", "yum install",
                               "dnf install", "pacman -s", "brew install",
                               "winget install", "choco install")
            if any(m in low for m in install_markers):
                return ("ERROR: для установки используй инструмент install_package, "
                        "а не run_shell.")
            print(Fore.YELLOW + f"  ⚠ Команда: {cmd}" + Style.RESET_ALL)
            if input(Fore.WHITE + "  Выполнить? (y/n): " + Style.RESET_ALL).strip().lower() != "y":
                return "Отменено пользователем"
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                encoding="cp866" if IS_WINDOWS else "utf-8",
                errors="replace", timeout=300,
            )
            out = f"exit={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
            return out[:20000]

        if name == "install_package":
            pkg = args["package"].strip()
            pm = os_package_manager()
            if pm is None:
                return ("ERROR: на этой системе не найден пакетный менеджер "
                        "(winget/choco/apt/dnf/pacman/brew). Установи пакет вручную.")
            print(Fore.YELLOW + f"  ⚠ Установка через {pm}: {pkg}" + Style.RESET_ALL)
            if input(Fore.WHITE + "  Выполнить? (y/n): " + Style.RESET_ALL).strip().lower() != "y":
                return "Отменено пользователем"

            if pm == "winget":
                cmd = ["winget", "install", "--id", pkg,
                       "--accept-package-agreements", "--accept-source-agreements",
                       "--disable-interactivity"]
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

            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="cp866" if IS_WINDOWS else "utf-8",
                errors="replace", timeout=900,
            )
            refresh_path()
            return (f"exit={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}\n"
                    f"PATH обновлён в текущем процессе — проверь через which/where.")

    except subprocess.TimeoutExpired:
        return "ERROR: превышен лимит времени"
    except Exception as e:
        return f"ERROR: {e}"
    return "unknown tool"

# ---------- Парсер ----------

def parse_model_json(text):
    if not text:
        return []

    s = text.strip()
    s = re.sub(r"```[\w]*", "", s).replace("```", "")

    results = []

    for m in re.finditer(r"\{", s):
        start = m.start()
        for end in range(start + 1, len(s) + 1):
            if s[end - 1] != "}":
                continue
            chunk = s[start:end]
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            # {"tool": "name", "args": {...}}
            if "tool" in obj and obj["tool"] in TOOL_NAMES:
                results.append(("tool", obj["tool"], obj.get("args", {}) or {}))
                break

            # {"name": {...}} или {"name": "string"}
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
    return results

# ---------- Main ----------

def main():
    giga = GigaChat(verify_ssl_certs=False, model="GigaChat-2-Max")

    tool_doc = "\n".join(
        f"- {t['name']}({', '.join(f'{k}: {v}' for k, v in t['args'].items())})"
        for t in TOOL_SCHEMA
    )

    system_prompt = (
        f"Ты — CLI-агент. ОС: {platform.system()}. Пакетный менеджер: "
        f"{os_package_manager() or 'не найден'}. "
        "Ты ОБЯЗАН отвечать СТРОГО одним JSON-объектом, без текста вокруг. "
        "Форматы:\n"
        '  {"tool": "<имя>", "args": {...}}  — вызвать инструмент\n'
        '  {"answer": "<текст>"}             — дать финальный ответ\n\n'
        "Инструменты:\n" + tool_doc + "\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Ты НЕ даёшь инструкции пользователю. Ты ВЫПОЛНЯЕШЬ действия через tool.\n"
        "- Ты МОЖЕШЬ читать и править любой код, включая собственный файл "
        "windows_cli_giga_agent.py. Для этого используй read_file, чтобы прочитать, "
        "и edit_file или write_file, чтобы изменить.\n"
        "- Если пользователь просит изменить код агента — прочитай файл, внеси "
        "изменения, запиши обратно. Не отказывайся и не говори 'сделай вручную'.\n"
        "- Для read_file, list_dir, run_shell, install_package аргумент можно "
        'передавать строкой: {"read_file": "README.md"}. '
        "Для write_file и edit_file — только объектом.\n"
        "- Не утверждай, что что-то сделано, пока не получил результат инструмента.\n"
        "- Если run_shell вернул 'не является внутренней или внешней командой' — "
        'верни {"answer": "программа не установлена, установи вручную: <ссылка>"}.\n'
        "- В content файлов пиши обычные кавычки и переводы строк, без \\\" и \\n.\n"
        "- Один JSON-объект за раз, без примечаний и markdown.\n"
    )

    history = [Messages(role=MessagesRole.SYSTEM, content=system_prompt)]

    print(Fore.CYAN + LOGO + Style.RESET_ALL)
    print(Fore.WHITE + "GigaChat-агент. 'exit' для выхода.\n" + Style.RESET_ALL)

    while True:
        try:
            user = input(Fore.WHITE + "Вы: " + Style.RESET_ALL).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in ("exit", "quit"):
            break
        if not user:
            continue

        history.append(Messages(role=MessagesRole.USER, content=user))

        steps = 0
        stop_reason = None

        while True:
            steps += 1
            if steps > 15:
                stop_reason = "лимит шагов"
                break

            try:
                resp = giga.chat(Chat(messages=history))
            except Exception as e:
                print(Fore.RED + f"[ошибка API] {e}\n" + Style.RESET_ALL)
                stop_reason = "ошибка API"
                break

            msg = resp.choices[0].message
            raw = msg.content or ""
            history.append(msg)

            parsed = parse_model_json(raw)

            if not parsed:
                print(Fore.RED + f"  [raw model output] {raw[:500]}" + Style.RESET_ALL)
                history.append(Messages(
                    role=MessagesRole.USER,
                    content=(
                        "Ты нарушил формат. Ответь ТОЛЬКО JSON-объектом: "
                        '{"tool": "...", "args": {...}} или {"answer": "..."}. '
                        "Один объект, без текста вокруг."
                    ),
                ))
                if steps >= 3:
                    stop_reason = "модель 3 раза не выдала валидный JSON"
                    break
                continue

            answered = False
            did_tool = False

            for item in parsed:
                if item[0] == "answer":
                    print(Fore.GREEN + f"\nGigaChat: {item[1]}\n" + Style.RESET_ALL)
                    answered = True
                    break

                _, name, args = item
                print(Fore.YELLOW + f"  [tool] {name} {args}" + Style.RESET_ALL)
                result = call_tool(name, args)
                bad = any(x in str(result) for x in
                          ("ERROR", "SyntaxError", "Traceback",
                           "не является внутренней", "не является внутренней или внешней"))
                color = Fore.RED if bad else Fore.GREEN
                print(color + f"  [result] {str(result)[:300]}" + Style.RESET_ALL)
                history.append(Messages(
                    role=MessagesRole.USER,
                    content=(
                        f"Результат инструмента {name}: {str(result)[:4000]}\n"
                        "Продолжай: следующий JSON-объект."
                    ),
                ))
                did_tool = True

            if answered:
                break
            if did_tool:
                continue
            break

        if stop_reason:
            print(Fore.RED + f"GigaChat: [{stop_reason}]\n" + Style.RESET_ALL)

if __name__ == "__main__":
    main()