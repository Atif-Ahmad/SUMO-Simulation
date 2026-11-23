#!/usr/bin/env bash
# Runs the baseline for the AM and PM levels listed below, for every seed.
# Skips runs that already finished, so you can stop it and start it again.

# SEEDS="1 2 3 4 5 6 7 8 9 10"
SEEDS="1 2 3 4 5"
AM_LEVELS="50 60 65 70 75 80 90 100"
PM_LEVELS="50 60 65 70 75 80 90 100"

run_one () {            # usage: run_one AM 65 3
  period=$1; level=$2; seed=$3
  out="results/raw/test/${period}_${level}_seed${seed}"
  if [ -f "$out/run_info.json" ]; then
    echo "skip  ${period}_${level} seed ${seed} (already done)"
    return
  fi
  echo "run   ${period}_${level} seed ${seed}"
  python scripts/run_scenario.py \
    --routes "la_routes/${period}_${level}.rou.xml" \
    --seed "$seed" \
    --out "$out"
}

for level in $AM_LEVELS; do
  for seed in $SEEDS; do run_one AM "$level" "$seed"; done
done

for level in $PM_LEVELS; do
  for seed in $SEEDS; do run_one PM "$level" "$seed"; done
done

echo "all done"