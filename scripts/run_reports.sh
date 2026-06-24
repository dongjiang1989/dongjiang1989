#!/bin/bash
# Shell wrapper for biweekly report generation
# Usage:
#   ./scripts/run_reports.sh                  # Generate all periods
#   ./scripts/run_reports.sh --latest         # Generate only latest period
#   ./scripts/run_reports.sh --start 2026-06-08 --end 2026-06-21  # Custom period
#   ./scripts/run_reports.sh --periods "2026-06-08..2026-06-21,2026-05-25..2026-06-07"

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "=== GitHub Biweekly Report Generator ==="
echo "Working directory: $(pwd)"
echo ""

# Pass all arguments to the Python script
python3 scripts/generate_biweekly_report.py "$@"
