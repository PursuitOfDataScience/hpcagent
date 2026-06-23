import re
import select
import shutil
import sys
import threading
import time

PTK_AVAILABLE = False
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.enums import EditingMode
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea
    PTK_AVAILABLE = True
except ImportError:
    pass


HISTORY = InMemoryHistory() if PTK_AVAILABLE else None


class Colors:
    PINK = '\033[38;2;255;0;128m'
    CYAN = '\033[38;2;0;255;255m'
    MAGENTA = '\033[38;2;191;0;255m'
    YELLOW = '\033[38;2;255;255;0m'
    GREEN = '\033[38;2;0;255;128m'
    ORANGE = '\033[38;2;255;128;0m'
    RED = '\033[38;2;255;0;0m'
    BLUE = '\033[38;2;0;128;255m'
    PURPLE = '\033[38;2;128;0;255m'
    WHITE = '\033[38;2;255;255;255m'
    GRAY = '\033[38;2;128;128;128m'
    DARK_GRAY = '\033[38;2;64;64;64m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    RESET = '\033[0m'
    TEAL = CYAN
    CORAL = PINK
    MINT = GREEN
    STATUS_OK = GREEN
    STATUS_WARN = YELLOW
    STATUS_ERR = RED
    UNDERLINE = '\033[4m'

    G1 = '\033[38;2;255;0;128m'
    G2 = '\033[38;2;255;0;191m'
    G3 = '\033[38;2;191;0;255m'
    G4 = '\033[38;2;128;0;255m'
    G5 = '\033[38;2;0;128;255m'
    G6 = '\033[38;2;0;255;255m'


c = Colors()


class ThinkingAnim:
    def __init__(self):
        self.running = False
        self.thread = None
        self.msg = "thinking"
        self.highlights = [
            '\033[38;2;255;0;128m',
            '\033[38;2;255;128;255m',
            '\033[38;2;255;255;255m',
            '\033[38;2;128;255;255m',
            '\033[38;2;0;255;255m',
        ]

    def _frame(self, idx):
        chars = list(self.msg)
        w = len(self.highlights)
        lead = idx % (len(chars) + w)
        center = lead - w // 2
        out = []
        for i, ch in enumerate(chars):
            off = i - center
            if 0 <= off < w:
                out.append(f"{c.BOLD}{self.highlights[off]}{ch}{c.RESET}")
            else:
                out.append(f"{c.GRAY}{ch}{c.RESET}")
        return "".join(out)

    def _run(self):
        i = 0
        while self.running:
            print(f"\r{self._frame(i)}\033[K", end="", flush=True)
            time.sleep(0.08)
            i += 1

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)
        print(f"\r{' ' * 40}\r", end="", flush=True)


def stop_animation_if_running(animation):
    if animation and animation.running:
        animation.stop()


