#!/usr/bin/env python3
"""Regression tests using isolated Fish histories and real PTY sessions."""

import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import struct
import termios
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'history_word_prefix.fish'
LOG_DIR = Path(os.environ.get('HWP_TEST_LOG_DIR', ROOT / 'test-logs')).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)


def fish_quote(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def check_matcher():
    with_env = 'PATH=/bin LD_LIBRARY_PATH=/lib podman compose ps'
    cases = [
        ('podman c e m', [
            'podman compose exec -it mycontainer /bin/sh',
            'podman compose exec --env MODE=debug mycontainer',
            'podman compose logs mycontainer',
            'podman exec compose mycontainer',
            'podman abc def mycontainer',
            'podman ps',
            'sudo podman compose exec mycontainer',
            'podman compose exec other-mycontainer',
        ], [0, 1]),
        ('podman   c\te m', ['podman\tcompose exec -it mycontainer'], [0]),
        ('tool a.b [x]', ['tool a.bcd [x]yz', 'tool axb [x]yz'], [0]),
        ('podman c e m', ['podman compose\nexec -it mycontainer\n'], [0]),
        ('tool aa aa', ['tool aaa', 'tool aaa aab'], [1]),
        ('', ['one', 'two\nthree\n'], [0, 1]),
        ('tool /b', ['tool /bin/sh', 'tool bin/sh'], [0]),
        ('podman c e m', ['podman compose exec "mycontainer"'], []),
        ('p c ps', [with_env], [0]),
        ('PATH p c', [with_env], [0]),
        ('P L ps', [with_env], [0]),
        ('L P', [with_env], []),
        ('c ps', [with_env, 'podman compose ps', 'PATH=/bin podman OPT=yes compose ps'], []),
        ('L p', [with_env], [0]),
        ('L P', ['LD_LIBRARY_PATH=/lib PATH=/bin podman compose ps'], [0]),
        ('p c ps', ['PATH=/bin sudo podman compose ps', 'echo PATH=/bin podman compose ps'], []),
        ('p c ps', ['PATH=/bin podman OPT=yes compose ps'], [0]),
        ('p c ps', [' \tPATH=/bin\nLD_LIBRARY_PATH=/lib\tpodman compose ps\n'], [0]),
        ('p c ps', ['=/bin EMPTY= EXTRA=a=b podman compose ps'], [0]),
        ('PATH PATH', [with_env], []),
        ('ps', [with_env], []),
    ]
    for query, commands, indexes in cases:
        proc = subprocess.run(
            ['fish', '--no-config', '--private', '-c',
             f'source {fish_quote(SOURCE)}; set -l pattern (__hwp_pattern "$argv[1]"); '
             'while read --null --local item; '
             'if string match --quiet --regex -- "$pattern" "$item"; printf "%s\\0" "$item"; end; end', query],
            input=b''.join(c.encode() + b'\0' for c in commands),
            capture_output=True, check=True,
        )
        actual = proc.stdout.split(b'\0')[:-1]
        expected = [commands[i].encode() for i in indexes]
        assert actual == expected, (query, actual, expected, proc.stderr)
    print(f'Matcher: {len(cases)} cases passed')


def check_prefixes():
    with_env = 'PATH=/bin LD_LIBRARY_PATH=/lib podman compose ps --all'
    env_prefix = 'PATH=/bin LD_LIBRARY_PATH=/lib '
    cases = [
        ('pod c', 'podman compose logs', 'podman compose'),
        ('pod c', 'podman exec mycontainer cat file', 'podman exec mycontainer cat'),
        ('podman compose e', 'podman compose exec -it mycontainer /bin/sh', 'podman compose exec'),
        ('pod c', 'podman compose exec cat file', 'podman compose'),
        ('pod c e m', 'podman compose exec -it mycontainer /bin/sh', 'podman compose exec -it mycontainer'),
        ('tool a.b', 'tool a.bcd suffix', 'tool a.bcd'),
        ('tool aa aa', 'tool aaa aab rest', 'tool aaa aab'),
        ('pod c', 'podman\ncompose logs', 'podman\ncompose'),
        ('', 'podman compose logs', 'podman'),
        ('工 子', '工具 子命令 其他', '工具 子命令'),
        ('p c', with_env, env_prefix + 'podman compose'),
        ('PATH p c', with_env, env_prefix + 'podman compose'),
        ('P L ps', with_env, env_prefix + 'podman compose ps'),
        ('L', with_env, env_prefix.rstrip()),
        ('P', 'PATH=/bin PROGRAM=release podman compose ps', 'PATH=/bin'),
        ('p c', ' \tPATH=/bin\nLD_LIBRARY_PATH=/lib\tpodman compose ps',
         ' \tPATH=/bin\nLD_LIBRARY_PATH=/lib\tpodman compose'),
    ]
    for query, candidate, expected in cases:
        proc = subprocess.run(
            ['fish', '--no-config', '--private', '-c',
             f'source {fish_quote(SOURCE)}; __hwp_prefix "$argv[1]" "$argv[2]"', query, candidate],
            capture_output=True, check=True,
        )
        assert proc.stdout == expected.encode() + b'\0', (query, proc.stdout, expected)
    print(f'Prefix endpoints: {len(cases)} cases passed')


class FishPTY:
    def __init__(self, directory, private=True):
        self.directory = Path(directory)
        self.snapshots = self.directory / 'snapshots'
        self.output = bytearray()
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, TERM='xterm-256color',
                       fish_history='hwp_test',
                       XDG_CONFIG_HOME=str(self.directory / 'config'),
                       XDG_DATA_HOME=str(self.directory / 'data'),
                       XDG_CACHE_HOME=str(self.directory / 'cache'))
            args = ['fish', '--no-config', '--interactive']
            if private:
                args.append('--private')
            os.execvpe('fish', args, env)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 120, 0, 0))
        self.drain(0.2)
        self.wait_for_prompt(0)
        setup = [
            "function fish_prompt; printf 'HWP> '; end",
            f'source {fish_quote(SOURCE)}',
            f"bind ctrl-x '__hwp_buffer >> {fish_quote(self.snapshots)}; commandline --function repaint'",
            "function __inject_query; commandline --replace -- (cat "
            + fish_quote(self.directory / 'injected-query')
            + " | string split0); commandline --cursor 3; end",
            "bind ctrl-y __inject_query",
            'function __test_observers --on-variable fish_bind_mode; if test "$fish_bind_mode" = hwp_inline; if set --query __test_capture; bind --mode hwp_inline ctrl-x "$__test_capture"; end; if set --query __test_ctrl_o; bind --mode hwp_inline ctrl-o "$__test_ctrl_o"; end; end; end',
        ]
        for command in setup:
            self.send(command.encode() + b'\r')

    def drain(self, wait=0.04):
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 65536)
                self.output.extend(chunk)
                if b'\x1b[6n' in chunk:
                    os.write(self.fd, b'\x1b[1;1R')
                if b'\x1b[c' in chunk or b'\x1b[0c' in chunk:
                    os.write(self.fd, b'\x1b[?1;2c')
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise

    def send(self, value):
        start = len(self.output)
        os.write(self.fd, value)
        self.drain()
        if value.endswith(b'\r'):
            self.wait_for_prompt(start)

    def wait_for_prompt(self, start):
        deadline = time.monotonic() + 5
        while b'\x1b]133;A' not in self.output[start:]:
            assert time.monotonic() < deadline, self.output.decode(errors='replace')
            self.drain(0.05)

    def observe(self, key, command, modes=('default', 'hwp_inline', 'insert')):
        variable = {'ctrl-x': '__test_capture', 'ctrl-o': '__test_ctrl_o'}[key]
        self.send(('set --global ' + variable + ' ' + fish_quote(command)).encode() + b'\r')
        for mode in modes:
            self.send(('bind --mode ' + mode + ' ' + key + ' ' + fish_quote(command)).encode() + b'\r')

    def load(self, commands):
        # Use builtins rather than executing any history entry.
        self.send(b'\x03')
        # The script removes its own invocation from interactive history too.
        fixture = self.directory / 'fixture.fish'
        fixture.write_text('builtin history clear\n' + ''.join(
            'builtin history append ' + fish_quote(c) + '\n' for c in commands))
        self.send(('source ' + fish_quote(fixture)).encode() + b'\r')

    def snapshot(self):
        old = self.snapshots.stat().st_size if self.snapshots.exists() else 0
        self.send(b'\x18')
        deadline = time.monotonic() + 3
        while (not self.snapshots.exists() or self.snapshots.stat().st_size <= old):
            assert time.monotonic() < deadline, self.output.decode(errors='replace')
            self.drain(0.05)
        return self.snapshots.read_bytes()[old:-1].decode()

    def expect(self, expected):
        actual = self.snapshot()
        assert actual == expected, (actual, expected, f'See {LOG_DIR} for the PTY transcript')

    def close(self):
        os.kill(self.pid, signal.SIGTERM)
        os.waitpid(self.pid, 0)
        os.close(self.fd)


