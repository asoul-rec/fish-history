# Fish 4.0+: source, then history_word_prefix_inline_bindings [--cycle].
# Matching uses whitespace-delimited words, not a shell parser.

function __hwp_pattern --argument-names query
    set -l words (string match --all --regex '\S+' -- "$query")
    set -l escaped (string escape --style=regex -- $words)
    set -l joined (string join '.*?\s+' -- $escaped)
    printf '%s%s\n' '(?s)^\s*' "$joined"
end

function __hwp_reset --on-event fish_prompt --on-event fish_preexec
    set --erase __hwp_query __hwp_rendered __hwp_matches __hwp_index __hwp_cursor
    set --erase __hwp_candidates __hwp_scanned __hwp_regex
end

# Remove just the newline printed by commandline, preserving the user's ones.
function __hwp_buffer
    set -l raw (commandline --current-buffer $argv | string collect --allow-empty --no-trim-newlines)
    set -l parts (string split --right --max 1 -- \n "$raw")
    printf '%s\0' "$parts[1]"
end

# Return the command through the end of the last matched word, not its prefix.
function __hwp_prefix --argument-names query candidate
    set -l accepted
    set -l pattern (__hwp_pattern "$query")
    if string match --quiet --regex -- "(?<accepted>$pattern\S*)" "$candidate"
        printf '%s\0' "$accepted"
    else
        return 1
    end
end

# Continue the frozen history scan, stopping at one match unless asked for all.
function __hwp_scan --argument-names amount
    for candidate_index in (seq (math "$__hwp_scanned + 1") (count $__hwp_candidates))
        set --global __hwp_scanned $candidate_index
        if string match --quiet --regex -- "$__hwp_regex" "$__hwp_candidates[$candidate_index]"
            set --global --append __hwp_matches "$__hwp_candidates[$candidate_index]"
            test "$amount" = all; or break
        end
    end
end

# Search cursor and frozen candidates are separate from the preview state.
function __hwp_move --argument-names direction cycle
    contains -- "$direction" older newer; or return 2
    contains -- "$cycle" 0 1; or return 2
    set -l buffer (__hwp_buffer | string split0)

    if not set --query __hwp_rendered; or test "$buffer" != "$__hwp_rendered"
        __hwp_reset
        if test "$direction" != older; and test "$cycle" -eq 0
            return
        end
        set --global __hwp_query "$buffer"
        set --global __hwp_cursor (commandline --cursor)
        set --global __hwp_index 0
        set --global __hwp_rendered "$buffer"
        set --global __hwp_scanned 0
        set --global __hwp_regex (__hwp_pattern "$buffer")
        set -l words (string match --all --regex '\S+' -- "$buffer")
        if set --query words[1]
            # Cheap literal prefilter inside Fish before the custom matcher.
            set --global __hwp_candidates (builtin history search --null --case-sensitive --contains -- "$words[1]" | string split0)
            set --global __hwp_matches
        else
            set --global __hwp_matches (builtin history search --null | string split0)
            set --global __hwp_candidates
        end
    end

    set -l next $__hwp_index
    if test "$direction" = older
        set next (math "$next + 1")
        if test "$next" -gt (count $__hwp_matches)
            # Scan only until the next match; keep both results and progress.
            __hwp_scan one
            if test "$cycle" -eq 1; and test "$next" -gt (count $__hwp_matches)
                set next 0
            end
        end
    else
        set next (math "$next - 1")
        if test "$cycle" -eq 1; and test "$next" -lt 0
            __hwp_scan all
            set next (count $__hwp_matches)
        end
    end
    if test "$next" -lt 0; or test "$next" -gt (count $__hwp_matches)
        return
    end

    set --global __hwp_index $next
    if test "$next" -eq 0
        commandline --replace -- "$__hwp_query"
        commandline --cursor $__hwp_cursor
    else
        commandline --replace -- "$__hwp_matches[$next]"
        commandline --cursor (string length -- "$__hwp_matches[$next]")
    end
    set --global __hwp_rendered (__hwp_buffer | string split0)
    commandline --function repaint
end

