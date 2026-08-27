#!/usr/bin/env python3
"""TLU hardware bridge.

Runs under the Python version required by Herakles (the ATLAS/LCG stack,
e.g. Python 3.9.12 from LCG_104d). Exposes a simple JSON-lines request/
response protocol over stdin/stdout, so a Constellation satellite running
under a different (newer) Python version can control the TLU without
ever importing Herakles directly.

Usage (must be launched with the matching Python interpreter):
    /path/to/lcg/python3.9 tlu_bridge.py --uri <uri> --address-table <path>

Protocol: one JSON object per line on stdin, one JSON object per line on
stdout. Every request has a "cmd" field. Every response has an "ok" field;
on failure it also has an "error" field.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Some C++ bindings (Herakles/uHAL) write banners/warnings directly to the
# stdout FILE DESCRIPTOR (fd 1) using low-level C I/O, bypassing Python's
# sys.stdout object entirely. Reassigning sys.stdout is not enough to catch
# those. So we duplicate the real stdout fd (to keep a private handle for
# our own JSON protocol), then redirect fd 1 itself to fd 2 (stderr) --
# this way ANY write to "stdout" from any language ends up on stderr,
# leaving the real stdout pipe exclusively for our messages.
_real_stdout = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr

# The TLU repo root (containing gui/ and lib_TLU/) is not assumed from this
# script's own location, since the bridge script can live anywhere. Set the
# TLU_REPO_ROOT environment variable, or pass --repo-root explicitly.
#_env_repo_root = os.environ.get("TLU_REPO_ROOT")
#MMM

def send(obj: dict) -> None:
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


def main() -> int:
    #========================================================================================================================================
    #                                          Configuration of the connection with TLU                          
    #========================================================================================================================================
    #getting all the arguments (uri, address table, repo_root)
    parser = argparse.ArgumentParser(description=__doc__)#docstring at the top of the .py, (--help)
    parser.add_argument("--uri", default=None)#URI of the TLU
    parser.add_argument("--address-table", default=None)#address table of the TLU
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Path to the TLU repo root (containing gui/ and lib_TLU/). "
        "Defaults to the TLU_REPO_ROOT environment variable.",
    )#Path to all of the TLU files
    args = parser.parse_args()

    if not args.repo_root:#no args for TLU repo --> error, stopping the bridge
        send({
            "ok": False,
            "event": "ready",
            "error": (
                "TLU repo root not set: pass --repo-root or set the "
                "TLU_REPO_ROOT environment variable."
            ),
        })
        return 1

    repo_root = Path(args.repo_root).expanduser().resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))#repo_root = highest priority
    #getting the modules to control the TLU
    try:
        from gui.tlu_service import TLUService, build_tlu_factory
    except ModuleNotFoundError as exc:
        send({
            "ok": False,
            "event": "ready",
            "error": f"Could not import gui.tlu_service from {repo_root}: {exc}",
        })
        return 1

    try: #creating a function that will create a TLU object ==> TLUService : hardware service (commands to control ==> high level com), build_tlu_factory = building an object TLU (Communication.py ==> low level com)
        service = TLUService(
            tlu_factory=build_tlu_factory(uri=args.uri, address_table=args.address_table)
        )
    except Exception as exc:
        send({"ok": False, "event": "ready", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    #TLU Service started correctly !
    send({"ok": True, "event": "ready"})
    #loop until the closing of the bridge
    #========================================================================================================================================
    #                                          Getting request from satellites and doing the action wanted by the user                           
    #========================================================================================================================================
    for raw_line in sys.stdin:#pipe between satellite and bridge 
        line = raw_line.strip()#reads entire line (until \n)
        if not line:#If the line is empty we skip and we don't parse it 
            continue

        try:
            request = json.loads(line)#Construction of a dict from the JSON word
            cmd = request.get("cmd")#analysing the command

            if cmd == "connect":
                bindings = service.connect()#Creating the TLU object and connecting to TLU and getting linked registers 
                registers = list(bindings.counters_to_tlu) + list(bindings.counters_from_tlu)#List of available counters list  
                send({"ok": True, "register_names": registers})

            elif cmd == "disconnect":
                service.disconnect()#disconnecting from TLU and distructing TLU object
                send({"ok": True})

            elif cmd == "set_running":
                service.set_running(bool(request["enabled"]))#casting the key "enabled" from the dictionnary request in a bool 
                send({"ok": True})

            elif cmd == "set_mode":
                service.set_mode(int(request["mode_value"]))#casting the key "mode_value" from the dictionnary request in an int 
                send({"ok": True})

            elif cmd == "apply_configuration":
                kwargs = {key: value for key, value in request.items() if key != "cmd"}#iterating on all the pairs key:value except the first (cmd)=> kwargs dict with conf elements
                service.apply_configuration(**kwargs)#unpacking the dict to send each configuration parameter to the TLU 
                send({"ok": True})

            elif cmd == "reset_counters":
                service.reset_counters()#resetting the counters
                send({"ok": True})

            elif cmd == "read_counters":
                values = service.read_counters(tuple(request["registers"]))#reading the counters
                send({"ok": True, "values": values})

            elif cmd == "ping":
                send({"ok": True, "event": "pong"})

            elif cmd == "shutdown":
                send({"ok": True})
                break

            else:
                send({"ok": False, "error": f"Unknown command '{cmd}'"})

        except Exception as exc:
            send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    try:
        if service.connected:#when the loop up is finished ending the connection 
            service.disconnect()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
