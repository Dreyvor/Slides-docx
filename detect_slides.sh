#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: detect_slides.sh VIDEO [OUTPUT] [--threshold NUMBER] [--crop W:H:X:Y]

Detect slide changes with FFmpeg and write timestamps in seconds.
OUTPUT defaults to slide_times.txt; threshold defaults to 12 (range 0–100).
Use a higher threshold for fewer detections. Crop is applied before scaling.
Slide 1 implicitly starts at zero; no changes produces an empty output file.
EOF
}

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

threshold=12
crop=
positional=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --threshold|--crop)
            [[ $# -ge 2 && -n "$2" ]] || fail "$1 requires a value"
            if [[ "$1" == --threshold ]]; then threshold=$2; else crop=$2; fi
            shift 2 ;;
        --) shift; positional+=("$@"); break ;;
        -*) fail "Unknown option: $1 (use --help)" ;;
        *) positional+=("$1"); shift ;;
    esac
done

[[ ${#positional[@]} -ge 1 && ${#positional[@]} -le 2 ]] || { usage >&2; exit 1; }
video=${positional[0]}
output=${positional[1]:-slide_times.txt}
[[ -f "$video" && -r "$video" ]] || fail "Cannot read video: $video"
[[ ! -d "$output" ]] || fail "Output is a directory: $output"
[[ ! "$video" -ef "$output" ]] || fail "Output must differ from the video"
[[ "$threshold" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "Threshold must be a number from 0 to 100"
awk -v value="$threshold" 'BEGIN { exit !(value >= 0 && value <= 100) }' || fail "Threshold must be from 0 to 100"
command -v ffmpeg >/dev/null 2>&1 || fail "FFmpeg is required; install it and ensure it is on PATH"

# Prefix relative paths so leading dashes cannot be treated as options.
[[ "$video" == /* ]] || video="./$video"
[[ "$output" == /* ]] || output="./$output"
filters="scale=640:-2,scdet=threshold=$threshold"
if [[ -n "$crop" ]]; then filters="crop=$crop,$filters"; fi

# Keep metadata on stdout and diagnostics on stderr. A temporary file beside
# the destination prevents failed detection from replacing existing timestamps.
temporary=$(mktemp "${output}.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ffmpeg -hide_banner -nostdin -i "$video" -map 0:v:0 \
    -vf "$filters,metadata=mode=print:key=lavfi.scd.time:file=-" \
    -an -sn -dn -f null - \
    | awk -F= '/^lavfi\.scd\.time=[0-9]+([.][0-9]+)?$/ { print $2 }' > "$temporary"
mv -f -- "$temporary" "$output"
printf 'Wrote slide-change timestamps to %s\n' "$output"