function __hwi_reset --on-event fish_prompt --on-event fish_preexec
    if set --query __hwi_mode; and test "$fish_bind_mode" = hwp_inline
        set --global fish_bind_mode "$__hwi_mode"
    end
    set --erase __hwi_mode __hwi_full __hwi_prefix __hwi_suffix __hwi_origin __hwi_origin_cursor __hwi_boundary __hwi_at_origin
end

# Leave preview before editing/executing. Even a bracketed paste may have
# inserted text directly into the reader: remove the old preview tail then.
function __hwi_finish --argument-names action
    set --query __hwi_full; or return
    commandline --function end-selection
    set -l buffer (__hwp_buffer | string split0)
    set -l cursor (commandline --cursor)
    if test "$fish_bind_mode" = hwp_inline
        set --global fish_bind_mode "$__hwi_mode"
    end
    if test "$action" = cancel
        commandline --replace -- "$__hwi_origin"
        commandline --cursor $__hwi_origin_cursor
    else if set --query __hwi_at_origin; and test "$__hwi_at_origin" -eq 1
        # There is no preview tail at the original input. Keep normal editing.
        commandline --cursor $cursor
    else if test "$action" = full; and test "$buffer" = "$__hwi_full"
        commandline --cursor (string length -- "$buffer")
    else
        set -l edited "$__hwi_prefix"
        if test "$buffer" = "$__hwi_full"; and test "$cursor" -ne "$__hwi_boundary"
            set edited (__hwp_buffer --cut-at-cursor | string split0)
        else if test "$buffer" != "$__hwi_full"
            set edited "$buffer"
            if test -n "$__hwi_suffix"
                set -l tail (string escape --style=regex -- "$__hwi_suffix")
                set -l retained
                if string match --quiet --regex -- "(?s)^(?<retained>.*)$tail\z" "$buffer"
                    set edited "$retained"
                else
                    set edited (__hwp_buffer --cut-at-cursor | string split0)
                end
            end
        end
        commandline --replace -- "$edited"
        commandline --cursor (math "min($cursor, "(string length -- "$edited")")")
    end
    __hwi_reset
    __hwp_reset
    commandline --function repaint
end

function __hwi_step --argument-names direction
    set -l original (__hwp_buffer | string split0)
    set -l original_cursor (commandline --cursor)
    set -l continuing 0
    if set --query __hwi_full
        set -l buffer (__hwp_buffer | string split0)
        if test "$buffer" != "$__hwi_full"
            __hwi_finish prefix
            set original (__hwp_buffer | string split0)
            set original_cursor (commandline --cursor)
        else if test (commandline --cursor) -ne "$__hwi_boundary"
            __hwi_reset
            commandline --function end-selection
        else
            set original "$__hwi_origin"
            set original_cursor $__hwi_origin_cursor
            set continuing 1
            __hwi_reset
        end
    else if commandline --paging-mode
        if test "$direction" = older
            commandline --function up-line
        else
            commandline --function down-line
        end
        return
    end
    set -l previous_mode "$fish_bind_mode"
    if test "$continuing" -eq 0
        set -l query (__hwp_buffer --cut-at-cursor | string split0)
        __hwp_reset
        commandline --replace -- "$query"
        commandline --cursor (string length -- "$query")
    end
    __hwp_move "$direction" "$__hwi_cycle"
    if not set --query __hwp_index; or test "$__hwp_index" -eq 0
        if set --query __hwp_matches[1]
            __hwi_show_origin "$original" "$original_cursor" "$previous_mode"
            return
        end
        commandline --replace -- "$original"
        commandline --cursor $original_cursor
        __hwp_reset
        commandline --function end-selection
        return
    end

    __hwi_render "$original" "$original_cursor" "$previous_mode"
end

function __hwi_show_origin --argument-names original original_cursor previous_mode
    __hwi_editor_bindings "$previous_mode"
    set --global __hwi_full "$original"
    set --global __hwi_prefix "$original"
    set --global __hwi_suffix ''
    set --global __hwi_origin "$original"
    set --global __hwi_origin_cursor $original_cursor
    set --global __hwi_boundary $original_cursor
    set --global __hwi_mode "$previous_mode"
    set --global __hwi_at_origin 1
    set --global __hwp_rendered "$original"
    set --global fish_bind_mode hwp_inline
    if functions --query __fish_vi_key_bindings_on_mode_change
        set --global fish_cursor_end_mode exclusive
    end
    commandline --replace -- "$original"
    commandline --cursor $original_cursor
    commandline --function end-selection suppress-autosuggestion repaint
