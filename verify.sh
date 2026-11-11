#!/usr/bin/env bash
# Reads your Grafana and queries it. Run with: ./verify.sh  (about 10 minutes)
cd "$(dirname "$0")" || exit 1
az account show >/dev/null 2>&1 || { echo "Not logged in to Azure (az login)." >&2; exit 1; }
exec python3 .lab/grafanacheck.py
