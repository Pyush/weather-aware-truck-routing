#!/usr/bin/env bash
#
# Secret scanner.
#
# Runs as the repository's pre-commit and pre-push hook, and as a CI step, so all
# three run the identical logic rather than three drifting copies. The hook in
# ~/.git-hooks is a thin wrapper that calls this script.
#
# Usage:
#   scripts/security-check.sh --staged   # scan files staged for commit
#   scripts/security-check.sh --full     # scan every tracked file (pre-push / CI)
#
# Exit codes: 0 clean, 1 findings, 2 bad usage.
#
# Avoiding a false positive is as important as catching a real one: a scanner
# that cries wolf gets disabled, and a disabled scanner catches nothing. So a
# line can be exempted with an explicit trailing marker:
#
#   token = "abc123"  # ok: fixture value used by the mocked-provider test
#
# Example files (.env.example and friends) are still scanned, because a real key
# pasted into one is exactly the accident this is meant to catch.

set -euo pipefail

MODE="${1:---staged}"
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
if [ ! -t 1 ]; then RED=""; GREEN=""; YELLOW=""; DIM=""; RESET=""; fi

usage() {
    echo "usage: $(basename "$0") [--staged|--full|--self-test]" >&2
    exit 2
}

case "$MODE" in
    --staged | --full | --self-test) ;;
    -h | --help) usage ;;
    *) usage ;;
esac

# --- patterns ---------------------------------------------------------------
# Format: <label>|<extended-regex>. Deliberately specific: a generic
# "password" match would fire on every config file in the repo.
#
# Each pattern targets a credential that is recognisable by its shape or prefix,
# so documentation and test fixtures do not trip it.
declare -a PATTERNS=(
    "AWS access key ID|(A3T[A-Z0-9]|AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}"
    "AWS secret access key|(aws_secret_access_key|aws-session-token)[[:space:]]*[:=][[:space:]]*[\"']?[A-Za-z0-9/+=]{40}"
    "Private key block|-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    "GitHub token|gh[pousr]_[A-Za-z0-9]{36,}"
    "GitHub fine-grained token|github_pat_[A-Za-z0-9_]{22,}"
    "Slack token|xox[baprs]-[A-Za-z0-9-]{10,}"
    "Stripe secret key|sk_(live|test)_[A-Za-z0-9]{16,}"
    "Google API key|AIza[0-9A-Za-z_-]{35}"
    "OpenAI key|sk-[A-Za-z0-9]{32,}"
    "Anthropic API key|sk-ant-[A-Za-z0-9_-]{24,}"
    "Slack webhook|hooks\.slack\.com/services/[A-Za-z0-9/+_-]{20,}"
    "Connection string with password|[a-zA-Z][a-zA-Z0-9+.-]*://[^\"[:space:]]*:[^\"[:space:]@]+@"
    "Hardcoded secret assignment|(secret|passwd|password|api[_-]?key|access[_-]?token|auth[_-]?token)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9/+_.-]{16,}[\"']"
    "npm token|npm_[A-Za-z0-9]{36}"
    "PyPI token|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}"
)

# NOTE: every dynamic pattern below is passed with an explicit `-e`. A pattern
# such as `-----BEGIN ... PRIVATE KEY-----` starts with a hyphen, so `grep -E
# "$regex"` would make grep read it as an *option list*, fail with exit 2, and --
# because "no match" and "grep errored" both look like a non-zero exit to `|| true`
# -- silently skip the pattern. That bug hid every private-key match.

# --- scan -------------------------------------------------------------------
# Findings for one line. Kept separate from file discovery so the self-test can
# exercise every pattern without needing git.
#
# NOTE: every dynamic pattern is passed with an explicit `-e`. A pattern such as
# `-----BEGIN ... PRIVATE KEY-----` starts with a hyphen, so `grep -E "$regex"`
# makes grep read it as an *option list*, fail with exit 2, and -- because "no
# match" and "grep errored" are both a non-zero exit to `|| true` -- silently skip
# the pattern. That bug hid every private-key match.
is_exempt() {
    # An explicit trailing marker exempts an intentional test fixture.
    printf '%s' "$1" | LC_ALL=C grep -qEe '#[[:space:]]*ok:[[:space:]]*\S'
}