end

function __hwi_render --argument-names original original_cursor previous_mode
    __hwi_editor_bindings "$previous_mode"
    set -l full (__hwp_buffer | string split0)
    set -l prefix (__hwp_prefix "$__hwp_query" "$full" | string split0)
    set -l boundary (string length -- "$prefix")
    # Include one separator after the completed word, ready for the next one.
    if test "$boundary" -eq (string length -- "$full")
        set full "$full "
    end
    set boundary (math "$boundary + 1")
    set --global __hwi_prefix ''
    set --global __hwi_suffix ''
    string match --quiet --regex -- "(?s)^(?<__hwi_prefix>.{$boundary})(?<__hwi_suffix>.*)\z" "$full"
    set --global __hwi_full "$full"
    set --global __hwi_mode "$previous_mode"
    set --global __hwi_origin "$original"
    set --global __hwi_origin_cursor $original_cursor
    set --global __hwi_boundary $boundary
    set --global __hwi_at_origin 0
    set --global __hwp_rendered "$full"
    set --global fish_bind_mode hwp_inline
    if functions --query __fish_vi_key_bindings_on_mode_change
        set --global fish_cursor_end_mode exclusive
    end
    commandline --replace -- "$full"
    commandline --cursor (string length -- "$full")
    commandline --function end-selection begin-selection
    for ignored in (seq (string length -- "$__hwi_suffix"))
        commandline --function backward-char
    end
    commandline --function suppress-autosuggestion repaint
end

function __hwi_accept --argument-names movement
    if set --query __hwi_at_origin; and test "$__hwi_at_origin" -eq 1
        __hwi_finish prefix
        commandline --function "$movement"
    else
        __hwi_finish full
    end
end

# Delete the original history text, never the truncated/space-padded preview.
function __hwi_delete
    set --query __hwi_full; or return
    set --query __hwp_index; or return
    test "$__hwp_index" -gt 0; or return
    set -l buffer (__hwp_buffer | string split0)
    test "$buffer" = "$__hwi_full"; or return

    set -l index $__hwp_index
    set -l entry "$__hwp_matches[$index]"
    builtin history delete --exact --case-sensitive -- "$entry"; or return
    builtin history save

    set -l original "$__hwi_origin"
    set -l original_cursor $__hwi_origin_cursor
    set -l previous_mode "$__hwi_mode"
    __hwi_reset
    commandline --function end-selection
    set --erase __hwp_matches[$index]
    set --global __hwp_index (math "$index - 1")
    # The removed entry is already before __hwp_scanned. Keeping that scan
    # position prevents it from reappearing through the frozen candidate list.
    if test "$__hwp_index" -gt 0
        commandline --replace -- "$__hwp_matches[$__hwp_index]"
    else
        commandline --replace -- "$__hwp_query"
    end
    set --global __hwp_rendered (__hwp_buffer | string split0)
    __hwp_move older 0
    if test "$__hwp_index" -gt 0
        __hwi_render "$original" "$original_cursor" "$previous_mode"
    else
        commandline --replace -- "$original"
        commandline --cursor $original_cursor
        __hwp_reset
        commandline --function suppress-autosuggestion repaint
    end
end

# Preserve the native up-or-search/down-or-search decisions for the pager and
# multiline editing. Only their history branch enters our inline search.
function __hwi_arrow --argument-names direction
    if commandline --paging-mode
        if test "$direction" = older
            commandline --function up-line
        else
            commandline --function down-line
        end
        return
    end
    if set --query __hwi_full
        set -l buffer (__hwp_buffer | string split0)
        if test "$buffer" = "$__hwi_full"; and test (commandline --cursor) -eq "$__hwi_boundary"
            __hwi_step "$direction"
            return
        else if test "$buffer" != "$__hwi_full"
            __hwi_finish prefix
        else
            __hwi_reset
            __hwp_reset
            commandline --function end-selection
        end
    end
    set -l line (commandline --line)
    set -l buffer (__hwp_buffer | string split0)
    set -l lines (string split -- \n "$buffer")
    if commandline --search-mode; or test "$direction" = older -a "$line" -eq 1; or test "$direction" = newer -a "$line" -eq (count $lines)
        __hwi_step "$direction"
    else if test "$direction" = older
        commandline --function up-line
    else
        commandline --function down-line
    end
