# Fish inline history completion

[简体中文](README.zh-CN.md)

Search history by word prefixes, skipping intervening arguments. For example, `pod c e m` matches `podman compose exec -it mycontainer /bin/sh`. Preview the full command inline while completing only through the last matched word, then keep typing or accept the whole command.

## Installation

Requires Fish 4.0+ and `seq`. Place the complete [history_word_prefix.fish](history_word_prefix.fish) script in your Fish configuration directory's `conf.d/` folder, then add this to `config.fish`:

```fish
if status is-interactive
    history_word_prefix_inline_bindings
end
```

Open a new Fish session to activate it. To try it in the current session, run these commands from the script's directory:

```fish
source ./history_word_prefix.fish
history_word_prefix_inline_bindings
```

Browsing stops at the ends by default. Use `history_word_prefix_inline_bindings --cycle` to cycle in both directions, including the original input. With cycling enabled, pressing ↓ first jumps to the oldest match. Call the binding function again to change the setting; use `--help` for help.

Use `--older-key` and `--newer-key` to choose keys using Fish `bind` names. They default to `up` and `down`:

```fish
history_word_prefix_inline_bindings --older-key ctrl-p --newer-key ctrl-n --cycle
```

The keys work in both normal editing and previews. Changing keys restores their previous bindings while preserving any manual edits made since setup. Omitting the key options restores the defaults. Below, ↑/↓ refer to these two configured keys; other keys retain their original bindings.

## Usage

Empty or whitespace-only input uses Fish's native history browsing on ↑/↓, with the cursor at the end and Enter running the whole command. Whitespace is treated as empty input; word-prefix previews and cycling do not apply.

Type `pod c` and press ↑. A preview might look like this, with `▏` marking the cursor and the highlighted remainder to its right:

```text
podman compose ▏logs
```

Type `e` to get `podman compose e▏`, then press ↑ again to match `podman compose exec ▏-it mycontainer /bin/sh`.

| Key | Action |
| --- | --- |
| ↑ / ↓ | Browse matches, newest first; move toward older / newer entries |
| → / End | Accept the whole preview without running it |
| Typing, pasting, or other editing keys | Remove the preview remainder, then perform the original editing action |
| Enter | By default, run only the completed prefix; press → or End first to run the whole command |
| Esc / Ctrl-G | Cancel the preview and restore the original input and cursor |
| Alt-D / Shift-Delete | Delete the full history entry behind the current preview and save history |

Only text before the cursor is used for matching. Repeated ↑/↓ keeps the same query; after editing or moving the cursor, the next request starts a new search. If nothing matches, the original input stays intact.

Leading words containing `=` may be skipped or included in matching. For example, `PATH=/bin LD_LIBRARY_PATH=/lib podman compose ps` matches `p c ps`, `PATH p c`, and `P L ps`, but not `L P` or `c ps`. Only these leading words may be skipped before the first match; completion preserves the full environment settings.

In normal editing, only the history-search branch of the chosen keys in `default` and `insert` modes is replaced. Multiline editing searches on ↑ from the first line or ↓ from the last line; other lines retain vertical movement. Fish pager navigation stays intact. Other keys run their original actions after removing the preview remainder.

Deletion uses an exact, case-sensitive full-command match and removes identical entries together. Cancelling the preview does not undo deletion. Prefer Alt-D: some terminals send Shift-Delete as plain Delete. Check with `fish_key_reader`.

## Limitations

- Matching is case-sensitive. The first word matches from the start of the command, optionally skipping leading words containing `=`; later words must stay in order. Words are separated by whitespace, without parsing quotes or Fish syntax: `m` does not match `"mycontainer"`, and `sh` does not match `/bin/sh`.
- The preview uses real command-line text and a selection, so plugins reading the buffer see the whole candidate. Search is triggered by ↑/↓ and does not refresh with every keystroke.

## Tests

GitHub Actions runs syntax checks, startup checks, and PTY interaction tests with isolated histories on Fish 4.1.2, 4.5.0, and 4.9.3. It runs on pushes, pull requests, and manual dispatch, saving terminal logs on failure.

To run locally on Linux with Fish and Python 3 installed, use `python3 tests/test_history_word_prefix.py`. Terminal logs are saved in `test-logs/`; tests do not modify your history.
