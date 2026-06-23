# hpcagent demo — render with:  vhs assets/demo.tape
# Produces assets/demo.gif. Requires https://github.com/charmbracelet/vhs
#
# vhs scripts a terminal session deterministically (no live screen recording),
# so the demo is reproducible. Tweak the questions/timings below to taste, and
# make sure a backend is configured first (e.g. an installed `claude`/`codex`
# CLI, or `export OPENAI_API_KEY=...`).

Output assets/demo.gif

Require hpcagent

Set Shell bash
Set FontSize 18
Set Width 1200
Set Height 760
Set Padding 24
Set TypingSpeed 55ms
Set Theme "Catppuccin Mocha"

# Launch the TUI (first run shows the banner + setup summary).
Type "hpcagent"
Enter
Sleep 3s

# Ask a real cluster question and let the agent stream + run tools.
Type "why is job 1837465 pending?"
Sleep 800ms
Enter
Sleep 7s

# Show the searchable model picker: type to filter, arrows to choose.
Type "/model"
Enter
Sleep 1500ms
Type "gemini"
Sleep 1500ms
Enter
Sleep 1500ms

# Pull in the cluster's own documentation by URL, then ask about it.
Type "/docs add"
Enter
Sleep 1s
Type "https://docs.rcc.uchicago.edu/"
Enter
Sleep 8s
Type "how do I request a GPU node here?"
Sleep 800ms
Enter
Sleep 7s

Type "/exit"
Enter
Sleep 2s
