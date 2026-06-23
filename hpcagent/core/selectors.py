import sys
import termios
import tty

_SELECTION_CANCELLED = object()
_GO_BACK = object()

_MENU_ROW_HEX = ['#7dd3fc', '#86efac', '#fda4af', '#fcd34d', '#c4b5fd', '#5eead4', '#f9a8d4', '#fdba74']


def _menu_row_ansi(i):
    h = _MENU_ROW_HEX[i % len(_MENU_ROW_HEX)].lstrip('#')
    return f'\033[38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m'


def _hex_to_ansi(hexstr):
    h = hexstr.lstrip('#')
    return f'\033[38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m'


def interactive_select(options, header="Select option", current_label="", default_idx=0,
                        display_fn=None, selected_color=None, unselected_color=None,
                        clear_on_confirm=False):
    from hpcagent.core.ui import c

    if display_fn is None:
        display_fn = str
    if selected_color is None:
        selected_color = f"{c.BOLD}"
    idx = default_idx % max(1, len(options))
    n = len(options) + 2

    def draw(up=False):
        if up:
            sys.stdout.write(f"\033[{n}F")
        sys.stdout.write(f"\r\033[2K{c.BOLD}{c.PINK}\u203a {header}{c.RESET}\n")
        for i, opt in enumerate(options):
            rc = _menu_row_ansi(i)
            label = display_fn(opt) if display_fn else str(opt)
            if i == idx:
                sys.stdout.write(f"\r\033[2K  {selected_color}{rc}{c.BOLD}\u2038 {label}{c.RESET}\n")
            else:
                sys.stdout.write(f"\r\033[2K    {rc}{label}{c.RESET}\n")
        sys.stdout.write(f"\r\033[2K{c.GRAY}Current: {current_label}  {c.DIM}\u2191\u2193 Enter q{c.RESET}\n")
        sys.stdout.flush()

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    draw()
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                fd = sys.stdin.fileno()
                a = list(termios.tcgetattr(fd))
                cc = list(a[6])
                cc[termios.VMIN] = 0; cc[termios.VTIME] = 3
                a[6] = cc
                termios.tcsetattr(fd, termios.TCSANOW, a)
                ch2 = sys.stdin.read(1)
                cc[termios.VMIN] = 1; cc[termios.VTIME] = 0
                a[6] = cc
                termios.tcsetattr(fd, termios.TCSANOW, a)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'A':
                        idx = (idx - 1) % len(options)
                        draw(up=True)
                    elif ch3 == 'B':
                        idx = (idx + 1) % len(options)
                        draw(up=True)
                elif not ch2:
                    if clear_on_confirm:
                        sys.stdout.write(f"\033[{n}F\033[J")
                    else:
                        sys.stdout.write("\r\n")
                    sys.stdout.write("\033[?25h")
                    sys.stdout.flush()
                    return _SELECTION_CANCELLED
            elif ch in ('\r', '\n'):
                if clear_on_confirm:
                    sys.stdout.write(f"\033[{n}F\033[J")
                else:
                    sys.stdout.write("\r\n")
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
                return idx
            elif ch in ('q', '\x03'):
                if clear_on_confirm:
                    sys.stdout.write(f"\033[{n}F\033[J")
                else:
                    sys.stdout.write("\r\n")
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
                return _SELECTION_CANCELLED
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def interactive_select_two_phase(
    phase1_options, phase1_display_fn, phase1_header,
    phase1_current_label, phase1_default_idx,
    phase2_options_fn, phase2_display_fn, phase2_header_fn,
    phase2_current_label_fn, phase2_default_idx_fn,
):
    from hpcagent.core.ui import c

    phase = "p1"
    i1 = phase1_default_idx
    i2 = 0
    p2_opts = []
    chosen = None
    P1L = len(phase1_options) + 2

    def dp1(up=False):
        if up:
            sys.stdout.write(f"\033[{P1L}F")
        sys.stdout.write(f"\r\033[2K{c.BOLD}{c.PINK}\u203a {phase1_header}{c.RESET}\n")
        for i, o in enumerate(phase1_options):
            rc = _menu_row_ansi(i)
            label = phase1_display_fn(o) if phase1_display_fn else str(o)
            if i == i1:
                sys.stdout.write(f"\r\033[2K  {c.BOLD}{rc}\u2038 {label}{c.RESET}\n")
            else:
                sys.stdout.write(f"\r\033[2K    {rc}{label}{c.RESET}\n")
        sys.stdout.write(f"\r\033[2K{c.GRAY}Current: {phase1_current_label}  {c.DIM}\u2191\u2193 Enter q{c.RESET}\n")
        sys.stdout.flush()

    def dp2(up=False):
        n = len(p2_opts) + 2
        if up:
            sys.stdout.write(f"\033[{n}F")
        sys.stdout.write(f"\r\033[2K{c.BOLD}{c.PINK}\u203a {phase2_header_fn(chosen)}{c.RESET}\n")
        for i, o in enumerate(p2_opts):
            rc = _menu_row_ansi(i)
            label = phase2_display_fn(o) if phase2_display_fn else str(o)
            if i == i2:
                sys.stdout.write(f"\r\033[2K  {c.BOLD}{rc}\u2038 {label}{c.RESET}\n")
            else:
                sys.stdout.write(f"\r\033[2K    {rc}{label}{c.RESET}\n")
        sys.stdout.write(f"\r\033[2K{c.GRAY}Current: {phase2_current_label_fn()}  {c.DIM}\u2191\u2193 Enter q{c.RESET}\n")
        sys.stdout.flush()

    def to_p2():
        nonlocal phase, p2_opts, i2
        phase = "p2"
        for _ in range(P1L):
            sys.stdout.write("\033[F\033[2K")
        p2_opts = phase2_options_fn(chosen)
        i2 = phase2_default_idx_fn()
        dp2()

    def to_p1():
        nonlocal phase
        for _ in range(len(p2_opts) + 2):
            sys.stdout.write("\033[F\033[2K")
        phase = "p1"
        dp1()

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    dp1()
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                fd = sys.stdin.fileno()
                a = list(termios.tcgetattr(fd))
                cc = list(a[6])
                cc[termios.VMIN] = 0; cc[termios.VTIME] = 3
                a[6] = cc
                termios.tcsetattr(fd, termios.TCSANOW, a)
                ch2 = sys.stdin.read(1)
                cc[termios.VMIN] = 1; cc[termios.VTIME] = 0
                a[6] = cc
                termios.tcsetattr(fd, termios.TCSANOW, a)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'A':
                        if phase == "p1":
                            i1 = (i1 - 1) % len(phase1_options); dp1(up=True)
                        else:
                            i2 = (i2 - 1) % max(1, len(p2_opts)); dp2(up=True)
                    elif ch3 == 'B':
                        if phase == "p1":
                            i1 = (i1 + 1) % len(phase1_options); dp1(up=True)
                        else:
                            i2 = (i2 + 1) % max(1, len(p2_opts)); dp2(up=True)
                elif not ch2:
                    if phase == "p2":
                        to_p1()
                    else:
                        chosen = None; break
            elif ch in ('\r', '\n'):
                if phase == "p1":
                    chosen = phase1_options[i1]
                    oo = phase2_options_fn(chosen)
                    if oo:
                        to_p2()
                    else:
                        break
                else:
                    break
            elif ch in ('q', '\x03'):
                if phase == "p2" and ch == 'q':
                    to_p1()
                else:
                    chosen = None; break
    finally:
        n = P1L if phase == "p1" else len(p2_opts) + 2
        for _ in range(n):
            sys.stdout.write("\033[F\033[2K")
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
    if chosen is None:
        return None, None
    if not p2_opts:
        return chosen, None
    sel = p2_opts[i2 % len(p2_opts)]
    return chosen, None if sel == "(default)" else sel
