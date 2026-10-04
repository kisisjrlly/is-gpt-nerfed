#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
/usr/bin/python3 - <<'PY'
import gi
gi.require_version('Gtk','3.0')
try:
 gi.require_version('AyatanaAppIndicator3','0.1')
except ValueError:
 gi.require_version('AppIndicator3','0.1')
PY
app="$HOME/.local/share/is-gpt-nerfed-linux"
mkdir -p "$app" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.config/autostart"
cp -R "$root/linux" "$app/"
mkdir -p "$app/plugin/skills/is-gpt-nerfed"
cp -R "$root/plugin/skills/is-gpt-nerfed/." "$app/plugin/skills/is-gpt-nerfed/"
codex_bin="$(command -v codex)"
python3 - "$app" "$codex_bin" <<'PY'
import pathlib, shlex, sys
app, codex = map(pathlib.Path, sys.argv[1:])
home = pathlib.Path.home()
launcher = home / '.local/bin/nerfed-tray'
launcher.write_text('#!/bin/sh\nexport PATH='+shlex.quote(str(codex.parent))+':"$PATH"\nexec /usr/bin/python3 '+shlex.quote(str(app/'linux/nerfed-tray.py'))+' "$@"\n')
launcher.chmod(0o755)
def quoted(path):
 return '"'+str(path).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
entry = '[Desktop Entry]\nType=Application\nName=is-gpt-nerfed\nComment=Codex 会话模型检测面板\nExec='+quoted(launcher)+' --show\nIcon='+str(app/'linux/icons/ok.svg')+'\nTerminal=false\nCategories=Utility;\n'
(home/'.local/share/applications/is-gpt-nerfed.desktop').write_text(entry)
(home/'.config/autostart/is-gpt-nerfed.desktop').write_text(entry.replace(' --show','')+'X-GNOME-Autostart-enabled=true\n')
print('Installed:', launcher)
PY
printf 'Launch: %s\n' "$HOME/.local/bin/nerfed-tray --show"
