#!/usr/bin/env bash
# Deletes the resource group and everything in it. Run with: ./teardown.sh
set -u
cd "$(dirname "$0")"
az account show >/dev/null 2>&1 || { echo "Not logged in to Azure (az login)." >&2; exit 1; }
if az group exists -n rg-grafana | grep -q true; then az group delete -n rg-grafana --yes && echo "deleted the resource group"; fi
echo "Now run ./verify-teardown.sh"
