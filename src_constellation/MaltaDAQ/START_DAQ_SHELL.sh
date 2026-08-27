#!/bin/bash
# ==============================================================================
#  MaltaDAQ satellite launcher
#  1) sources the DAQ setup (ROOT/env needed by MaltaMultiDAQ)
#  2) re-activates the Python venv so `python3` still points to Constellation's
#     interpreter (the DAQ setup can override $PATH/$PYTHONPATH otherwise)
#  3) starts the satellite script, forwarding any CLI args (-n, -g, etc.)
# ==============================================================================

set -e

# --- adjust these two paths to your setup -----------------------------------
DAQ_SETUP="/home/MaltaSW/setup.sh"
VENV_DIR="/home/itdc/venv"
SATELLITE_SCRIPT="/home/itdc/work/Thomas/Constellation/MaltaDAQ/src/MaltaDAQ/MaltaDAQ.py"
# ------------------------------------------------------------------------------

echo "[run_malta_satellite] Sourcing DAQ setup: ${DAQ_SETUP}"
source "${DAQ_SETUP}"

echo "[run_malta_satellite] Re-activating venv: ${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

echo "[run_malta_satellite] Starting satellite: ${SATELLITE_SCRIPT} -n W4R1_W2R6 -g edda"
exec python3 "${SATELLITE_SCRIPT}" -n W4R1_W2R6 -g edda
