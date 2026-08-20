#!/bin/sh
set -eu

summary=$(./farm-c single --seed 42)
case "$summary" in
    *"strategy: profit_optimizer"*"actual_seed: 42"*"days_simulated:"*"bankrupt:"*) ;;
    *) echo "CLI summary is missing required fields" >&2; exit 1 ;;
esac

history=$(./farm-c single --strategy fast_seller --seed 42 --verbose)
case "$history" in
    *"day 1:"*"strategy: fast_seller"*) ;;
    *) echo "CLI verbose output is missing day history" >&2; exit 1 ;;
esac

if ./farm-c single --strategy missing_strategy >/dev/null 2>&1; then
    echo "unknown strategy unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c single --invalid-option >/dev/null 2>&1; then
    echo "invalid argument unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c single --config /definitely/missing/farm-config >/dev/null 2>&1; then
    echo "missing config unexpectedly succeeded" >&2
    exit 1
fi

batch_summary=$(./farm-c batch --runs 2 --seed 42 --strategy fast_seller --strategy profit_optimizer)
case "$batch_summary" in
    *"base_seed: 42"*"total_runs: 4"*"fast_seller"*"profit_optimizer"*) ;;
    *) echo "batch summary is missing required fields" >&2; exit 1 ;;
esac

csv_path=$(mktemp)
./farm-c batch --runs 2 --seed 42 --strategy fast_seller --csv "$csv_path" >/dev/null
csv_rows=$(wc -l <"$csv_path")
rm -f "$csv_path"
if [ "$csv_rows" -ne 3 ]; then
    echo "batch --csv did not write the expected header + 2 rows" >&2
    exit 1
fi

if ./farm-c batch --strategy missing_strategy --runs 1 >/dev/null 2>&1; then
    echo "unknown batch strategy unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c batch --runs 0 >/dev/null 2>&1; then
    echo "non-positive --runs unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c batch >/dev/null 2>&1; then
    echo "missing --runs unexpectedly succeeded" >&2
    exit 1
fi
printf '%s\n' "CLI tests passed"
