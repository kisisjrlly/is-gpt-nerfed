#!/usr/bin/python3
"""Linux GTK/AppIndicator frontend for the existing nerfed snapshot protocol."""
import argparse
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import gi

gi.require_version('Gtk', '3.0')
try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as Indicator
except ValueError:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as Indicator
from gi.repository import Gtk, GLib, Pango

BASE = Path(__file__).resolve().parent
NERFED = BASE.parent / 'plugin/skills/is-gpt-nerfed/scripts/nerfed'
LABELS = {'MATCH': '匹配', 'MISMATCH': '不匹配', 'DOWNGRADED!': '降级',
          'SUSPICIOUS': '可疑', 'UNLISTED': '模型未收录', 'INVALID': '检测失败'}


def command(*args):
    result = subprocess.run([sys.executable, str(NERFED), *args], capture_output=True,
                            text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or '命令失败')
    return result.stdout


class Tray:
    def __init__(self, demo=False):
        self.demo = demo
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.busy = False
        self.snapshot = {}
        self.pending = {}
        self.last_tick = self.last_fresh = 0
        self.seen_verdicts = None
        self.indicator = Indicator.Indicator.new('is-gpt-nerfed', str(BASE / 'icons/idle.svg'),
                                                  Indicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        for title, callback in [('打开检测面板', self.show), ('立即刷新', self.refresh),
                                ('退出', Gtk.main_quit)]:
            item = Gtk.MenuItem(label=title)
            item.connect('activate', lambda _, fn=callback: fn())
            menu.append(item)
        menu.show_all()
        self.indicator.set_menu(menu)
        self.indicator.set_title('is-gpt-nerfed')
        self.window = Gtk.Window(title='is-gpt-nerfed · 会话检测')
        self.window.set_default_size(960, 640)
        self.window.connect('delete-event', self.hide)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(16)
        self.window.add(box)
        self.status = Gtk.Label(label='正在读取会话…', xalign=0)
        self.status.set_selectable(True)
        box.pack_start(self.status, False, False, 0)
        bar = Gtk.Box(spacing=8)
        box.pack_start(bar, False, False, 0)
        for title, callback in [('刷新', self.refresh), ('检测选中会话', self.probe_selected),
                                ('检测新会话', lambda: self.probe('fresh'))]:
            button = Gtk.Button(label=title)
            button.connect('clicked', lambda _, fn=callback: fn())
            bar.pack_start(button, False, False, 0)
        bar.pack_start(Gtk.Label(label='自动检测：'), False, False, 0)
        self.frequency = Gtk.ComboBoxText()
        for value in ['30m', '60m', 'turns:8', 'manual']:
            self.frequency.append(value, {'30m': '累计使用 30 分钟', '60m': '累计使用 60 分钟',
                                         'turns:8': '每 8 轮', 'manual': '仅手动'}[value])
        bar.pack_start(self.frequency, False, False, 0)
        apply = Gtk.Button(label='应用')
        apply.connect('clicked', self.set_frequency)
        bar.pack_start(apply, False, False, 0)
        pane = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(pane, True, True, 0)
        self.rows = Gtk.ListStore(str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.rows)
        for i, title in enumerate(['会话（过去 48 小时）', '请求模型', '最近结果', '状态']):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=i + 1)
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            column.set_fixed_width([380, 180, 210, 100][i])
            column.set_resizable(True)
            if i == 0:
                column.set_expand(True)
                column.set_min_width(260)
            self.tree.append_column(column)
        self.tree.get_selection().connect('changed', self.selection_changed)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.tree)
        pane.pack1(scroll, True, False)
        self.report = Gtk.TextView(editable=False, cursor_visible=False,
                                   wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.report.set_left_margin(10)
        self.report.set_right_margin(10)
        details = Gtk.ScrolledWindow()
        details.add(self.report)
        pane.pack2(details, True, False)
        pane.set_position(300)
        self.footer = Gtk.Label(label='每 8 秒刷新；自动检测消耗账户额度，每次通常 3 次简短回答。关闭窗口后仍在托盘运行。', xalign=0)
        box.pack_start(self.footer, False, False, 0)
        GLib.timeout_add_seconds(8, self.refresh)
        self.refresh()

    def hide(self, *_):
        self.window.hide()
        return True

    def show(self):
        self.window.show_all()
        self.window.present()

    def submit(self, fn, done):
        future = self.pool.submit(fn)
        def finish(f):
            try:
                value, error = f.result(), None
            except Exception as exc:
                value, error = None, str(exc)
            GLib.idle_add(done, value, error)
        future.add_done_callback(finish)

    def refresh(self):
        if not self.busy:
            self.busy = True
            args = ['snapshot', '--json'] + (['--demo'] if self.demo else [])
            self.submit(lambda: json.loads(command(*args)), self.updated)
        return True

    def selected(self):
        model, row = self.tree.get_selection().get_selected()
        return model[row][0] if row else None

    def selection_changed(self, *_):
        sid = self.selected()
        if sid == 'fresh':
            text = self.snapshot.get('global_report_text') or '新会话尚未检测。'
        else:
            thread = next((t for t in self.snapshot.get('threads', []) if t['id'] == sid), {})
            text = thread.get('report_text') or '此会话尚无检测结果。'
            if thread.get('halted'):
                text += '\n会话已暂停；需在对应对话中明确要求继续。'
        self.report.get_buffer().set_text(text)

    def updated(self, data, error):
        self.busy = False
        if error:
            self.status.set_text('读取失败：' + error)
            self.indicator.set_icon_full(str(BASE / 'icons/warn.svg'), '读取失败')
            return False
        selected = self.selected()
        self.snapshot = data
        overall = data.get('overall', {})
        hooks = data.get('hooks', {})
        summary = ' · '.join(f"{overall.get(key, 0)} {label}" for key, label in [('downgraded', '异常'), ('suspicious', '可疑'), ('upgraded', '升级'), ('unverified', '未验证'), ('running', '检测中')] if overall.get(key)) or '暂无异常证据'
        self.status.set_text(f"{summary}   ·   hooks {hooks.get('trusted', 0)}/{hooks.get('total', 0)} 已信任   ·   {data.get('generated', '')}")
        icon = ('alert' if overall.get('downgraded') else 'warn' if overall.get('suspicious')
                else 'running' if overall.get('running') or self.pending else 'ok' if data.get('last_verdict') else 'idle')
        self.indicator.set_icon_full(str(BASE / f'icons/{icon}.svg'), overall.get('message', ''))
        self.rows.clear()
        entries = [{'id': 'fresh', 'title': '新会话（无历史上下文）', 'model': data.get('default_model'),
                    'last_probe': data.get('global_probe'), 'last_failure': data.get('global_failure'), 'probe_running': data.get('global_running')}] + data.get('threads', [])
        for thread in entries:
            sid = thread['id']
            probe = thread.get('last_probe') or {}
            pending = self.pending.get(sid, 0)
            if thread.get('probe_running') or time.monotonic() - pending > 45:
                self.pending.pop(sid, None)
            result = LABELS.get(probe.get('verdict'), probe.get('verdict') or '尚未检测')
            if probe.get('stale_account'):
                result = '未验证（其他或未知账户）'
            elif probe.get('is_upgrade'):
                result = '升级'
            elif probe.get('is_downgrade') or thread.get('hard_evidence'):
                result = '降级 / 异常证据'
            if thread.get('last_failure'):
                result += ' · 最近尝试失败'
            state = ('已暂停' if thread.get('halted') else '检测中' if thread.get('probe_running') or sid in self.pending
                     else '等待检测' if thread.get('due') else '活跃' if thread.get('active') else '空闲')
            row = self.rows.append([sid, thread.get('title') or sid, thread.get('model') or '未知', result, state])
            if sid == selected:
                self.tree.get_selection().select_iter(row)
        if not selected and len(self.rows):
            self.tree.get_selection().select_path(0)
        self.selection_changed()
        frequency = data.get('config', {}).get('frequency', '30m')
        if self.frequency.get_active_id() is None:
            if frequency not in ['30m', '60m', 'turns:8', 'manual']:
                self.frequency.append(frequency, frequency)
            self.frequency.set_active_id(frequency)
        self.notify_results(data)
        if not self.demo:
            now = time.monotonic()
            if now - self.last_tick > 60:
                self.last_tick = now
                self.submit(lambda: command('tick'), self.action_done)
            if data.get('fresh_due') and not data.get('global_running') and 'fresh' not in self.pending and now - self.last_fresh > 300:
                self.last_fresh = now
                self.probe('fresh')
        return False

    def notify_results(self, data):
        probes = data.get('recent_probes', [])
        ids = {p['id'] for p in probes}
        if self.seen_verdicts is not None and not self.demo and data.get('config', {}).get('notify', True):
            for probe in probes:
                verdict = probe.get('verdict')
                if probe['id'] not in self.seen_verdicts and not probe.get('stale_account') and (verdict in ['MISMATCH', 'DOWNGRADED!', 'SUSPICIOUS', 'UNLISTED'] or
                        verdict == 'MATCH' and data.get('config', {}).get('notify_on_ok')):
                    try:
                        subprocess.Popen(['notify-send', 'is-gpt-nerfed',
                                          f"{LABELS.get(verdict, verdict)}：{probe.get('expected')} → {probe.get('prediction')}"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except OSError:
                        pass
        self.seen_verdicts = ids if self.seen_verdicts is None else self.seen_verdicts | ids

    def probe_selected(self):
        if self.selected():
            self.probe(self.selected())

    def probe(self, sid):
        if self.demo or sid in self.pending:
            return
        thread = next((t for t in self.snapshot.get('threads', []) if t['id'] == sid), {})
        if thread.get('halted') or thread.get('probe_running') or (sid == 'fresh' and self.snapshot.get('global_running')):
            self.footer.set_text('该会话正在检测或已暂停，未重复启动。')
            return
        args = ['worker', '--fresh'] if sid == 'fresh' else ['worker', '--thread', sid]
        try:
            logdir = Path.home() / '.local/state/is-gpt-nerfed'
            logdir.mkdir(parents=True, exist_ok=True)
            with (logdir / 'workers.log').open('a') as log:
                subprocess.Popen([sys.executable, str(NERFED), *args], stdout=log, stderr=log,
                                 start_new_session=True)
            self.pending[sid] = time.monotonic()
            self.footer.set_text('检测已启动，结果将自动刷新。')
            self.refresh()
        except OSError as exc:
            self.footer.set_text(str(exc))

    def set_frequency(self, *_):
        value = self.frequency.get_active_id()
        if value and not self.demo:
            self.submit(lambda: command('config', 'set', 'frequency', value), self.action_done)

    def action_done(self, _, error):
        if error:
            self.footer.set_text('操作失败：' + error)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
    lock = (runtime / f'nerfed-tray-{os.getuid()}.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # The running instance listens for requests to show its window.
        (runtime / f'nerfed-tray-{os.getuid()}.show').touch()
        return
    tray = Tray(args.demo)
    signal = runtime / f'nerfed-tray-{os.getuid()}.show'
    def check_show():
        if signal.exists():
            signal.unlink(missing_ok=True)
            tray.show()
        return True
    GLib.timeout_add_seconds(1, check_show)
    if args.show or args.smoke_test:
        tray.show()
    if args.smoke_test:
        def verify():
            if not tray.snapshot:
                print('FAIL: no snapshot', file=sys.stderr)
                os._exit(1)
            print(f"PASS: GTK window, tray and snapshot ({len(tray.rows)} rows)", flush=True)
            Gtk.main_quit()
            return False
        GLib.timeout_add_seconds(12, verify)
    Gtk.main()


if __name__ == '__main__':
    main()
