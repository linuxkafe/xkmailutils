#!/usr/bin/env bash
# test_main.sh — minimal contract test
#
# Convention: exit 0 = green, non-zero = red. `scripts/test.sh` runs every
# tests/test_*.sh and aggregates the result.
set -uo pipefail

PASS=0
FAIL=0

pass() {
	PASS=$((PASS + 1))
	printf '  ✅ %s\n' "$1"
}

fail() {
	FAIL=$((FAIL + 1))
	printf '  ❌ %s\n' "$1"
}

# The entrypoint must exist and be executable
if [ -f scripts/main.sh ]; then
	pass "scripts/main.sh exists"
else
	fail "scripts/main.sh missing"
fi

if [ -x scripts/main.sh ]; then
	pass "scripts/main.sh is executable"
else
	fail "scripts/main.sh is not executable"
fi

# It must parse
if bash -n scripts/main.sh 2>/dev/null; then
	pass "scripts/main.sh parses (bash -n)"
else
	fail "scripts/main.sh has a syntax error"
fi

# And it must run
if output=$(bash scripts/main.sh 2>&1); then
	pass "scripts/main.sh runs"
else
	fail "scripts/main.sh exited non-zero: $output"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