def check_inline():
    with tempfile.TemporaryDirectory(prefix='hwp-inline-') as directory:
        shell = FishPTY(directory)
        cursor_file = shell.directory / 'cursor'
        checks = 0
        try:
            shell.send(b'history_word_prefix_inline_bindings\r')
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       f'printf "%s\\n" (commandline --cursor) >> {fish_quote(cursor_file)}; '
                       'commandline --function repaint')
            shell.observe('ctrl-x', capture)

            def expect(buffer, cursor=None):
                nonlocal checks
                shell.expect(buffer)
                if cursor is not None:
                    actual = int(cursor_file.read_text().splitlines()[-1])
                    assert actual == cursor, (actual, cursor, buffer)
                checks += 1

            old = 'podman compose exec -it mycontainer /bin/sh'
            logs = 'podman compose logs'
            cat = 'podman exec mycontainer cat file'
            shell.load([old, logs, cat])
            shell.send(b'pod c\x1b[A')
            expect(cat, len('podman exec mycontainer cat '))
            shell.send(b'\x1b[A'); expect(logs, len('podman compose '))
            shell.send(b'e'); expect('podman compose e')
            shell.send(b'\x1b[A'); expect(old, len('podman compose exec '))
            shell.send(b'\x1b[C'); expect(old, len(old))
            # Escape restores the original shorthand.
            shell.send(b'\x03')
            shell.send(b'pod c\x1b[A')
            shell.send(b'\x1b'); shell.drain(0.1)
            expect('pod c', len('pod c'))
            # Down returns from the first candidate to the original input.
            shell.send(b'\x1b[A'); shell.send(b'\x1b[B')
            expect('pod c', len('pod c'))
            # Match only text before the cursor, and restore the whole buffer
            # and the exact cursor if the request is cancelled.
            original = 'pod c unrelated-suffix'
            shell.send(b'\x03')
            shell.send(original.encode())
            shell.send(b'\x1b[D' * len(' unrelated-suffix'))
            shell.send(b'\x1b[A'); expect(cat, len('podman exec mycontainer cat '))
            shell.send(b'\x1b'); shell.drain(0.1)
            expect(original, len('pod c'))
            # Left inside preview commits its prefix, then moves left normally.
            shell.send(b'\x1b[A\x1b[A')
            expect(logs, len('podman compose '))
            shell.send(b'\x1b[D'); expect('podman compose ', len('podman compose'))
            shell.send(b'\x1b[A'); expect(logs, len('podman compose '))
            # Simulate a mouse/external cursor move without invoking Left's
            # preview-commit binding. A new request uses that exact position.
            shell.send(b'\x1b')
            shell.drain(0.1)
            shell.send(b'\x03')
            shell.observe('ctrl-o', 'commandline --cursor 3', modes=['hwp_inline'])
            shell.send(b'\x03')
            shell.send(b'pod c\x1b[A\x1b[A')
            shell.send(b'\x0f\x1b[A'); expect(cat, len('podman '))
            shell.send(b'\x1b'); shell.drain(0.1)
            expect(logs, 3)
            shell.send(b'\x03')
            shell.send(b'pod c\x1b[A\x1b[A')
            # Bracketed paste cannot accidentally retain the preview tail.
            shell.send(b'\x1b[200~e\x1b[201~')
            shell.send(b'\x1b[A'); expect(old, len('podman compose exec '))
            # Backspace edits the completed prefix, not the preview suffix.
            shell.send(b'\x7f'); expect('podman compose exec', len('podman compose exec'))
            # Enter executes only the completed prefix; Right accepts the full
            # command for a subsequent Enter. Preview commands never run here.
            sentinel = shell.directory / 'executed'
            candidate = 'printf typed-prefix; touch ' + str(sentinel)
            shell.load([candidate])
            shell.send(b'pri\x1b[A'); expect(candidate, len('printf '))
            shell.send(b'\r')
            expect('')
            assert not sentinel.exists(); checks += 1
            shell.load([candidate])
            shell.send(b'pri\x1b[A')
            shell.send(b'\x1b[F'); expect(candidate, len(candidate))
            assert not sentinel.exists(); checks += 1
            # A candidate with no suffix gets exactly one ready-to-type space.
            shell.load(['podman compose'])
            shell.send(b'pod c\x1b[A'); expect('podman compose ', len('podman compose '))
            # With no matching history, ordinary file completion still works.
            shell.send(b'\x03')
            completion_file = shell.directory / 'alphafile'
            completion_file.touch()
            shell.send(('cat ' + str(shell.directory / 'alpha')).encode() + b'\t')
            expect('cat ' + str(completion_file) + ' ')
            # vi insert mode is restored after accepting a suggestion.
            shell.send(b'\x03')
            shell.send(b'fish_vi_key_bindings insert; history_word_prefix_inline_bindings\r')
            shell.send(('bind --mode insert ctrl-x ' + fish_quote(capture)).encode() + b'\r')
            shell.send(b'pod c\x1b[A')
            shell.send(b'\x1b[C')
            shell.send(b'e'); expect('podman compose e')
            print(f'Inline preview PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'inline-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_leading_assignments():
    with tempfile.TemporaryDirectory(prefix='hwp-assignments-') as directory:
        shell = FishPTY(directory)
        state_file = shell.directory / 'state'
        checks = 0
        try:
            shell.send(b'history_word_prefix_inline_bindings\r')
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       f'printf "%s %s\\n" (commandline --cursor) "$fish_bind_mode" >> {fish_quote(state_file)}')
            shell.observe('ctrl-x', capture)

            def expect(buffer, cursor=None, mode='hwp_inline'):
                nonlocal checks
                shell.expect(buffer)
                state = state_file.read_text().splitlines()[-1].split()
                assert int(state[0]) == (len(buffer) if cursor is None else cursor), state
                assert state[1] == mode, state
                checks += 1

            env_prefix = 'PATH=/bin LD_LIBRARY_PATH=/lib '
            command = env_prefix + 'podman compose ps --all'
            plain = 'podman compose logs'
            shell.load([plain, command])
            for query, prefix in [
                ('p c ps', env_prefix + 'podman compose ps '),
                ('PATH p c', env_prefix + 'podman compose '),
                ('P L ps', env_prefix + 'podman compose ps '),
                ('L p', env_prefix + 'podman '),
            ]:
                shell.send(query.encode() + b'\x1b[A'); expect(command, len(prefix))
                shell.send(b'\x1b'); shell.drain(0.1)
                expect(query, mode='default')
                shell.send(b'\x03')
            for query in ['L P', 'c ps']:
                shell.send(query.encode() + b'\x1b[A'); expect(query, mode='default')
                shell.send(b'\x03')
            # Completing and editing preserve the skipped environment words.
            shell.send(b'p c\x1b[Aps')
            expect(env_prefix + 'podman compose ps', mode='default')
            shell.send(b'\x1b[A'); expect(command, len(env_prefix + 'podman compose ps '))
            shell.send(b'\x1b[C'); expect(command, mode='default')
            # Deletion still uses the original full history entry.
            shell.send(b'\x03')
            shell.send(b'p c\x1b[A\x1bd'); expect(plain, len('podman compose '))
            shell.send(b'\x03')
            shell.send(b'p c ps\x1b[A'); expect('p c ps', mode='default')
            print(f'Leading assignments PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'assignments-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_delete():
    with tempfile.TemporaryDirectory(prefix='hwp-delete-') as directory:
        shell = FishPTY(directory, private=False)
        history_file = shell.directory / 'history-snapshot'
        checks = 0
        try:
            shell.send(b'history_word_prefix_inline_bindings\r')
            capture = f'__hwp_buffer >> {fish_quote(shell.snapshots)}; commandline --function repaint'
            shell.observe('ctrl-x', capture)
            inspect = f'builtin history search --null > {fish_quote(history_file)}'
            shell.observe('ctrl-o', inspect)

            def expect(buffer):
                nonlocal checks
                shell.expect(buffer)
                checks += 1

            def entries():
                shell.send(b'\x0f')
                return [v.decode() for v in history_file.read_bytes().split(b'\0')[:-1]]

            old = 'podman compose exec -it mycontainer /bin/sh'
            logs = 'podman compose logs'
            cat = 'podman exec mycontainer cat file'
            delete = b'\x1b[3;2~'
            shell.load([old, logs, cat])
            shell.send(b'pod c\x1b[A' + delete)
            expect(logs)
            assert cat not in entries() and old in entries(); checks += 1
            # Deletion is immediately persisted, visible in a separate shell.
            env = dict(os.environ, fish_history='hwp_test',
                       XDG_CONFIG_HOME=str(shell.directory / 'config'),
                       XDG_DATA_HOME=str(shell.directory / 'data'))
            persisted = subprocess.run(['fish', '--no-config', '-c', 'builtin history search --null'],
                                       env=env, capture_output=True, check=True).stdout
            assert cat.encode() + b'\0' not in persisted
            assert logs.encode() + b'\0' in persisted; checks += 1
            # Delete an item already followed by cached matches.
            shell.send(b'\x1b[A\x1b[B' + delete)
            expect(old)
            shell.send(b'\x1b[B'); expect('pod c')
            shell.send(b'\x1b[A' + delete); expect('pod c')
            assert not any(v in entries() for v in [old, logs, cat]); checks += 1
            # Removing the oldest match falls back to a newer one.
            shell.load([old, logs, cat])
            shell.send(b'pod c\x1b[A\x1b[A\x1b[A' + delete); expect(logs)
            shell.send(b'\x1b[B'); expect(cat)
            # An appended completion space is never used as the delete key.
            padded = 'podman compose '
            shell.load([padded, 'podman compose'])
            shell.send(b'pod c\x1b[A' + delete); expect(padded)
            assert 'podman compose' not in entries() and padded in entries(); checks += 1
            # Exact matching preserves similar commands and case variants.
            shell.load(['Tool a.b [x]', 'tool a.b [x] suffix', 'tool a.b [x]'])
            shell.send(b'tool a.\x1b[A' + delete); expect('tool a.b [x] suffix')
            assert 'Tool a.b [x]' in entries() and 'tool a.b [x]' not in entries(); checks += 1
            # Multiline raw entries, including their final LF, delete exactly.
            multi = 'podman compose\nexec mycontainer\n'
            shell.load([old, multi])
            shell.send(b'pod c\x1b[A' + delete); expect(old)
            assert multi not in entries(); checks += 1
            # A pasted edit invalidates the candidate: Shift-Delete does not
            # delete the previously displayed history entry behind the edit.
            shell.send(b'\x1b[200~e\x1b[201~' + delete)
            assert old in entries(); checks += 1
            # Reproduce a terminal dropping the Shift modifier: ordinary
            # Delete commits the prefix, and the same history reappears. Alt-D
            # must really remove the original typo instead.
            bad_git = 'git commit --amend--no-edit'
            good_git = 'git commit --amend --no-edit'
            shell.load([good_git, bad_git])
            shell.send(b'git c\x1b[A\x1b[3~'); expect('git commit ')
            assert bad_git in entries(); checks += 1
            shell.send(b'\x1b[A'); expect(bad_git)
            shell.send(b'\x1bd'); expect(good_git)
            assert bad_git not in entries() and good_git in entries(); checks += 1
            persisted = subprocess.run(['fish', '--no-config', '-c', 'builtin history search --null'],
                                       env=env, capture_output=True, check=True).stdout
            assert bad_git.encode() + b'\0' not in persisted; checks += 1
            shell.send(b'\x03')
            shell.send(b'git c\x1b[A'); expect(good_git)
            print(f'Delete current history PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'delete-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_cycles():
    with tempfile.TemporaryDirectory(prefix='hwp-cycle-') as directory:
        shell = FishPTY(directory)
        cursor_file = shell.directory / 'cursor'
        checks = 0
        try:
            shell.send(b'history_word_prefix_inline_bindings --cycle\r')
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       f'printf "%s\\n" (commandline --cursor) >> {fish_quote(cursor_file)}; '
                       'commandline --function repaint')
            shell.observe('ctrl-x', capture)

            def expect(buffer, cursor=None):
                nonlocal checks
                shell.expect(buffer)
                if cursor is not None:
                    actual = int(cursor_file.read_text().splitlines()[-1])
                    assert actual == cursor, (actual, cursor, buffer)
                checks += 1

            old = 'podman compose exec -it mycontainer /bin/sh'
            logs = 'podman compose logs'
            cat = 'podman exec mycontainer cat file'
            query = 'pod c'
            reverse = b'\x1b[B'
            shell.load([old, logs, cat])
            shell.send(query.encode())
            for expected in [cat, logs, old, query, cat, logs, old, query]:
                shell.send(b'\x1b[A'); expect(expected)
            # The original input is a real ring position in both directions.
            for expected in [old, logs, cat, query, old, logs, cat, query]:
                shell.send(reverse); expect(expected)
            # Starting with Down must scan to the oldest match.
            shell.send(b'\x03')
            shell.send(query.encode() + reverse); expect(old)
            shell.send(b'\x1b[A'); expect(query, len(query))
            shell.send(b'\x1b[A'); expect(cat)
            # Deletion after wrapping must neither resurrect the item nor
            # strand the ring at a removed index.
            shell.send(reverse + reverse + b'\x1bd'); expect(logs)
            shell.send(b'\x1b[A'); expect(query)
            shell.send(reverse); expect(logs)
            shell.send(reverse); expect(cat)
            shell.send(reverse); expect(query)
            # Input on the original ring position starts a fresh search.
            shell.send(b'a'); expect('pod ca')
            shell.send(b'\x1b[A'); expect(cat)
            shell.send(b'\x1b[A'); expect('pod ca')
            # A singleton alternates with the original, even in reverse.
            shell.load([logs])
            shell.send(query.encode() + b'\x1b[A'); expect(logs)
            shell.send(b'\x1b[A'); expect(query)
            shell.send(reverse); expect(logs)
            shell.send(reverse); expect(query)
            # Preserve the suffix and original cursor through either wrap.
            shell.load([old, logs, cat])
            original = query + ' unrelated-suffix'
            shell.send(original.encode())
            shell.send(b'\x1b[D' * len(' unrelated-suffix'))
            shell.send(b'\x1b[A' * 4); expect(original, len(query))
            shell.send(reverse); expect(old)
            shell.send(b'\x1b[A'); expect(original, len(query))
            # Ordinary typing on the origin must not erase its existing tail.
            shell.send(b'X'); expect('pod cX unrelated-suffix', len(query) + 1)
            # Arrow movement on the original input is normal character motion.
            shell.send(b'\x03')
            shell.send(original.encode())
            shell.send(b'\x1b[D' * len(' unrelated-suffix'))
            shell.send(b'\x1b[A' * 4 + b'\x1b[D'); expect(original, len(query) - 1)
            shell.send(b'\x1b[A'); expect(cat, len('podman '))
            shell.send(b'\x1b'); shell.drain(0.1)
            expect(original, len(query) - 1)
            # No matching history leaves the input intact even with cycling.
            shell.load([])
            shell.send(b'no_hwp_match' + reverse); expect('no_hwp_match')
            shell.send(b'\x1b[A'); expect('no_hwp_match')
            # The origin can never delete a history record, and wrapping must
            # preserve vi insert-mode cursor placement and subsequent typing.
            shell.send(b'\x03')
            shell.send(b'fish_vi_key_bindings insert; history_word_prefix_inline_bindings --cycle\r')
            shell.send(('bind --mode insert ctrl-x ' + fish_quote(capture)).encode() + b'\r')
            shell.load([logs])
            shell.send(query.encode() + reverse); expect(logs, len('podman compose '))
            shell.send(reverse + b'\x1bd'); expect(query, len(query))
            shell.send(reverse); expect(logs)
            shell.send(b'\x1b[A'); expect(query, len(query))
            shell.send(b'a'); expect('pod ca', len('pod ca'))
            print(f'Circular inline PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'cycle-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_arrow_contexts():
    with tempfile.TemporaryDirectory(prefix='hwp-arrows-') as directory:
        shell = FishPTY(directory)
        state_file = shell.directory / 'reader-state'
        cursor_file = shell.directory / 'inject-cursor'
        checks = 0
        up, down = b'\x1b[A', b'\x1b[B'
        try:
            shell.send(b'history_word_prefix_inline_bindings --cycle\r')
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       'set -l paging 0; commandline --paging-mode; and set paging 1; '
                       f'printf "%s %s %s %s\\n" (commandline --cursor) (commandline --line) $paging "$fish_bind_mode" >> {fish_quote(state_file)}; '
                       'commandline --function repaint')
            shell.observe('ctrl-x', capture)
            inject_code = ('function __context_inject; commandline --replace -- (cat '
                           + fish_quote(shell.directory / 'injected-query')
                           + ' | string split0); commandline --cursor (cat '
                           + fish_quote(cursor_file) + '); end; bind ctrl-y __context_inject')
            shell.send(inject_code.encode() + b'\r')
            words = [f'choice_{i:02}_long_completion' for i in range(16)]
            shell.send(('complete --command hwp_demo --no-files --keep-order --arguments '
                        + fish_quote(' '.join(words))).encode() + b'\r')
            old = 'podman compose exec -it mycontainer /bin/sh'
            logs = 'podman compose logs'
            shell.load([old, logs])

            def inject(buffer, cursor=None):
                shell.send(b'\x03')
                (shell.directory / 'injected-query').write_text(buffer)
                cursor_file.write_text(str(len(buffer) if cursor is None else cursor))
                shell.send(b'\x19')

            def expect(buffer=None, line=None, paging=None, mode=None):
                nonlocal checks
                actual = shell.snapshot()
                state = state_file.read_text().splitlines()[-1].split()
                if buffer is not None:
                    assert actual == buffer, (actual, buffer)
                if line is not None:
                    assert int(state[1]) == line, (state, line)
                if paging is not None:
                    assert int(state[2]) == paging, (state, paging)
                if mode is not None:
                    assert state[3] == mode, (state, mode)
                checks += 1
                return actual

            # Vertical editing is untouched away from the history boundaries.
            multi = 'echo first\nsecond\nthird'
            inject(multi, multi.index('second') + 2)
            shell.send(up); expect(multi, line=1, mode='default')
            shell.send(down); expect(multi, line=2, mode='default')
            shell.send(down); expect(multi, line=3, mode='default')
            shell.send(up); expect(multi, line=2, mode='default')
            original = 'pod c\nleave untouched'
            inject(original, len('pod c'))
            shell.send(down); expect(original, line=2, mode='default')
            inject(original, len('pod c'))
            shell.send(up); expect(logs, mode='hwp_inline')
            shell.send(b'\x1b'); shell.drain(0.1)
            expect(original, line=1, mode='default')
            # A trailing blank line counts as the bottom line for Down.
            inject('pod c\n')
            shell.send(down); expect(old, mode='hwp_inline')
            # No history match must never invoke ordinary completion from Up.
            completion_file = shell.directory / 'alphafile'
            completion_file.touch()
            partial = 'cat ' + str(shell.directory / 'alpha')
            inject(partial)
            shell.send(up); expect(partial, paging=0, mode='default')
            shell.send(b'\t'); expect('cat ' + str(completion_file) + ' ', mode='default')
            # Native completion pager arrows choose rows rather than history.
            inject('hwp_demo ')
            shell.send(b'\t\t')
            first = expect(paging=1, mode='default')
            shell.send(down)
            second = expect(paging=1, mode='default')
            assert second != first, (first, second)
            shell.send(up); expect(first, paging=1, mode='default')
            # Ctrl-R remains the original history pager.
            inject('pod')
            shell.send(b'\x12'); expect(paging=1, mode='default')
            shell.send(down); expect(paging=1, mode='default')
            # Existing user-defined Tab/Shift-Tab/Ctrl-R survive setup and
            # are invoked after committing the prefix from inline preview.
            shell.send(b'\x03')
            for key, marker in [('tab', 'CUSTOM'), ('shift-tab', 'BACK'), ('ctrl-r', 'FIND')]:
                command = ('bind ' + key + ' ' + fish_quote('commandline --insert ' + marker))
                shell.send(command.encode() + b'\r')
            shell.send(b'history_word_prefix_inline_bindings --cycle\r')
            for key, marker in [(b'\t', 'CUSTOM'), (b'\x1b[Z', 'BACK'), (b'\x12', 'FIND')]:
                inject('pod c')
                shell.send(up + key)
                expect('podman compose ' + marker, mode='default')
            # Multiline candidates keep searching while the preview is active.
            shell.load(['podman compose\nexec mycontainer'])
            inject('pod c e')
            shell.send(up); expect('podman compose\nexec mycontainer', line=2, mode='hwp_inline')
            shell.send(up); expect('pod c e', mode='hwp_inline')
            shell.send(down); expect('podman compose\nexec mycontainer', mode='hwp_inline')
            print(f'Native arrow contexts PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'arrow-context-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_native_empty_queries():
    with tempfile.TemporaryDirectory(prefix='hwp-native-empty-') as directory:
        shell = FishPTY(directory)
        state_file = shell.directory / 'state'
        checks = 0
        up, down = b'\x1b[A', b'\x1b[B'
        try:
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       'set -l searching 0; commandline --search-mode; and set searching 1; '
                       f'printf "%s %s %s\\n" (commandline --cursor) "$fish_bind_mode" $searching >> {fish_quote(state_file)}')
            shell.observe('ctrl-x', capture)

            def expect(buffer, cursor=None, mode='default', searching=None):
                nonlocal checks
                shell.expect(buffer)
                state = state_file.read_text().splitlines()[-1].split()
                assert int(state[0]) == (len(buffer) if cursor is None else cursor), state
                assert state[1] == mode, state
                if searching is not None:
                    assert int(state[2]) == searching, state
                checks += 1

            old, multi, recent = 'one', 'echo first\necho second', 'recent command'
            for option in ['', ' --cycle']:
                shell.send(('history_word_prefix_inline_bindings' + option).encode() + b'\r')
                for query in ['', '   ', '\t \t']:
                    shell.load([old, multi, recent])
                    if query:
                        (shell.directory / 'injected-query').write_bytes(query.encode() + b'\0')
                        shell.send(b'\x19')
                    # Native browsing never wraps, even with --cycle enabled.
                    for expected in [recent, multi, old, old]:
                        shell.send(up); expect(expected, searching=1)
                    for expected in [multi, recent, '', '']:
                        shell.send(down); expect(expected)
                    shell.send(up); expect(recent, searching=1)
                shell.send(b'\x03')
            # Down on whitespace and either direction with no history stay
            # empty, without entering the preview mode.
            shell.load([recent])
            shell.send(b'   ' + down); expect('')
            shell.send(up); expect(recent)
            shell.load([])
            shell.send(b'   ' + up); expect('')
            shell.send(down); expect('')
            # Multiline whitespace still follows native vertical movement
            # until the top line is reached for a history search.
            shell.load([recent])
            (shell.directory / 'injected-query').write_bytes(b' \n \0')
            shell.send(b'\x19')
            shell.send(up); expect(' \n ', cursor=1)
            shell.send(up); expect(recent)
            # Editing a native result keeps the entire command. Once edited,
            # a later word-prefix request can enter the custom preview again.
            shell.load(['podman compose logs'])
            shell.send(up + b'X'); expect('podman compose logsX', searching=0)
            shell.send(b'\x15pod c' + up)
            expect('podman compose logs', cursor=len('podman compose '), mode='hwp_inline')
            # Enter executes the complete native history item, not just its
            # first word. The only effect is a file in this isolated fixture.
            sentinel = shell.directory / 'executed'
            command = 'printf native-history > ' + str(sentinel)
            shell.load([command])
            shell.send(up); expect(command)
            shell.send(b'\r'); expect('')
            assert sentinel.read_text() == 'native-history'; checks += 1
            # Native browsing keeps vi insert mode and the cursor at the end.
            shell.send(b'fish_vi_key_bindings insert; history_word_prefix_inline_bindings --cycle\r')
            shell.load([recent])
            shell.send(up); expect(recent, mode='insert')
            shell.send(up); expect(recent, mode='insert')
            shell.send(down); expect('', mode='insert')
            print(f'Native empty-query history PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'native-empty-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_startup_bindings():
    with tempfile.TemporaryDirectory(prefix='hwp-startup-') as directory:
        directory = Path(directory)
        config = directory / 'config/fish'
        (config / 'conf.d').mkdir(parents=True)
        (config / 'conf.d/history_word_prefix.fish').symlink_to(SOURCE)
        (config / 'config.fish').write_text(
            'if status is-interactive\n    history_word_prefix_inline_bindings --cycle\nend\n')
        env = dict(os.environ, TERM='xterm-256color',
                   XDG_CONFIG_HOME=str(directory / 'config'),
                   XDG_DATA_HOME=str(directory / 'data'),
                   XDG_CACHE_HOME=str(directory / 'cache'))
        command = ('printf "cycle=%s\\n" "$__hwi_cycle"; '
                   'bind --user --mode default up; '
                   'bind --user --mode hwp_inline shift-delete')
        proc = subprocess.run(['fish', '--private', '--interactive', '--command', command],
                              env=env, capture_output=True, check=True)
        assert not proc.stderr, proc.stderr.decode()
        assert b'cycle=1\n' in proc.stdout, proc.stdout
        assert b'__hwi_arrow older' in proc.stdout, proc.stdout
        assert b'__hwi_delete' in proc.stdout, proc.stdout
        print('Fresh configuration startup: 4 assertions passed')


def check_custom_history_keys():
    script = f'''source {fish_quote(SOURCE)}
bind up 'commandline --insert UP'
bind --mode insert up 'commandline --insert VI_UP'
bind --sets-mode insert ctrl-p 'commandline --insert A' 'commandline --insert B'
bind --mode insert ctrl-p 'commandline --insert VI_P'
set -l saved (bind --user --mode default ctrl-p | string match -- 'bind *' | string collect)
history_word_prefix_inline_bindings
history_word_prefix_inline_bindings --older-key ctrl-p --newer-key ctrl-n --cycle
string match --quiet '* UP*' (bind --user --mode default up); or exit 10
string match --quiet '* VI_UP*' (bind --user --mode insert up); or exit 11
history_word_prefix_inline_bindings --older-key '' 2>/dev/null
test $status = 2; or exit 12
history_word_prefix_inline_bindings --older-key ctrl-p --newer-key ctrl-p 2>/dev/null
test $status = 2; or exit 13
history_word_prefix_inline_bindings --newer-key 2>/dev/null
test $status = 2; or exit 14
history_word_prefix_inline_bindings --older-key ctrl- 2>/dev/null
test $status = 2; or exit 22
history_word_prefix_inline_bindings --help >/dev/null
test "$__hwi_older_key" = ctrl-p; and test "$__hwi_newer_key" = ctrl-n; and test "$__hwi_cycle" = 1; or exit 15
history_word_prefix_inline_bindings --older-key alt-k --newer-key alt-j
test "$saved" = (bind --user --mode default ctrl-p | string match -- 'bind *' | string collect); or exit 16
string match --quiet '* VI_P*' (bind --user --mode insert ctrl-p); or exit 17
if bind --user --mode default ctrl-n >/dev/null 2>&1; exit 18; end
bind alt-k 'commandline --insert MANUAL'
history_word_prefix_inline_bindings
string match --quiet '* MANUAL*' (bind --user --mode default alt-k); or exit 19
if bind --user --mode default alt-j >/dev/null 2>&1; exit 20; end
test "$__hwi_older_key" = up; and test "$__hwi_newer_key" = down; or exit 21
'''
    proc = subprocess.run(['fish', '--no-config', '--private', '-c', script],
                          capture_output=True, check=True,
                          env=dict(os.environ, TERM='xterm-256color'))
    assert not proc.stderr, proc.stderr
    checks = 13
    with tempfile.TemporaryDirectory(prefix='hwp-custom-keys-') as directory:
        shell = FishPTY(directory)
        state_file = shell.directory / 'state'
        try:
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       f'printf "%s %s\\n" (commandline --cursor) "$fish_bind_mode" >> {fish_quote(state_file)}')
            shell.observe('ctrl-x', capture)

            def expect(buffer, cursor=None, mode='hwp_inline'):
                nonlocal checks
                shell.expect(buffer)
                state = state_file.read_text().splitlines()[-1].split()
                assert int(state[0]) == (len(buffer) if cursor is None else cursor), state
                assert state[1] == mode, state
                checks += 1

            shell.send(b'history_word_prefix_inline_bindings --older-key ctrl-p --newer-key ctrl-n --cycle\r')
            old, logs = 'podman compose exec tail', 'podman compose logs'
            query = b'pod c'
            shell.load([old, logs])
            shell.send(query + b'\x1b[A'); expect('pod c', mode='default')
            shell.send(b'\x10'); expect(logs, len('podman compose '))
            shell.send(b'\x10'); expect(old, len('podman compose '))
            shell.send(b'\x0e'); expect(logs, len('podman compose '))
            shell.send(b'\x0e'); expect('pod c')
            shell.send(b'\x0e'); expect(old, len('podman compose '))
            shell.send(b'\x1b'); shell.drain(0.1)
            expect('pod c', mode='default')
            shell.send(b'\x10\x1b[C'); expect(logs, mode='default')
            # Empty input uses native history browsing on the configured keys.
            shell.send(b'\x03')
            shell.send(b'\x10'); expect(logs, mode='default')
            shell.send(b'\x0e'); expect('', mode='default')
            # Either flag can be used alone; swapped arrow keys also work.
            for options, older, newer in [
                ('--older-key alt-k', b'\x1bk', b'\x1b[B'),
                ('--newer-key alt-j', b'\x1b[A', b'\x1bj'),
                ('--older-key down --newer-key up', b'\x1b[B', b'\x1b[A'),
                ('--older-key ctrl-k,ctrl-p --newer-key ctrl-k,ctrl-n', b'\x0b\x10', b'\x0b\x0e'),
            ]:
                shell.send(b'\x03')
                shell.send(('history_word_prefix_inline_bindings ' + options).encode() + b'\r')
                shell.load([old, logs])
                shell.send(query + older); expect(logs, len('podman compose '))
                shell.send(newer); expect('pod c')
            shell.send(b'\x03')
            shell.send(b'fish_vi_key_bindings insert; history_word_prefix_inline_bindings --older-key ctrl-p --newer-key ctrl-n\r')
            shell.load([logs])
            shell.send(query + b'\x10'); expect(logs, len('podman compose '))
            shell.send(b'\x1b[C'); expect(logs, mode='insert')
            print(f'Custom history keys: {checks} assertions passed')
        finally:
            (LOG_DIR / 'custom-keys-transcript.txt').write_bytes(shell.output)
            shell.close()


def check_options_and_editing():
    script = f'''source {fish_quote(SOURCE)}
history_word_prefix_inline_bindings
test "$__hwi_cycle" = 0; or exit 10
history_word_prefix_inline_bindings --cycle
test "$__hwi_cycle" = 1; or exit 11
history_word_prefix_inline_bindings --invalid 2>/dev/null
test $status = 2; or exit 12
test "$__hwi_cycle" = 1; or exit 13
history_word_prefix_inline_bindings --help >/dev/null
test "$__hwi_cycle" = 1; or exit 14
history_word_prefix_inline_bindings unexpected 2>/dev/null
test $status = 2; or exit 15
test "$__hwi_cycle" = 1; or exit 16
history_word_prefix_inline_bindings
test "$__hwi_cycle" = 0; or exit 17
set -l overrides (bind --user --mode default | string match -- 'bind *')
test (count $overrides) = 2; or exit 18
string match --quiet '*up*__hwi_arrow older*' $overrides; or exit 19
string match --quiet '*down*__hwi_arrow newer*' $overrides; or exit 20
if bind --user --mode hwp_inline Defined >/dev/null 2>&1; exit 21; end
'''
    proc = subprocess.run(['fish', '--no-config', '--private', '-c', script],
                          capture_output=True, check=True,
                          env=dict(os.environ, TERM='xterm-256color'))
    assert not proc.stderr, proc.stderr
    checks = 12
    with tempfile.TemporaryDirectory(prefix='hwp-options-') as directory:
        shell = FishPTY(directory)
        state_file = shell.directory / 'mode'
        up, down = b'\x1b[A', b'\x1b[B'
        try:
            shell.send(b'history_word_prefix_inline_bindings\r')
            capture = (f'__hwp_buffer >> {fish_quote(shell.snapshots)}; '
                       f'printf "%s\\n" "$fish_bind_mode" >> {fish_quote(state_file)}')
            shell.observe('ctrl-x', capture)

            def expect(buffer, mode=None):
                nonlocal checks
                shell.expect(buffer)
                if mode is not None:
                    assert state_file.read_text().splitlines()[-1] == mode
                checks += 1

            old, recent, query = 'podman compose exec tail', 'podman compose logs', 'pod c'
            shell.load([old, recent])
            # Default: initial Down stays put; oldest never wraps.
            shell.send(query.encode() + down); expect(query, 'default')
            for expected in [recent, old, old, old]:
                shell.send(up); expect(expected, 'hwp_inline')
            for expected in [recent, query, query, query]:
                shell.send(down); expect(expected)
            # Rebinding changes the option and resets the search state.
            shell.send(b'\x03')
            shell.send(b'history_word_prefix_inline_bindings -c\r')
            shell.send(query.encode() + down); expect(old)
            shell.send(up); expect(query)
            shell.send(b'\x03')
            shell.send(b'history_word_prefix_inline_bindings\r')
            shell.send(query.encode() + down); expect(query)
            shell.send(up * 3); expect(old)
            # Compare native Ctrl-W's path semantics with the same prefix
            # in preview, rather than mirroring the implementation in a test.
            shell.send(b'\x03')
            shell.send(b'tool /tmp/alpha.beta/file \x17')
            native = shell.snapshot()
            shell.load(['tool /tmp/alpha.beta/file tail'])
            shell.send(b'to /t' + up + b'\x17'); expect(native, 'default')
            # User actions added after setup must be inherited on next use;
            # preserve multiple commands, literal shell text and --sets-mode.
            shell.send(b'\x03')
            action = 'commandline --insert -- ' + fish_quote('literal $() `tick` \\ value')
            shell.send(('bind alt-q ' + fish_quote(action)).encode() + b'\r')
            shell.send(b'to /t' + up + b'\x1bq')
            expect('tool /tmp/alpha.beta/file literal $() `tick` \\ value', 'default')
            shell.send(b'\x03')
            shell.send(b"bind --sets-mode insert alt-q 'commandline --insert ONE' 'commandline --insert TWO'\r")
            shell.send(b'to /t' + up + b'\x1bq')
            expect('tool /tmp/alpha.beta/file ONETWO', 'insert')
            print(f'Options and inherited editing PTY: {checks} assertions passed')
        finally:
            (LOG_DIR / 'options-transcript.txt').write_bytes(shell.output)
            shell.close()


if __name__ == '__main__':
    check_matcher()
    check_prefixes()
    check_inline()
    check_leading_assignments()
    check_delete()
    check_cycles()
    check_arrow_contexts()
    check_native_empty_queries()
    check_startup_bindings()
    check_options_and_editing()
    check_custom_history_keys()