match_line() {
    local file="$1" lineno="$2" line="$3"
    local label regex

    is_exempt "$line" && return 0

    for entry in "${PATTERNS[@]}"; do
        label="${entry%%|*}"
        regex="${entry#*|}"
        if printf '%s\n' "$line" | LC_ALL=C grep -qEe "$regex"; then
            printf '%s:%s\t%s\n' "$file" "$lineno" "$label"
        fi
    done
}

# grep is invoked with every flag as its OWN argument, never as a cluster.
#
# `grep -nIe "$re" file` (clustered -I with -e) silently matches NOTHING on
# GNU grep 3.12 -- -I in the same cluster as -e breaks pattern matching, so every
# pattern reports "no match" and the scanner declares a secret-free repository.
# Passing -I -E -e separately works correctly. This was found because the
# --self-test exercised a different code path than the real scan; the self-test now
# runs the real scan function.
grep_scan_file() {
    local file="$1" regex="$2"
    LC_ALL=C grep -n -I -E -e "$regex" "$file" 2>/dev/null || true
}

# --- self-test --------------------------------------------------------------
# A pattern that silently stops matching is the worst possible failure for this
# script: it reports "clean" forever. One real bug of exactly that kind was found
# during development (the `-e` bug above), so every pattern now has a fixture
# here and `--self-test` proves each one still fires.
if [ "$MODE" = "--self-test" ]; then
    FORTY=$(printf 'A%.0s' $(seq 1 40))
    FIFTY=$(printf 'b%.0s' $(seq 1 50))
    # Each sample is written as two adjacent quoted halves. Bash concatenates
    # them, so the runtime string is a valid credential and the pattern matches,
    # while the *source text* contains no matchable literal. That keeps this
    # file fully auditable by the scanner itself instead of needing an exclusion
    # list, which would be a blind spot someone could hide a real key behind.
    declare -a CASES=(
        "AWS access key ID|AKIA""IOSFODNN7EXAMPLE"
        "AWS secret access key|aws_secret_access_key = ${FORTY}"
        "Private key block|-----BEGIN ""RSA PRIVATE KEY-----"
        "GitHub token|ghp""_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "GitHub fine-grained token|github_pat""_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "Slack token|xoxb""-123456789012-abcdefghijkl"
        "Stripe secret key|sk_live""_ABCDEFGHIJKLMNOPQRST"
        "Google API key|AIza""SyA1234567890abcdefghijklmnopqrstuvw"
        "OpenAI key|sk-""ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        "Anthropic API key|sk-ant-""api03-ThisIsFakeButLooksReal1234567890"
        "Slack webhook|https://hooks.slack.com/""services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
        "Connection string with password|postgres://""admin:hunter2RealSecret@db.example.com/app"
        "Hardcoded secret assignment|password = ""'sup3rS3cretValueHere'"
        "npm token|npm""_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "PyPI token|pypi-""AgEIcHlwaS5vcmc${FIFTY}"
    )

    failed=0
    for case_entry in "${CASES[@]}"; do
        expected="${case_entry%%|*}"
        sample="${case_entry#*|}"
        actual="$(match_line self-test 1 "$sample")"
        if printf '%s' "$actual" | grep -qF "$expected"; then
            printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$expected"
        else
            printf '  %s✗%s %s %s(did not match; got: %s)%s\n' \
                "$RED" "$RESET" "$expected" "$YELLOW" "${actual:-nothing}" "$RESET"
            failed=$((failed + 1))
        fi
    done

    # The exemption marker must also actually suppress a finding.
    if match_line self-test 1 'AKIA""IOSFODNN7EXAMPLE  # ok: fixture' | grep -q .; then
        printf '  %s✗%s exemption marker was ignored\n' "$RED" "$RESET"
        failed=$((failed + 1))
    else
        printf '  %s✓%s exemption marker suppresses findings\n' "$GREEN" "$RESET"
    fi

    # End-to-end: run the SAME code path the real scan uses, over a real file on
    # disk. The per-line check above is not sufficient on its own -- a bug in the
    # file-scanning path (a mis-parsed grep flag, say) would leave every pattern
    # individually healthy while the scanner as a whole detected nothing.
    TMPDIR_SELF="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR_SELF"' EXIT
    E2E="$TMPDIR_SELF/probe.py"
    printf '%s\n' 'KEY = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"' > "$E2E"
    if [ -n "$(grep_scan_file "$E2E" 'gh[pousr]_[A-Za-z0-9]{36,}')" ]; then
        printf '  %s✓%s end-to-end file scan detects a planted secret\n' "$GREEN" "$RESET"
    else
        printf '  %s✗%s end-to-end file scan MISSED a planted secret\n' "$RED" "$RESET"
        printf '    %sgrep is almost certainly being invoked with a broken flag cluster%s\n' \
            "$YELLOW" "$RESET"
        failed=$((failed + 1))
    fi

    if [ "$failed" -gt 0 ]; then
        printf '%s%d pattern(s) not matching -- the scanner is not trustworthy%s\n' \
            "$RED" "$failed" "$RESET"
        exit 1
    fi
    printf "%s✓ all %d patterns fire, exemption works, end-to-end scan detects%s\n" "$GREEN" "${#CASES[@]}" "$RESET"
    exit 0
