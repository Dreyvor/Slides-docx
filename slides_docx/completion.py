"""Shell completion support driven by the argparse command tree."""

import argparse
import os
from pathlib import Path


VIDEO_SUFFIXES = {
    ".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg",
    ".mts", ".ts", ".webm", ".wmv",
}


def complete(parser, tokens, cursor, profile_names=None, cwd=None):
    """Return completion candidates for *tokens* at the zero-based *cursor*."""
    try:
        cursor = max(0, int(cursor))
        tokens = list(tokens)
        if not tokens:
            tokens = [parser.prog]
        while len(tokens) <= cursor:
            tokens.append("")
        current = tokens[cursor]
        before = tokens[1:cursor]
        subparsers = _subparser_action(parser)
        if subparsers is None:
            return []

        command_index = next(
            (index for index, token in enumerate(before) if token in subparsers.choices),
            None,
        )
        if command_index is None:
            candidates = list(subparsers.choices)
            candidates.extend(_option_candidates(parser, current, set()))
            return _matching(candidates, current)

        command = before[command_index]
        active_parser = subparsers.choices[command]
        command_tokens = before[command_index + 1:]
        state = _parse_previous(active_parser, command_tokens)

        if state["pending"] is not None:
            return _value_candidates(
                active_parser, state["pending"], current, state["positionals"],
                profile_names, cwd,
            )

        if current.startswith("--") and "=" in current:
            option, value = current.split("=", 1)
            action = active_parser._option_string_actions.get(option)
            if action is not None and action.nargs != 0:
                values = _value_candidates(
                    active_parser, action, value, state["positionals"],
                    profile_names, cwd,
                )
                return [f"{option}={candidate}" for candidate in values]

        if current.startswith("-"):
            return _option_candidates(active_parser, current, state["used"])

        candidates = []
        positional = _next_positional(active_parser, len(state["positionals"]))
        if positional is not None:
            candidates.extend(
                _value_candidates(
                    active_parser, positional, current, state["positionals"],
                    profile_names, cwd,
                )
            )
        if current == "":
            candidates.extend(_option_candidates(active_parser, current, state["used"]))
        return _unique(candidates)
    except Exception:
        # Completion must never make an interactive shell noisy or unusable.
        return []


def render_completion(shell):
    """Return the activation script for a supported shell."""
    try:
        return SHELL_SCRIPTS[shell]
    except KeyError:
        raise ValueError(f"Unsupported shell: {shell}") from None


def _subparser_action(parser):
    return next(
        (action for action in parser._actions
         if isinstance(action, argparse._SubParsersAction)),
        None,
    )


def _parse_previous(parser, tokens):
    used = set()
    positionals = []
    pending = None
    options_enabled = True
    for token in tokens:
        if pending is not None:
            pending = None
            continue
        if options_enabled and token == "--":
            options_enabled = False
            continue
        if options_enabled and token.startswith("-"):
            option = token.split("=", 1)[0]
            action = parser._option_string_actions.get(option)
            if action is not None:
                used.add(action)
                if action.nargs != 0 and "=" not in token:
                    pending = action
                continue
        positionals.append(token)
    return {"used": used, "positionals": positionals, "pending": pending}


def _option_candidates(parser, prefix, used):
    blocked = set(used)
    for group in parser._mutually_exclusive_groups:
        if any(action in used for action in group._group_actions):
            blocked.update(group._group_actions)
    candidates = []
    for action in parser._actions:
        if action in blocked:
            continue
        for option in action.option_strings:
            if option.startswith(prefix):
                candidates.append(option)
    return _unique(candidates)


def _next_positional(parser, consumed):
    positionals = [
        action for action in parser._actions
        if not action.option_strings and not isinstance(action, argparse._SubParsersAction)
    ]
    for action in positionals:
        if action.nargs in ("*", "+"):
            return action
        if consumed == 0:
            return action
        consumed -= 1
    return None


def _value_candidates(parser, action, prefix, positionals, profile_names, cwd):
    if action.choices is not None:
        return _matching((str(choice) for choice in action.choices), prefix)
    if action.dest == "profile":
        return _matching(_profiles(profile_names), prefix)
    if action.dest == "name" and positionals and positionals[0] in {"activate", "delete"}:
        return _matching(_profiles(profile_names), prefix)
    if action.type is Path:
        suffixes = _preferred_suffixes(parser, action)
        return _path_candidates(prefix, suffixes, cwd)
    return []