end

# Inherit the actual parent bindings, including custom editing commands. The
# prefix cleanup runs before their original actions. bind escapes newlines in
# its output; read tokenizes that output without evaluating any command text.
function __hwi_editor_bindings --argument-names mode
    set -l bindings (bind --preset --mode "$mode"; bind --user --mode "$mode")
    set -l signature (string join \n -- $bindings | string collect)
    if set --query __hwi_bindings_mode; and test "$__hwi_bindings_mode" = "$mode"; and test "$__hwi_bindings_signature" = "$signature"
        return
    end
    bind --erase --all --user --preset --mode hwp_inline
    for binding in $bindings
        printf '%s\0' "$binding" | read --null --local --tokenize --array parts
        __hwi_copy_binding $parts[2..-1]
    end
    bind --mode hwp_inline up '__hwi_arrow older'
    bind --mode hwp_inline down '__hwi_arrow newer'
    bind --mode hwp_inline right '__hwi_accept forward-char'
    bind --mode hwp_inline end '__hwi_accept end-of-line'
    bind --mode hwp_inline escape '__hwi_finish cancel'
    bind --mode hwp_inline ctrl-g '__hwi_finish cancel'
    bind --mode hwp_inline shift-delete __hwi_delete
    bind --mode hwp_inline alt-d __hwi_delete
    # Terminfo aliases may win over their named equivalents for legacy input.
    bind --silent --mode hwp_inline --key up '__hwi_arrow older'
    bind --silent --mode hwp_inline --key down '__hwi_arrow newer'
    bind --silent --mode hwp_inline --key right '__hwi_accept forward-char'
    bind --silent --mode hwp_inline --key end '__hwi_accept end-of-line'
    bind --silent --mode hwp_inline --key sdc __hwi_delete
    set --global __hwi_bindings_mode "$mode"
    set --global __hwi_bindings_signature "$signature"
end

function __hwi_copy_binding
    argparse --stop-nonopt 'M/mode=' 'm/sets-mode=' k/key preset user s/silent -- $argv; or return
    set -l destination
    if set --query _flag_sets_mode
        set destination --sets-mode "$_flag_sets_mode"
    end
    if set --query _flag_key
        set --append destination --key
    end
    set -l level --user
    if set --query _flag_preset
        set level --preset
    end
    set -l key "$argv[1]"
    # read leaves legacy control sequences escaped. Fish 4.0 also serializes
    # a space in those sequences as its internal code, which must be decoded.
    if string match --quiet --regex -- '^\\\\e' "$key"
        set key (string replace --all --regex -- '\\\\uF50E' ' ' "$key")
        set key (string unescape -- "$key")
    end
    bind $level --mode hwp_inline $destination -- "$key" '__hwi_finish prefix' $argv[2..-1]
end

# Only Up/Down change in normal editing. Looping is an explicit opt-in.
function history_word_prefix_inline_bindings
    argparse --name=history_word_prefix_inline_bindings c/cycle h/help -- $argv; or return 2
    if set --query _flag_help
        printf '%s\n' 'Usage: history_word_prefix_inline_bindings [--cycle]' \
            '  --cycle  Wrap through the original input (default: stop at the ends).'
        return
    end
    if set --query argv[1]
        printf '%s\n' 'history_word_prefix_inline_bindings: unexpected argument' >&2
        return 2
    end
    __hwi_reset
    __hwp_reset
    set --global __hwi_cycle 0
    if set --query _flag_cycle
        set --global __hwi_cycle 1
    end
    set --erase __hwi_bindings_mode __hwi_bindings_signature
    for mode in default insert
        bind --mode $mode up '__hwi_arrow older'
        bind --mode $mode down '__hwi_arrow newer'
    end
    __hwi_editor_bindings "$fish_bind_mode"
end
