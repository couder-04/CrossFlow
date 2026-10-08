#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-redpanda:9092}"
echo "Waiting for Redpanda at ${BOOTSTRAP}..."
for i in $(seq 1 60); do
  if rpk cluster info -X brokers="${BOOTSTRAP}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Create a topic, or widen an existing one to at least TARGET partitions.
# `rpk topic add-partitions -n N` ADDS N partitions every time it runs, so it must only be
# called with the missing count; calling it unconditionally grew the topics on every start.
ensure_topic() {
  local topic="$1" target="$2"
  rpk topic create "${topic}" -p "${target}" -r 1 -X brokers="${BOOTSTRAP}" >/dev/null 2>&1 || true
  local current
  current=$(rpk topic describe "${topic}" -p -X brokers="${BOOTSTRAP}" | tail -n +2 | grep -c . || true)
  if [ "${current}" -lt "${target}" ]; then
    rpk topic add-partitions "${topic}" -n "$((target - current))" -X brokers="${BOOTSTRAP}"
    current="${target}"
  fi
  echo "${topic}: ${current} partitions"
}

ensure_topic anpr.reads.v1 12
ensure_topic alerts.v1 6
ensure_topic analytics.flow.v1 3
echo "Topics ready."