def _profiles(provider):
    if provider is None:
        from .profiles import ProfileStore

        try:
            data = ProfileStore().load()
            names = data.get("profiles", {})
        except Exception:
            return []
    else:
        try:
            names = provider()
        except Exception:
            return []
    return sorted((str(name) for name in names), key=str.casefold)


def _preferred_suffixes(parser, action):
    if action.dest == "video":
        return VIDEO_SUFFIXES
    if action.dest == "vtt":
        return {".vtt"}
    if action.dest == "slide_times":
        return {".txt"}
    if action.dest == "contact_output":
        return {".jpg", ".jpeg"}
    if action.dest == "output":
        return {".docx"} if parser.prog.endswith(" build") else {".txt"}
    return None


def _path_candidates(prefix, suffixes, cwd):
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    parent_text = os.path.dirname(prefix)
    name_prefix = os.path.basename(prefix)
    expanded_parent = os.path.expanduser(parent_text) if parent_text else ""
    search_directory = Path(expanded_parent) if expanded_parent else cwd
    if not search_directory.is_absolute():
        search_directory = cwd / search_directory
    try:
        entries = list(search_directory.iterdir())
    except OSError:
        return []

    candidates = []
    for entry in entries:
        if not entry.name.startswith(name_prefix):
            continue
        if not name_prefix.startswith(".") and entry.name.startswith("."):
            continue
        if not entry.is_dir() and suffixes is not None and entry.suffix.lower() not in suffixes:
            continue
        displayed = os.path.join(parent_text, entry.name) if parent_text else entry.name
        if entry.is_dir():
            displayed += os.sep
        candidates.append((not entry.is_dir(), entry.name.casefold(), displayed))
    return [item[2] for item in sorted(candidates)]


def _matching(values, prefix):
    return _unique(value for value in values if value.startswith(prefix))


def _unique(values):
    return list(dict.fromkeys(values))


BASH_SCRIPT = r'''# slides-docx completion for Bash
_slides_docx_complete() {
    local candidate
    COMPREPLY=()
    while IFS= read -r candidate; do
        [[ -n "$candidate" ]] && COMPREPLY+=("$candidate")
    done < <(command slides-docx _complete "$COMP_CWORD" "${COMP_WORDS[@]}" 2>/dev/null)
    compopt -o filenames 2>/dev/null || true
}
complete -F _slides_docx_complete slides-docx
'''


ZSH_SCRIPT = r'''#compdef slides-docx
# slides-docx completion for Zsh
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit }
_slides_docx_complete() {
    local -a candidates
    candidates=("${(@f)$(command slides-docx _complete $((CURRENT - 1)) "${words[@]}" 2>/dev/null)}")
    (( ${#candidates[@]} )) && compadd -- "${candidates[@]}"
}
compdef _slides_docx_complete slides-docx
'''


FISH_SCRIPT = r'''# slides-docx completion for Fish
function __slides_docx_complete
    set -l tokens (commandline -opc)
    set -l current (commandline -ct)
    if test (count $tokens) -eq 0
        set tokens slides-docx
    end
    set -l cursor (count $tokens)
    command slides-docx _complete $cursor $tokens "$current" 2>/dev/null
end
complete -c slides-docx -f -a '(__slides_docx_complete)'
'''


POWERSHELL_SCRIPT = r'''# slides-docx completion for PowerShell
Register-ArgumentCompleter -Native -CommandName slides-docx -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $tokens = @()
    foreach ($element in $commandAst.CommandElements) {
        if ($element -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            $tokens += $element.Value
        } else {
            $tokens += $element.Extent.Text
        }
    }
    if ($tokens.Count -eq 0) {
        $tokens = @('slides-docx')
    }
    if ($wordToComplete -eq '') {
        $tokens += ''
    } elseif ($tokens[-1] -ne $wordToComplete) {
        $tokens += $wordToComplete
    }
    $cursor = $tokens.Count - 1
    & slides-docx _complete $cursor @tokens 2>$null | ForEach-Object {
        if ($_ -match "[\s']") {
            $completionText = "'" + ($_ -replace "'", "''") + "'"
        } else {
            $completionText = $_
        }
        [System.Management.Automation.CompletionResult]::new(
            $completionText, $_, 'ParameterValue', $_
        )
    }
}
'''


SHELL_SCRIPTS = {
    "bash": BASH_SCRIPT,
    "zsh": ZSH_SCRIPT,
    "fish": FISH_SCRIPT,
    "powershell": POWERSHELL_SCRIPT,
}
