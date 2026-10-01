import sublime
import sublime_plugin
import subprocess
import os
import threading
import json
import re
import sys

# Only support macOS and Linux
IS_WINDOWS = sys.platform == 'win32'

# Get default shell
def get_default_shell():
    for shell in ['/bin/zsh', '/usr/bin/zsh', '/bin/bash', '/usr/bin/bash']:
        if os.path.exists(shell):
            return shell
    return '/bin/sh'

DEFAULT_SHELL = get_default_shell()

# Lightweight rc file path
def get_lite_rc_path():
    home = os.path.expanduser("~")
    if 'zsh' in DEFAULT_SHELL:
        return os.path.join(home, '.zshrc_lite')
    elif 'bash' in DEFAULT_SHELL:
        return os.path.join(home, '.bashrc_lite')
    return None

BLOCKED = ["top", "htop", "vim", "vi", "nano", "watch", "less", "more", "man"]

# Dangerous command patterns (require confirmation)
DANGEROUS_PATTERNS = [
    # rm
    (r'\brm\s+(-[rfRvI]*\s+)*/', "Delete files under root (/)"),
    (r'\brm\s+(-[rfRvI]*\s+)*~', "Delete files under home (~)"),
    (r'\brm\s+-[rfRvI]*r[rfRvI]*\s', "Recursive delete"),
    (r'\brm\s+-[rfRvI]*f[rfRvI]*\s', "Force delete"),
    # Dangerous redirects
    (r'>\s*/dev/sd[a-z]', "Write to disk device"),
    # dd
    (r'\bdd\s+.*of=/dev/', "Write to device"),
    # mkfs
    (r'\bmkfs', "Format filesystem"),
    # chmod/chown
    (r'\bchmod\s+(-R\s+)?777\s+/', "Recursively change permissions under /"),
    (r'\bchown\s+-R\s+.*\s+/', "Recursively change ownership under /"),
    # Dangerous combinations
    (r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;', "Fork bomb"),
    (r'\bsudo\s+rm\s', "sudo delete"),
    (r'\bsudo\s+dd\s', "sudo write to device"),
    # Overwrite files
    (r'>\s*/etc/', "Overwrite system config files"),
    (r'>\s*~/', "Overwrite files under home (~)"),
    # Shutdown / reboot
    (r'\b(shutdown|reboot|halt|poweroff)\b', "Shutdown/reboot"),
]

MAX_HISTORY = 100

# Command history
_command_history = []
_history_file = os.path.join(sublime.packages_path(), 'User', 'shell_history.json')


def load_history():
    """Load history from disk."""
    global _command_history
    try:
        if os.path.exists(_history_file):
            with open(_history_file, 'r', encoding='utf-8') as f:
                _command_history = json.load(f)
    except:
        _command_history = []


def save_history():
    """Save history to disk."""
    try:
        os.makedirs(os.path.dirname(_history_file), exist_ok=True)
        with open(_history_file, 'w', encoding='utf-8') as f:
            json.dump(_command_history[-MAX_HISTORY:], f, ensure_ascii=False)
    except:
        pass


def add_to_history(cmd):
    """Add a command to history."""
    cmd = cmd.strip()
    if not cmd:
        return
    # De-duplicate by moving existing entries to the end.
    if cmd in _command_history:
        _command_history.remove(cmd)
    _command_history.append(cmd)
    save_history()


def plugin_loaded():
    """Load history when the plugin is loaded."""
    load_history()


def check_dangerous_command(cmd):
    """Check whether a command is dangerous. Returns (is_dangerous, reason)."""
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return True, reason
    return False, ""


class RunCommandInsertOutputCommand(sublime_plugin.TextCommand):
    """Helper command: insert text at a specific position."""
    def run(self, edit, point, text):
        self.view.insert(edit, point, text)


class RunCommandHistoryCompleteCommand(sublime_plugin.TextCommand):
    """History completion command."""
    def run(self, edit):
        if not _command_history:
            return
        
        sel = self.view.sel()
        if not sel:
            return
        
        # Get current line content.
        line_region = self.view.line(sel[0])
        line = self.view.substr(line_region).strip()
        
        if not line:
            # Empty line: show the most recent commands.
            matches = _command_history[-10:]
        else:
            # Match commands that start with the current input.
            matches = [cmd for cmd in _command_history if cmd.startswith(line)]
        
        if not matches:
            return
        
        # De-duplicate and reverse (most recent first).
        matches = list(dict.fromkeys(reversed(matches)))
        
        def on_select(idx):
            if idx >= 0:
                self.view.run_command('run_command_replace_line', {'text': matches[idx]})
        
        self.view.window().show_quick_panel(matches, on_select)


class RunCommandReplaceLineCommand(sublime_plugin.TextCommand):
    """Replace current line content."""
    def run(self, edit, text):
        sel = self.view.sel()
        if not sel:
            return
        line_region = self.view.line(sel[0])
        self.view.replace(edit, line_region, text)


class ShellHistoryAutoComplete(sublime_plugin.EventListener):
    """Auto-complete listener."""
    
    def on_modified_async(self, view):
        """Trigger auto-complete while typing."""
        if not _command_history:
            return
        
        sel = view.sel()
        if not sel:
            return
        
        # Get current line content.
        line_region = view.line(sel[0])
        line = view.substr(line_region).strip()
        
        # Require at least 2 characters.
        if len(line) < 2:
            return
        
        # Check for a matching history command.
        for cmd in reversed(_command_history):
            if cmd.startswith(line) and cmd != line:
                view.run_command('auto_complete', {
                    'disable_auto_insert': True,
                    'next_completion_if_showing': False
                })
                break
    
    def on_query_completions(self, view, prefix, locations):
        if not _command_history:
            return None
        
        # Get current line content up to the cursor.
        line_region = view.line(locations[0])
        line_start = line_region.begin()
        cursor = locations[0]
        line_to_cursor = view.substr(sublime.Region(line_start, cursor)).lstrip()
        
        if not line_to_cursor:
            return None
        
        # Match history commands and return full commands as completions.
        completions = []
        for cmd in reversed(_command_history):
            if cmd.startswith(line_to_cursor) and cmd != line_to_cursor:
                # Trigger and completion are both the full command.
                completions.append(sublime.CompletionItem(
                    trigger=cmd,
                    completion=cmd,
                    kind=(sublime.KIND_ID_SNIPPET, "R", "History")
                ))
            if len(completions) >= 10:
                break
        
        if completions:
            return sublime.CompletionList(completions, flags=sublime.INHIBIT_WORD_COMPLETIONS)
        return None
    
    def on_post_text_command(self, view, command_name, args):
        """Post-completion fix: remove duplicated prefixes."""
        if command_name not in ('commit_completion', 'insert_best_completion'):
            return
        
        sel = view.sel()
        if not sel:
            return
        
        line_region = view.line(sel[0])
        line = view.substr(line_region)
        
        # Check for duplicates (for example, "whwhoami" -> "whoami").
        for cmd in _command_history:
            # Find duplicate pattern.
            for i in range(1, len(cmd)):
                prefix = cmd[:i]
                if line.strip() == prefix + cmd:
                    # Replace with the correct command.
                    view.run_command('run_command_replace_line', {'text': cmd})
                    return


class RunCommandRunLineCommand(sublime_plugin.TextCommand):
    """Main command: run current line or selection."""
    def run(self, edit):
        for sel in self.view.sel():
            selected_text = self.view.substr(sel)
            
            # Check if there is selected text.
            if sel.size() > 0 and selected_text.strip():
                # Get last line of selection to check for a pipe.
                lines = selected_text.split('\n')
                last_line = lines[-1]
                
                # Check if the last line contains a pipe.
                pipe_idx = -1
                input_text = ""
                pipe_cmd = ""
                
                if last_line.strip().startswith('|'):
                    # Standalone pipe line.
                    input_text = '\n'.join(lines[:-1])
                    pipe_cmd = last_line.strip()[1:].strip()
                    pipe_idx = 0
                elif '|' in last_line:
                    # Inline pipe: use the last | position.
                    pipe_idx = last_line.rfind('|')
                    input_text = '\n'.join(lines[:-1])
                    if input_text:
                        input_text += '\n'
                    input_text += last_line[:pipe_idx]
                    pipe_cmd = last_line[pipe_idx + 1:].strip()
                
                if pipe_idx >= 0 and pipe_cmd:
                    insert_point = sel.end()
                    self.view.insert(edit, insert_point, "\n")
                    self._run_pipe_with_stdin(input_text, pipe_cmd, insert_point + 1)
                    continue
                
                # No pipe: execute selection as a command.
                cmd = selected_text.strip()
                if '\n' not in cmd:  # Single-line selection.
                    cmd_name = cmd.split()[0] if cmd.split() else ""
                    if cmd_name in BLOCKED:
                        self.view.insert(edit, sel.end(), "\n[Interactive command; please run in a terminal]")
                        continue
                    
                    is_dangerous, reason = check_dangerous_command(cmd)
                    if is_dangerous:
                        self._confirm_dangerous(cmd, sel, reason)
                        continue
                    
                    add_to_history(cmd)
                    
                    # "ll" alias.
                    if cmd == "ll" or cmd.startswith("ll "):
                        cmd = "ls -la" + cmd[2:]
                    
                    cwd = os.path.expanduser("~")
                    if self.view.file_name():
                        cwd = os.path.dirname(self.view.file_name())
                    
                    self.view.insert(edit, sel.end(), "\n")
                    insert_point = sel.end() + 1
                    
                    thread = threading.Thread(
                        target=self._run_command,
                        args=(cmd, cwd, self.view, insert_point)
                    )
                    thread.daemon = True
                    thread.start()
                    continue
            
            # Default path: run current line.
            line_region = self.view.line(sel)
            line = self.view.substr(line_region).strip()
            
            if not line:
                continue
            
            cmd_name = line.split()[0]
            if cmd_name in BLOCKED:
                self.view.insert(edit, line_region.end(), "\n[Interactive command; please run in a terminal]")
                continue
            
            # Check for dangerous commands.
            is_dangerous, reason = check_dangerous_command(line)
            if is_dangerous:
                self._confirm_dangerous(line, line_region, reason)
                continue
            
            self._execute_command(edit, line, line_region)
    
    def _run_pipe_command(self, cmd, insert_point):
        """Run a piped command."""
        cwd = os.path.expanduser("~")
        if self.view.file_name():
            cwd = os.path.dirname(self.view.file_name())
        
        thread = threading.Thread(
            target=self._run_command,
            args=(cmd, cwd, self.view, insert_point)
        )
        thread.daemon = True
        thread.start()
    
    def _run_pipe_with_stdin(self, input_text, pipe_cmd, insert_point):
        """Pass content to a piped command via stdin."""
        cwd = os.path.expanduser("~")
        if self.view.file_name():
            cwd = os.path.dirname(self.view.file_name())
        
        def run():
            try:
                # Build command.
                lite_rc = get_lite_rc_path()
                if lite_rc and os.path.exists(lite_rc):
                    wrapped_cmd = '{} -c "source {} && {}"'.format(
                        DEFAULT_SHELL, lite_rc, pipe_cmd.replace('"', '\\"'))
                else:
                    wrapped_cmd = pipe_cmd
                
                proc = subprocess.Popen(
                    wrapped_cmd,
                    shell=True,
                    executable=DEFAULT_SHELL,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    bufsize=1
                )
                
                # Write input to stdin.
                proc.stdin.write(input_text.encode('utf-8'))
                proc.stdin.close()
                
                current_point = [insert_point]
                view = self.view
                
                def insert_text(text):
                    def do_insert():
                        if not view.is_valid():
                            return
                        view.run_command('run_command_insert_output', {
                            'point': current_point[0],
                            'text': text
                        })
                        current_point[0] += len(text)
                    sublime.set_timeout(do_insert, 0)
                
                byte_buffer = b''
                while True:
                    chunk = proc.stdout.read(32)
                    if not chunk:
                        if byte_buffer:
                            insert_text(byte_buffer.decode('utf-8', errors='replace'))
                        break
                    
                    byte_buffer += chunk
                    safe_len = len(byte_buffer)
                    while safe_len > 0:
                        try:
                            byte_buffer[:safe_len].decode('utf-8')
                            break
                        except UnicodeDecodeError:
                            safe_len -= 1
                    
                    if safe_len > 0:
                        text = byte_buffer[:safe_len].decode('utf-8')
                        byte_buffer = byte_buffer[safe_len:]
                        insert_text(text)
                
                proc.wait()
                
            except Exception as e:
                err_msg = str(e)
                def show_error(err_msg=err_msg):
                    if self.view.is_valid():
                        self.view.run_command('run_command_insert_output', {
                            'point': insert_point,
                            'text': '[Error: ' + err_msg + ']'
                        })
                sublime.set_timeout(show_error, 0)
        
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
    
    def _confirm_dangerous(self, line, line_region, reason):
        """Confirm dangerous command."""
        def on_confirm(confirmed):
            if confirmed:
                self.view.run_command('run_command_execute_confirmed', {
                    'line': line,
                    'line_end': line_region.end()
                })
        
        sublime.ok_cancel_dialog(
            "Dangerous Command Warning\n\n"
            "Command: {}\n"
            "Reason: {}\n\n"
            "Are you sure you want to run it?".format(line, reason),
            "Run"
        ) and on_confirm(True)
    
    def _execute_command(self, edit, line, line_region):
        """Run a command."""
        # Record in history.
        add_to_history(line)
        
        # "ll" alias: only replace at the start or after a pipe.
        cmd = line
        if cmd == "ll" or cmd.startswith("ll "):
            cmd = "ls -la" + cmd[2:]
        elif " ll" in cmd:
            cmd = cmd.replace(" ll ", " ls -la ").replace(" ll\n", " ls -la\n")
            if cmd.endswith(" ll"):
                cmd = cmd[:-3] + " ls -la"
        
        # Working directory: prefer the current file's directory.
        cwd = os.path.expanduser("~")
        if self.view.file_name():
            cwd = os.path.dirname(self.view.file_name())
        
        # Insert newline first.
        self.view.insert(edit, line_region.end(), "\n")
        insert_point = line_region.end() + 1
        
        # Run asynchronously.
        thread = threading.Thread(
            target=self._run_command,
            args=(cmd, cwd, self.view, insert_point)
        )
        thread.daemon = True
        thread.start()
    
    def _run_command(self, cmd, cwd, view, insert_point):
        """Run a command asynchronously and stream output."""
        try:
            # Build command.
            lite_rc = get_lite_rc_path()
            if lite_rc and os.path.exists(lite_rc):
                wrapped_cmd = '{} -c "source {} && {}"'.format(
                    DEFAULT_SHELL, lite_rc, cmd.replace('"', '\\"'))
            else:
                wrapped_cmd = cmd
            
            proc = subprocess.Popen(
                wrapped_cmd,
                shell=True,
                executable=DEFAULT_SHELL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                bufsize=1
            )
            
            current_point = [insert_point]
            
            def insert_text(text):
                def do_insert():
                    if not view.is_valid():
                        return
                    view.run_command('run_command_insert_output', {
                        'point': current_point[0],
                        'text': text
                    })
                    current_point[0] += len(text)
                sublime.set_timeout(do_insert, 0)
            
            # Stream output with small reads and UTF-8 boundary handling.
            byte_buffer = b''
            while True:
                chunk = proc.stdout.read(32)
                if not chunk:
                    if byte_buffer:
                        insert_text(byte_buffer.decode('utf-8', errors='replace'))
                    break
                
                byte_buffer += chunk
                
                safe_len = len(byte_buffer)
                while safe_len > 0:
                    try:
                        byte_buffer[:safe_len].decode('utf-8')
                        break
                    except UnicodeDecodeError:
                        safe_len -= 1
                
                if safe_len > 0:
                    text = byte_buffer[:safe_len].decode('utf-8')
                    byte_buffer = byte_buffer[safe_len:]
                    insert_text(text)
            
            proc.wait()
            
        except Exception as e:
            err_msg = str(e)
            def show_error(err_msg=err_msg):
                if view.is_valid():
                    view.run_command('run_command_insert_output', {
                        'point': insert_point,
                        'text': '[Error: ' + err_msg + ']'
                    })
            sublime.set_timeout(show_error, 0)


class RunCommandExecuteConfirmedCommand(sublime_plugin.TextCommand):
    """Run a confirmed dangerous command."""
    def run(self, edit, line, line_end):
        # Record in history.
        add_to_history(line)
        
        # "ll" alias.
        cmd = line
        if cmd == "ll" or cmd.startswith("ll "):
            cmd = "ls -la" + cmd[2:]
        
        # Working directory.
        cwd = os.path.expanduser("~")
        if self.view.file_name():
            cwd = os.path.dirname(self.view.file_name())
        
        # Insert newline.
        self.view.insert(edit, line_end, "\n")
        insert_point = line_end + 1
        
        # Run asynchronously.
        thread = threading.Thread(
            target=self._run_command,
            args=(cmd, cwd, self.view, insert_point)
        )
        thread.daemon = True
        thread.start()
    
    def _run_command(self, cmd, cwd, view, insert_point):
        """Run a command asynchronously."""
        try:
            # Use lightweight rc if available.
            lite_rc = get_lite_rc_path()
            if lite_rc and os.path.exists(lite_rc):
                wrapped_cmd = '{} -c "source {} && {}"'.format(
                    DEFAULT_SHELL, lite_rc, cmd.replace('"', '\\"'))
            else:
                wrapped_cmd = cmd
            
            proc = subprocess.Popen(
                wrapped_cmd,
                shell=True,
                executable=DEFAULT_SHELL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                bufsize=1
            )
            
            current_point = [insert_point]
            
            def insert_text(text):
                def do_insert():
                    if not view.is_valid():
                        return
                    view.run_command('run_command_insert_output', {
                        'point': current_point[0],
                        'text': text
                    })
                    current_point[0] += len(text)
                sublime.set_timeout(do_insert, 0)
            
            byte_buffer = b''
            while True:
                chunk = proc.stdout.read(32)
                if not chunk:
                    if byte_buffer:
                        insert_text(byte_buffer.decode('utf-8', errors='replace'))
                    break
                
                byte_buffer += chunk
                safe_len = len(byte_buffer)
                while safe_len > 0:
                    try:
                        byte_buffer[:safe_len].decode('utf-8')
                        break
                    except UnicodeDecodeError:
                        safe_len -= 1
                
                if safe_len > 0:
                    text = byte_buffer[:safe_len].decode('utf-8')
                    byte_buffer = byte_buffer[safe_len:]
                    insert_text(text)
            
            proc.wait()
        except Exception as e:
            err_msg = str(e)
            def show_error(err_msg=err_msg):
                if view.is_valid():
                    view.run_command('run_command_insert_output', {
                        'point': insert_point,
                        'text': '[Error: ' + err_msg + ']'
                    })
            sublime.set_timeout(show_error, 0)