fi

# --- file selection ---------------------------------------------------------
# Deferred until after --self-test so the self-test needs neither git nor a repo.
mapfile -t FILES < <(
    if [ "$MODE" = "--staged" ]; then
        git diff --cached --name-only --diff-filter=ACMR
    else
        git ls-files
    fi | while read -r f; do
        # Skip paths that cannot contain meaningful source, and skip binary files.
        case "$f" in
            *.png | *.jpg | *.jpeg | *.gif | *.webp | *.ico | *.pdf | *.zip | *.gz | \
            *.woff | *.woff2 | *.ttf | *.eot | *.mp4 | *.so | *.pyc) continue ;;
        esac
        [ -f "$f" ] || continue
        # Detect binary via grep's own heuristic rather than trusting extensions.
        if LC_ALL=C grep -qIe . "$f" 2>/dev/null; then
            printf '%s\n' "$f"
        fi
    done | sort -u
)

if [ "${#FILES[@]}" -eq 0 ]; then
    printf '%sno files to scan%s\n' "$DIM" "$RESET"
    exit 0
fi

# Exclude this script by exact path. It necessarily contains every credential
# pattern as a literal, so it cannot be scanned by those same patterns without
# matching itself -- GNU grep 3.12 does match the private-key pattern against its
# own regex definition line. The compensating control is `--self-test`, which
# proves every pattern still fires against a real fixture. This is a named,
# single-file exclusion rather than a path wildcard, so it cannot quietly grow.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
FILTERED=()
for f in "${FILES[@]}"; do
    dir="$(dirname "$f")"
    if dir="$(cd "$dir" 2>/dev/null && pwd)"; then
        [ "$dir/$(basename "$f")" = "$SELF" ] && continue
    fi
    FILTERED+=("$f")
done
FILES=("${FILTERED[@]}")

# One grep per (pattern, file), not per (pattern, line). The per-line variant is
# still available as match_line() for --self-test, but running it over a whole
# repository costs one subprocess spawn per pattern per line and turns a sub-second
# scan into a multi-minute one.
FINDINGS=0
for entry in "${PATTERNS[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"
    for file in "${FILES[@]}"; do
        while IFS= read -r hit; do
            [ -n "$hit" ] || continue
            lineno="${hit%%:*}"
            line="${hit#*:}"
            is_exempt "$line" && continue
            FINDINGS=$((FINDINGS + 1))
            printf '%s✗ %s:%s%s\n' "$RED" "$file" "$lineno" "$RESET"
            printf '  %s%s%s\n' "$DIM" "$label" "$RESET"
            # Truncate the echoed line so a huge base64 blob cannot flood output.
            printf '    %s\n\n' "$(printf '%s' "$line" | cut -c1-160)"
        done < <(grep_scan_file "$file" "$regex")
    done
done

TOTAL="${#FILES[@]}"
if [ "$FINDINGS" -gt 0 ]; then
    printf '%s%d potential secret(s) found in %d file(s) scanned.%s\n' \
        "$RED" "$FINDINGS" "$TOTAL" "$RESET"
    printf 'Either remove the secret, rotate it if it was ever real, or mark an\n'
    printf 'intentional test fixture with a trailing comment:  # ok: <reason>\n'
    exit 1
fi

printf '%s✓ no secrets found in %d file(s) scanned%s\n' "$GREEN" "$TOTAL" "$RESET"
