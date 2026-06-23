# hpcagent demo — render with:  vhs assets/demo.tape   (produces assets/demo.gif)
# Requires https://github.com/charmbracelet/vhs  (conda: vhs ttyd ffmpeg)
#
# The session is driven by scripts/demo.py, which self-animates using hpcagent's
# real UI components (banner, streaming markdown, tool-status), so the GIF looks
# exactly like the app while staying fully reproducible (no live LLM/cluster).

Output assets/demo.gif

Set Shell bash
Set FontSize 18
Set Width 1180
Set Height 760
Set Padding 22
Set Framerate 24
Set Theme "Catppuccin Mocha"

Hide
Type "python scripts/demo.py"
Enter
Show
Sleep 26s