def fmt_inline(text):
    segments = []
    def stash(m):
        segments.append(m.group(1))
        return f"\x00S{len(segments)-1}\x00"
    def restore(t):
        for i, s in enumerate(segments):
            t = t.replace(f"\x00S{i}\x00", f"{c.CYAN}{s}{c.RESET}")
        return t
    t = re.sub(r'`([^`]+)`', stash, text)
    t = re.sub(r'\*\*(.+?)\*\*', lambda m: f'{c.BOLD}{m.group(1)}{c.RESET}', t)
    t = re.sub(r'__(.+?)__', lambda m: f'{c.BOLD}{m.group(1)}{c.RESET}', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', lambda m: f'{c.DIM}{m.group(1)}{c.RESET}', t)
    t = re.sub(r'(?<![\w/])_([^_\n/]+)_(?![\w/])', lambda m: f'{c.DIM}{m.group(1)}{c.RESET}', t)
    return restore(t)


def strip_md(text):
    segments = []
    def stash(m):
        segments.append(m.group(1))
        return f"\x00S{len(segments)-1}\x00"
    def restore(t):
        for i, s in enumerate(segments):
            t = t.replace(f"\x00S{i}\x00", s)
        return t
    t = re.sub(r'`([^`]+)`', stash, text)
    for pat in [r'\*\*(.+?)\*\*', r'__(.+?)__']:
        t = re.sub(pat, r'\1', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', t)
    t = re.sub(r'(?<![\w/])_([^_\n/]+)_(?![\w/])', r'\1', t)
    return restore(t)


def fmt_header(line):
    m = re.match(r'^(#{1,6})\s+(.*)$', line)
    if not m:
        return None
    lvl = len(m.group(1))
    text = fmt_inline(m.group(2).strip())
    palette = {1: c.PINK, 2: c.CYAN, 3: c.MAGENTA, 4: c.GREEN, 5: c.BLUE, 6: c.GRAY}
    return f"\n{c.BOLD}{palette.get(lvl, c.CYAN)}{text}{c.RESET}"


def fmt_table(rows):
    if not rows:
        return ""
    nc = max(len(r) for r in rows)
    cw = [0] * nc
    for r in rows:
        for i, cell in enumerate(r):
            if i < nc:
                cw[i] = max(cw[i], len(strip_md(cell)))
    lines = []
    if len(rows) == 1:
        parts = []
        for i, cell in enumerate(rows[0]):
            f = fmt_inline(cell)
            pl = len(strip_md(cell))
            parts.append(f + " " * (cw[i] - pl + 2))
        lines.append("".join(parts))
        return "\n".join(lines)
    header = rows[0]
    parts = []
    for i, cell in enumerate(header):
        f = fmt_inline(cell)
        pl = len(strip_md(cell))
        parts.append(f"{c.BOLD}{c.PINK}{f}{c.RESET}" + " " * (cw[i] - pl + 2))
    lines.append("".join(parts))
    sep = c.GRAY + "─" * (sum(cw) + 2 * nc) + c.RESET
    lines.append(sep)
    for row in rows[1:]:
        parts = []
        for i, cell in enumerate(row):
            if i < len(row):
                f = fmt_inline(cell)
                pl = len(strip_md(cell))
                w = cw[i] if i < len(cw) else 0
                parts.append(f + " " * (w - pl + 2))
        lines.append("".join(parts))
    return "\n".join(lines)


class StreamRenderer:
    def __init__(self):
        self.buf = ""
        self.in_code = False
        self.code_lang = ""
        self.code_lines = []
        self.in_table = False
        self.table_rows = []

    def _render_line(self, line):
        if line.strip().startswith('```'):
            if not self.in_code:
                self.in_code = True
                self.code_lang = line.strip()[3:].strip()
                self.code_lines = []
                return None
            else:
                self.in_code = False
                out = []
                if self.code_lines:
                    label = self.code_lang or "code"
                    out.append(f"{c.GRAY}╭── {label} ──{c.RESET}")
                    for cl in self.code_lines:
                        out.append(f"{c.CYAN}  {cl}{c.RESET}")
                    out.append(f"{c.GRAY}╰{'─' * 8}{c.RESET}")
                self.code_lines = []
                self.code_lang = ""
                return "\n".join(out) if out else None
        if self.in_code:
            self.code_lines.append(line)
            return None
        if re.match(r'^[\s]*[-*_]{3,}[\s]*$', line):
            return None
        h = fmt_header(line)
        if h:
            return h
        if '|' in line and line.strip().startswith('|'):
            s = line.strip()
            if re.match(r'^\|[\s\-:]+\|', s):
                return None
            cells = [c.strip() for c in s.split('|')[1:-1]]
            if not self.in_table:
                self.in_table = True
                self.table_rows = []
            self.table_rows.append(cells)
            return None
        else:
            result = ""
            if self.in_table and self.table_rows:
                result = fmt_table(self.table_rows) + "\n"
                self.table_rows = []
                self.in_table = False
            bq = re.match(r'^(\s*)((?:>\s*)+)(.*)$', line)
            nl = re.match(r'^(\s*)(\d+)\.\s+(.*)$', line)
            bl = re.match(r'^(\s*)[-•]\s+(.*)$', line)
            sb = re.match(r'^(\s*)\*\s+(?!\*)(.*)$', line)
            if bq:
                depth = bq.group(2).count('>')
                prefix = f"{c.DARK_GRAY}{'▎ ' * depth}{c.GRAY}"
                result += f"{bq.group(1)}{prefix} {fmt_inline(bq.group(3))}"
            elif nl:
                result += f"{nl.group(1)}{c.CYAN}{c.BOLD}{nl.group(2)}.{c.RESET} {fmt_inline(nl.group(3))}"
            elif bl:
                result += f"{bl.group(1)}{c.PINK}•{c.RESET} {fmt_inline(bl.group(2))}"
            elif sb:
                result += f"{sb.group(1)}{c.PINK}•{c.RESET} {fmt_inline(sb.group(2))}"
            else:
                result += fmt_inline(line)
            return result

    def feed(self, text):
        self.buf += text
        while '\n' in self.buf:
            idx = self.buf.index('\n')
            line = self.buf[:idx]
            self.buf = self.buf[idx + 1:]
            rendered = self._render_line(line)
            if rendered is not None:
                print(rendered, flush=True)

    def process_chunk(self, chunk):
        self.feed(chunk)

    def flush(self):
        if self.buf:
            r = self._render_line(self.buf)
            if r is not None:
                print(r, end="", flush=True)
            self.buf = ""
        if self.in_code and self.code_lines:
            label = self.code_lang or "code"
            print(f"\n{c.GRAY}╭── {label} ──{c.RESET}")
            for cl in self.code_lines:
                print(f"{c.CYAN}  {cl}{c.RESET}")
            print(f"{c.GRAY}╰{'─' * 8}{c.RESET}", end="", flush=True)
            self.code_lines = []
            self.in_code = False
        if self.in_table and self.table_rows:
            print(fmt_table(self.table_rows), end="", flush=True)
            self.table_rows = []
            self.in_table = False


def tool_status(name, status="running"):
    if status == "running":
        print(f"\r{c.GRAY}⚡ {name}...\033[K{c.RESET}", end="", flush=True)
    elif status == "success":
        print(f"\r{c.GREEN}✓{c.RESET} {c.GRAY}{name}\033[K{c.RESET}", end="", flush=True)
    elif status == "error":
        print(f"\r{c.RED}✗ {name}\033[K{c.RESET}", end="", flush=True)


def print_assistant_response_text(text, marker=True):
    if not text:
        return
    if marker:
        print(f"{c.TEAL}{c.BOLD}●{c.RESET} ", end="", flush=True)
    r = StreamRenderer()
    r.feed(text)
    r.flush()
    print()


def _read_rich_input(prompt_text="▸ "):
    global PTK_AVAILABLE
    if not PTK_AVAILABLE or not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        w = max(28, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)
        ta = TextArea(
            multiline=True, wrap_lines=True, dont_extend_height=True,
            history=HISTORY, auto_suggest=AutoSuggestFromHistory(),
            prompt=[("class:text-area.prompt", prompt_text)],
        )
        style = Style.from_dict({
            "frame": "bg:#0a0a0a",
            "frame.border": "#ff0080 bg:#0a0a0a",
            "text-area": "bg:#0f0f0f #e0e0e0",
            "text-area.prompt": "bold",
            "suggestion": "#00ff80 bg:#0f0f0f",
            "auto-suggestion": "#404040 italic",
        })
        frame = Frame(ta, width=w)
        layout = HSplit([frame])
        kb = KeyBindings()
        @kb.add("enter")
        def _submit(ev):
            if (ta.text or "").strip():
                ev.app.exit(result=ta.text)
        @kb.add("escape", "enter")
        def _nl(ev):
            ta.buffer.insert_text("\n")
        app = Application(
            layout=Layout(layout, focused_element=ta.window),
            key_bindings=kb, editing_mode=EditingMode.EMACS,
            mouse_support=False, full_screen=False, erase_when_done=True,
            style=style,
        )
        text = app.run()
        if text and HISTORY is not None:
            HISTORY.append_string(text)
        return text
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        PTK_AVAILABLE = False
        return None


def _read_fallback(prompt):
    first = input(f"{c.CYAN}{prompt}")
    lines = [first]
    extra = False
    idle = 0
    while True:
        timeout = 0.12 if not extra else 0.05
        if not select.select([sys.stdin], [], [], timeout)[0]:
            if not extra or idle >= 1:
                break
            idle += 1
            continue
        idle = 0
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if not line:
            break
        s = line.rstrip('\n\r')
        print(f"{c.CYAN}{s}")
        lines.append(s)
        extra = True
    return "\n".join(lines)


def read_input(prompt):
    r = _read_rich_input()
    if r is not None:
        return r
    return _read_fallback(prompt)


def print_banner(banner_lines, subtitle="", model="", effort="", animate=True):
    base_rgb = [
        (255, 0, 128), (255, 0, 191), (191, 0, 255),
        (128, 0, 255), (0, 128, 255), (0, 255, 255),
    ]
    def _esc(rgb):
        return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    def _brighten(rgb, t):
        return (min(255, int(rgb[0] + (255 - rgb[0]) * t)),
                min(255, int(rgb[1] + (255 - rgb[1]) * t)),
                min(255, int(rgb[2] + (255 - rgb[2]) * t)))
    grad = [_esc(c) for c in base_rgb]
    print()
    for i, line in enumerate(banner_lines):
        print(f"{grad[i]}{c.BOLD}{line}{c.RESET}")
    print()
    if animate:
        n = len(banner_lines)
        print(f"\033[{n + 1}A", end="")
        print("\033[s", end="")
        for _ in range(2):
            crest = -2.0
            while crest <= n + 1.0:
                print("\033[u", end="")
                for i, line in enumerate(banner_lines):
                    intensity = max(0.0, 1.0 - abs(i - crest) / 2.0) * 0.8
                    color = _esc(_brighten(base_rgb[i], intensity))
                    print(f"\033[2K{color}{c.BOLD}{line}{c.RESET}\n", end="")
                print("\033[2K\n", end="")
                time.sleep(0.04)
                crest += 0.5
        print("\033[u", end="")
        for i, line in enumerate(banner_lines):
            print(f"\033[2K{grad[i]}{c.BOLD}{line}{c.RESET}")
        print()
    if subtitle:
        print(f"  {c.CYAN}{subtitle}{c.RESET}")
    if model:
        model_line = f"  {c.GRAY}Model: {c.CYAN}{model}{c.RESET}"
        if effort:
            model_line += f"  {c.GRAY}· effort: {c.YELLOW}{effort}{c.RESET}"
        print(model_line)
    print(f"  {c.GRAY}Type {c.GREEN}/help{c.GRAY} for commands, {c.RED}/exit{c.GRAY} to leave.{c.RESET}")
    print()
