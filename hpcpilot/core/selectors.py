import sys

_SELECTION_CANCELLED = object()


def _fallback_select(options, header="Select option"):
    """Text-based fallback when not on a TTY."""
    print(f"\n{header}:")
    for i, opt in enumerate(options):
        print(f"  [{i}] {opt}")
    while True:
        try:
            val = input(f"Enter number (0-{len(options)-1}) or q to cancel: ").strip()
            if val.lower() in ('q', 'quit', ''):
                return _SELECTION_CANCELLED
            idx = int(val)
            if 0 <= idx < len(options):
                return idx
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        print(f"Invalid selection. Enter 0-{len(options)-1} or q.")


def interactive_select(options, header="Select option", current_label="", default_idx=0,
                        display_fn=None, selected_color=None, unselected_color=None,
                        clear_on_confirm=False, searchable=False, always_show=None,
                        max_visible=15):
    """Arrow-key selector returning the chosen index into ``options``.

    When ``searchable`` is True, printable keys build a live filter query and
    only matching options (plus any in ``always_show``) are shown, windowed to
    ``max_visible`` rows. Returns ``_SELECTION_CANCELLED`` on ESC / Ctrl-C.
    """
    from hpcpilot.core.ui import c

    # Non-TTY fallback
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _fallback_select(options, header)

    # Lazy import termios/tty (may not be available on all platforms)
    try:
        import termios
        import tty
    except ImportError:
        return _fallback_select(options, header)

    if display_fn is None:
        display_fn = str
    if selected_color is None:
        selected_color = f"{c.BOLD}{c.CYAN}"
    if unselected_color is None:
        unselected_color = c.GRAY
    always_show = always_show or set()

    import shutil
    term_w = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)

    def _truncate(label):
        # Keep rows from wrapping so the cursor-redraw math stays correct.
        budget = term_w - 4
        if len(label) > budget:
            return label[:budget - 1] + "…"
        return label

    query = ""
    sel = default_idx if 0 <= default_idx < len(options) else 0
    offset = 0
    state = {"last_lines": 0}

    def filtered():
        if searchable and query:
            ql = query.lower()
            return [i for i, o in enumerate(options)
                    if ql in display_fn(o).lower() or o in always_show]
        return list(range(len(options)))

    def draw(first=False):
        nonlocal sel, offset
        filt = filtered()
        if not filt:
            sel = 0
        else:
            sel %= len(filt)
            if sel < offset:
                offset = sel
            elif sel >= offset + max_visible:
                offset = sel - max_visible + 1
            offset = max(0, min(offset, max(0, len(filt) - max_visible)))
        window = filt[offset:offset + max_visible]

        lines = [f"{c.BOLD}{c.PINK}\u203a {_truncate(header)}{c.RESET}"]
        if searchable:
            shown_q = query if query else f"{c.DIM}type to filter\u2026{c.RESET}"
            lines.append(f"{c.GRAY}Search: {c.RESET}{c.CYAN}{shown_q}{c.RESET}")
        if not filt:
            lines.append(f"  {c.DIM}(no matches){c.RESET}")
        for pos, orig_i in enumerate(window):
            real_pos = offset + pos
            label = _truncate(display_fn(options[orig_i]))
            if real_pos == sel:
                lines.append(f"  {selected_color}> {label}{c.RESET}")
            else:
                lines.append(f"    {unselected_color}{label}{c.RESET}")
        more = len(filt) - (offset + len(window))
        if more > 0:
            lines.append(f"  {c.DIM}\u2026 {more} more{c.RESET}")
        hint = "\u2191\u2193 Enter  type=filter  ESC" if searchable else "\u2191\u2193 Enter q"
        cur = str(current_label) if len(str(current_label)) <= 40 else str(current_label)[:39] + "\u2026"
        lines.append(f"{c.GRAY}{_truncate(f'Current: {cur}  {hint}')}{c.RESET}")

        if not first and state["last_lines"]:
            sys.stdout.write(f"\033[{state['last_lines']}F")
        sys.stdout.write("\033[J")
        sys.stdout.write("".join(f"\r{ln}\n" for ln in lines))
        sys.stdout.flush()
        state["last_lines"] = len(lines)

    def _finish():
        if clear_on_confirm and state["last_lines"]:
            sys.stdout.write(f"\033[{state['last_lines']}F\033[J")
        else:
            sys.stdout.write("\r\n")
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    draw(first=True)
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
                    filt = filtered()
                    if ch3 == 'A' and filt:
                        sel = (sel - 1) % len(filt)
                        draw()
                    elif ch3 == 'B' and filt:
                        sel = (sel + 1) % len(filt)
                        draw()
                elif not ch2:
                    _finish()
                    return _SELECTION_CANCELLED
            elif ch in ('\r', '\n'):
                filt = filtered()
                if not filt:
                    continue
                _finish()
                return filt[sel]
            elif ch == '\x03':
                _finish()
                return _SELECTION_CANCELLED
            elif searchable and ch in ('\x7f', '\b'):
                query = query[:-1]
                sel = 0
                offset = 0
                draw()
            elif searchable and ch.isprintable():
                query += ch
                sel = 0
                offset = 0
                draw()
            elif not searchable and ch == 'q':
                _finish()
                return _SELECTION_CANCELLED
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
