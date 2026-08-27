import csv
import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime

from constellation.core.satellite import Satellite, SatelliteArgumentParser
from constellation.core.configuration import Configuration
from constellation.core.logging import setup_cli_logging

PLANE_IDS = tuple(str(i) for i in range(1, 7))#Creating a Tuple with 6 values --> keys for next dictionnaries
PLANE_COUNTER_REGISTERS = {plane: f"COUNTER_P{plane}" for plane in PLANE_IDS}#dict containing all the plane counters e.g : 1: COUNTER_PLANE_1...
# Fixed hardware mapping: trigger planes 1/2/3 each have a corresponding
# busy-line input, wired to planes 4/5/6 respectively (as labeled in the
# standalone GUI). Enabling trigger plane N automatically also monitors
# its busy line -- no separate configuration needed.
BUSY_LINE_FOR_PLANE = {"1": "4", "2": "5", "3": "6"}
# Best-effort label for each mode value, only used for the run summary CSV.
# Adjust if your TLU firmware uses different mode names/numbers.
MODE_LABELS = {
    0: "MALTA",
    1: "DRS",
    2: "Shadow DRS",
    3: "Scope",
    4: "Shadow Scope",
}
DEFAULT_FALLBACK_COUNTERS = (
    "COUNTER_TRIG_TO_MALTA",
    "COUNTER_P1",
    "COUNTER_DUT1",
    "COUNTER_SCINT",
)


def hz_to_ns(rate_hz: int) -> int:
    return int(1e9 / rate_hz) if rate_hz > 0 else 0


class TLUBridgeError(Exception):
    """Raised when the TLU bridge subprocess fails, crashes, or times out."""


class TLUBridgeClient:
    """Talks to tlu_bridge.py (a Python 3.9 subprocess importing Herakles)
    over a JSON-lines protocol on stdin/stdout, so this satellite process
    (which needs Python >=3.11 for Constellation) never has to import
    Herakles itself."""

    def __init__(
        self,
        python_executable: str,#bridge_python in config
        bridge_script: str,#bridge_script in config
        uri: str | None,#uri in config
        address_table: str | None,#address_table in config
        repo_root: str | None = None,#bridge_repo_root in config
        extra_pythonpath: str | None = None,#bridge_pythonpath
        extra_ld_library_path: str | None = None,#bridge_ld_library_path
        timeout: float = 10.0,
    ):
        self._timeout = timeout
        self.uri = uri
        args = [python_executable, bridge_script]#Python interpretor and script to start 
        if uri:
            args += ["--uri", uri]
        if address_table:
            args += ["--address-table", address_table]
        if repo_root:
            args += ["--repo-root", repo_root]

        # The bridge subprocess only inherits this process's environment by
        # default. Herakles needs its own PYTHONPATH/LD_LIBRARY_PATH (e.g.
        # from the ATLAS/LCG stack), which may not be set in whatever shell
        # launched the satellite (MissionControl, a systemd unit, etc.), so
        # let the config add to it explicitly rather than relying on it.
        env = os.environ.copy()#copy the environment variable of the Satellite ==> we will change this copy
        if extra_pythonpath:
            env["PYTHONPATH"] = extra_pythonpath + os.pathsep + env.get("PYTHONPATH", "")
        if extra_ld_library_path:#indicates to the dynamic linker where to search for shared librairies (.so)
            env["LD_LIBRARY_PATH"] = extra_ld_library_path + os.pathsep + env.get("LD_LIBRARY_PATH", "")
	#starting the subprocess (bridge)
        self._process = subprocess.Popen(
            args,#python3.9 tlu_bridge.py --uri 192.168.200.20...
            stdin=subprocess.PIPE,#creating a pipe to send commands to bridge
            stdout=subprocess.PIPE,#Creating a pipe to read the bridge
            stderr=subprocess.PIPE,#error/log pipe
            text=True,#the pipes are exhanging str
            bufsize=1,#each line is directly sent/available, the buffer does not need to be full
            env=env,#modified satellite's env for bridge pythonpath/LD_LIBRARY
        )
        self._response_queue: "queue.Queue[str]" = queue.Queue()#thread safe FIFO for std_out
        self._stderr_lines: list[str] = []#read in synch so no need for a queue
        self._lock = threading.Lock()

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)#creating a thread for stdout reading
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        self._wait_for_ready()#waiting fo first message of bridge or timeout
    
    #method used in a thread to read stdout continously 
    def _read_stdout(self) -> None:
        if self._process.stdout is None:#if no pipe quit the function, thread does nothing
            return
        for line in self._process.stdout:#loop for every lines
            self._response_queue.put(line)#adding value read to the queue
    
    #method used in a thread to read stderr continuously
    def _read_stderr(self) -> None:
        if self._process.stderr is None:#if no pipe quit the function, thread does nothing
            return
        for line in self._process.stderr:#loop for every lines
            self._stderr_lines.append(line.rstrip())
            # Keep only the last lines to avoid unbounded memory growth.
            if len(self._stderr_lines) > 200:
                del self._stderr_lines[:100]
    
    #method called after starting sub program, waits for first message or timeout
    def _wait_for_ready(self) -> None:
        try:
            line = self._response_queue.get(timeout=self._timeout)#reads queue if not empty
        except queue.Empty:
            raise TLUBridgeError(
                "Bridge process did not respond within timeout. "
                f"stderr: {chr(10).join(self._stderr_lines[-20:])}"
            )
        #parsing answer and checking if subprogram works well
        response = json.loads(line)
        if response.get("event") != "ready" or not response.get("ok", False):
            raise TLUBridgeError(f"Bridge failed to start: {response}")

    #----------------- low level communication with bridge----------------------
    def _request(self, command: str, **params) -> dict:
        if self._process.poll() is not None:#process not running
            raise TLUBridgeError(
                f"Bridge process exited (code={self._process.returncode}). "#code of the error
                f"stderr: {chr(10).join(self._stderr_lines[-20:])}"#20 last messages of stderr
            )
        with self._lock:#takes the lock before giving back at the end of with ==> ensures we only send one request after the other
            payload = json.dumps({"cmd": command, **params})
            assert self._process.stdin is not None#Checking that stdin PIPE still running
            self._process.stdin.write(payload + "\n")
            self._process.stdin.flush()
            try:
                line = self._response_queue.get(timeout=self._timeout)#get answer
            except queue.Empty:
                raise TLUBridgeError(
                    f"Timed out waiting for bridge response to '{command}'. "
                    f"stderr: {chr(10).join(self._stderr_lines[-20:])}"
                )
        response = json.loads(line)#Parsing answer
        if not response.get("ok", False):
            raise TLUBridgeError(response.get("error", f"Unknown bridge error for '{command}'"))
        return response

    #----------------- high level communication with bridge----------------------
    def connect(self) -> list[str]:
        response = self._request("connect")
        return response.get("register_names", [])

    def disconnect(self) -> None:
        self._request("disconnect")

    def set_running(self, enabled: bool) -> None:
        self._request("set_running", enabled=enabled)

    def set_mode(self, mode_value: int) -> None:
        self._request("set_mode", mode_value=mode_value)

    def apply_configuration(self, **kwargs) -> None:
        self._request("apply_configuration", **kwargs)

    def reset_counters(self) -> None:
        self._request("reset_counters")

    def read_counters(self, registers: tuple) -> dict:
        response = self._request("read_counters", registers=list(registers))
        return response["values"]

    def close(self) -> None:
        try:
            self._request("shutdown")
        except Exception:
            pass
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()


class MaltaTLU(Satellite):
    """Satellite piloting the MALTA Trigger Logic Unit (TLU) through a
    Python-version bridge, since Herakles (Python 3.9) and Constellation
    (Python >=3.11) cannot coexist in the same interpreter.

    - do_initializing : launch the bridge subprocess and connect to the TLU
    - do_launching    : apply mode/planes/veto/width/max-rate configuration
    - do_starting     : reset counters (if requested) and enable the run
    - do_run          : poll trigger counters, compute the rate, send telemetry
    - do_stopping     : disable the run, reset counters (if requested)
    - do_landing      : disconnect from the TLU and terminate the bridge
    """

    def do_initializing(self, config: Configuration) -> str:
        """Launch the bridge subprocess (Python 3.9) and connect to the TLU."""
        bridge_python = config.get("bridge_python", "", return_type=str) or None
        bridge_script = config.get("bridge_script", "", return_type=str) or None
        bridge_repo_root = config.get("bridge_repo_root", "", return_type=str) or None
        bridge_pythonpath = config.get("bridge_pythonpath", "", return_type=str) or None
        bridge_ld_library_path = config.get("bridge_ld_library_path", "", return_type=str) or None
        if not bridge_python or not bridge_script:
            raise RuntimeError(
                "Both 'bridge_python' (path to the Python 3.9 interpreter with "
                "Herakles available) and 'bridge_script' (path to tlu_bridge.py) "
                "must be set in the satellite configuration."
            )
	#-----------------Getting Configuration----------------------------------
        uri = config.get("uri", "", return_type=str) or None
        address_table = (
            config.get("address_table", "", return_type=str)
            or config.get("adress_table", "", return_type=str)
            or None
        )
	#Interval of pollings and data actualisation
        self.log_folder = config.get("log_folder", "logs", return_type=str)
        self.poll_interval = config.get("poll_interval_s", 0.1, return_type=float)
        self.status_interval = config.get("status_every_s", 10.0, return_type=float)
        self.telemetry_interval = config.get("telemetry_interval_s", 1.0, return_type=float)
        self.mode_value = config.get("mode", 0, return_type=int)

        #Planes enabled for trigger logic
        self.planes_enabled = {
            plane: config.get(f"plane_{plane}", False, return_type=bool) for plane in PLANE_IDS
        }
	
	#Scitillator enabled or not
        self.sc_enabled = config.get("sc_enabled", False, return_type=bool)
	
	#Veto configuration in ns
        self.veto_ns: dict[str, int] = {}
        for plane in PLANE_IDS:
            key = f"veto_{plane}"
            if config.has(key):
                self.veto_ns[plane] = config.get(key, 0, return_type=int)
        if config.has("L1A"):
            self.veto_ns["L1A"] = config.get("L1A", 0, return_type=int)
        self.veto_ns["SC"] = config.get("veto_SC", 1, return_type=int)
	
	#Veto configuration in ns
        self.width_ns: dict[str, int] = {}
        for plane in PLANE_IDS:
            key = f"width_{plane}"
            if config.has(key):
                self.width_ns[plane] = config.get(key, 0, return_type=int)
        if config.has("width_L1A"):
            self.width_ns["L1A"] = config.get("width_L1A", 0, return_type=int)
        self.width_ns["SC"] = config.get("width_SC", 80, return_type=int)

        #Max trigger rate configuration
        self.max_rate_hz = config.get("max_rate_hz", 0, return_type=int)
        self.max_rate_enabled = config.get("max_rate_enabled", self.max_rate_hz > 0, return_type=bool)

        #Reset counters on start/stop 
        self.reset_counters_on_start = config.get("reset_counters_on_start", True, return_type=bool)
        self.reset_counters_on_stop = config.get("reset_counters_on_stop", True, return_type=bool)
        self.monitor_counter_override = config.get("monitor_counter", "", return_type=str) or None

        # do_initializing can be re-entered: close a previous bridge first.
        if getattr(self, "bridge", None) is not None:
            try:
                self.bridge.close()
            except Exception:
                pass
            self.bridge = None

	#Creating an object TLUBridgeClient
        self.bridge = TLUBridgeClient(
            python_executable=bridge_python,
            bridge_script=bridge_script,
            uri=uri,
            address_table=address_table,
            repo_root=bridge_repo_root,
            extra_pythonpath=bridge_pythonpath,
            extra_ld_library_path=bridge_ld_library_path,
        )
        register_names = set(self.bridge.connect())
        self.register_names = register_names  # kept on self so do_reconfigure can reuse it
        self.monitor_counter = self._choose_monitor_counter(register_names)

        # All plane counter registers confirmed to exist in the address
        # table, regardless of whether the plane is enabled -- needed by
        # the run summary CSV to tell "disabled" (-> "NC") apart from
        # "register does not exist at all" (also "NC", but for a different
        # reason; we don't distinguish the two in the CSV).
        self.all_plane_registers = {
            plane: PLANE_COUNTER_REGISTERS[plane]
            for plane in PLANE_IDS
            if PLANE_COUNTER_REGISTERS[plane] in register_names
        }
	
        self.register_metric("TRIGGER_COUNT", "counts", f"New triggers on {self.monitor_counter} since last telemetry update")
        self.register_metric("TRIGGER_RATE", "Hz", "Instantaneous trigger rate")
	
        self.trig_to_malta_register = "COUNTER_TRIG_TO_MALTA" if "COUNTER_TRIG_TO_MALTA" in register_names else None
        if self.trig_to_malta_register:
            self.register_metric("TRIG_TO_MALTA", "counts", "New counts on COUNTER_TRIG_TO_MALTA since last telemetry update")
	
	
	#enabling the plane elemety only of plane activ
        self.plane_registers = {
            plane: PLANE_COUNTER_REGISTERS[plane]
            for plane, enabled in self.planes_enabled.items()
            if enabled and PLANE_COUNTER_REGISTERS[plane] in register_names
        }
        for plane in self.plane_registers:
            self.register_metric(f"PLANE_{plane}_COUNT", "counts", f"New counts on plane {plane} since last telemetry update")

        #Busy-line registers: for each trigger plane that is enabled, also
        #monitor its corresponding busy line (see BUSY_LINE_FOR_PLANE), e.g.
        #enabling plane 1 automatically monitors busy line 4 too. Keyed by
        #the busy line's own plane number (e.g. "4"), for clear metric names.
        self.busy_registers = {
            busy_plane: PLANE_COUNTER_REGISTERS[busy_plane]
            for trigger_plane, busy_plane in BUSY_LINE_FOR_PLANE.items()
            if self.planes_enabled.get(trigger_plane, False)
            and PLANE_COUNTER_REGISTERS[busy_plane] in register_names
        }
        for plane in self.busy_registers:
            self.register_metric(f"BUSY_{plane}_COUNT", "counts", f"New counts on busy line {plane} since last telemetry update")

        return f"Connected to TLU via bridge (uri={uri}, monitor_counter={self.monitor_counter})"

    def do_launching(self) -> str:
        """Apply the mode, planes/scintillator, veto/width and max-rate configuration."""
        self.bridge.set_running(False)
        self.bridge.set_mode(self.mode_value)
        self.bridge.apply_configuration(
            planes_enabled=self.planes_enabled,
            scintillators={"SC": self.sc_enabled},
            veto_ns=self.veto_ns,
            width_ns=self.width_ns,
            max_rate_ns=hz_to_ns(self.max_rate_hz),
            max_rate_enabled=self.max_rate_enabled,
        )
        return f"TLU configured (mode={self.mode_value})"

    def do_starting(self, run_identifier: str) -> None:
        """Reset counters if requested and enable the run. The run summary
        CSV is written once, at do_stopping."""
        if self.reset_counters_on_start:
            self.bridge.reset_counters()

        self.run_identifier = run_identifier
        self.t0 = time.monotonic()
        self._run_started_at_wall = datetime.now().astimezone()
        self._last_report_time = self.t0
        # Last known value PER REGISTER (not just the main counter anymore),
        # so we can compute a "new triggers since last telemetry update"
        # delta for TRIGGER_COUNT, TRIG_TO_MALTA and every PLANE_x_COUNT,
        # instead of sending the raw, ever-growing hardware counter value.
        self._last_counts: dict[str, int] = {}
        # Current and peak instantaneous rate (Hz) seen for each register
        # during this run, used to fill in the run summary CSV at stop.
        self._last_rates: dict[str, float] = {}
        self._peak_rates: dict[str, float] = {}
        self._next_report_time = self.t0 + self.status_interval
        self._next_telemetry_time = self.t0 + self.telemetry_interval

        self.bridge.set_running(True)

    def do_run(self) -> str:
        """Poll trigger counters through the bridge, log and send telemetry."""
        watched = tuple(#Constructing a list by merging four sources 
            dict.fromkeys(
                [self.monitor_counter]#main counter
                + ([self.trig_to_malta_register] if self.trig_to_malta_register else [])#if trig_to_malta not none we add a list with it otherwise blank list
                + list(self.plane_registers.values())#Confirmed planes (trigger logic)
                + list(self.busy_registers.values())#Confirmed busy lines (telemetry only)
            )
        )

        while not self.stop_requested():#looping until stop from user
            now = time.monotonic()
            values = self.bridge.read_counters(watched)#reads all the register that we want as one

            if now >= self._next_telemetry_time:
                delta_t = max(1e-9, now - self._last_report_time)#time between last call

                #Compute the delta ONCE per distinct register (a register
                #can be watched under more than one "role" -- e.g. monitor_counter
                #and trig_to_malta_register are often the same register --
                #so we must not call the delta logic twice for it, or the
                #second call would always see previous == current and give 0).
                deltas: dict[str, int] = {}
                for register in watched:
                    current = values[register]
                    previous = self._last_counts.get(register, current)
                    self._last_counts[register] = current
                    deltas[register] = max(0, current - previous)

                #Track current/peak instantaneous rate per register, used
                #to fill the run summary CSV written at do_stopping.
                for register, delta_val in deltas.items():
                    reg_rate = delta_val / delta_t
                    self._last_rates[register] = reg_rate
                    self._peak_rates[register] = max(self._peak_rates.get(register, 0.0), reg_rate)

                monitor_delta = deltas[self.monitor_counter]#new triggers on the main counter since last call
                rate_hz = self._last_rates[self.monitor_counter]#calculating trigger rate

		#------------------Sending metrics----------------------
                #TRIGGER_COUNT / TRIG_TO_MALTA / PLANE_x_COUNT now report a
                #snapshot "at time T" (new counts since the last telemetry
                #update), NOT the ever-increasing cumulative hardware value.
                self.stat("TRIGGER_COUNT", monitor_delta)
                self.stat("TRIGGER_RATE", rate_hz)
                if self.trig_to_malta_register:
                    self.stat("TRIG_TO_MALTA", deltas[self.trig_to_malta_register])
                for plane, register in self.plane_registers.items():
                    if register in deltas:
                        self.stat(f"PLANE_{plane}_COUNT", deltas[register])
                for plane, register in self.busy_registers.items():
                    if register in deltas:
                        self.stat(f"BUSY_{plane}_COUNT", deltas[register])
		
		#-------------Actualising Values for next measurments-------------------- 
                self._last_report_time = now
                self._next_telemetry_time = now + self.telemetry_interval

	    #----------------Live status line in Constellation logs------------------------
            if now >= self._next_report_time:
                elapsed = now - self.t0
                self.log.info(f"Triggers={values[self.monitor_counter]}, elapsed={elapsed:.1f}s")
                self._next_report_time = now + self.status_interval

            time.sleep(self.poll_interval)

        return "Monitoring stopped"

    def do_stopping(self) -> None:
        """Disable the run, write the run summary CSV, and reset counters
        if requested."""
        self.bridge.set_running(False)

        try:
            self._write_run_summary(run_state="stopped")
        except Exception:
            self.log.exception("Failed to write run summary CSV")

        if self.reset_counters_on_stop:
            self.bridge.reset_counters()

    def _write_run_summary(self, run_state: str) -> None:
        """Write a field,value CSV summarizing the run: configuration used
        and, for each enabled plane (plus the main/MALTA counter), the
        total count and current/average/peak rate. Disabled planes show
        "NC" instead of misleading zeros. Unused registers (scope/DRS/ACK/
        spill counters) are not included at all."""
        run_end = datetime.now().astimezone()
        duration_seconds = max(0.0, time.monotonic() - self.t0)

        # Registers we actually care about for this summary: the main
        # counter, TRIG_TO_MALTA (if distinct), and every plane register
        # confirmed to exist -- deduplicated, in a stable order.
        summary_registers = list(
            dict.fromkeys(
                [self.monitor_counter]
                + ([self.trig_to_malta_register] if self.trig_to_malta_register else [])
                + list(self.all_plane_registers.values())
            )
        )
        final_values = self.bridge.read_counters(tuple(summary_registers))

        planes_enabled_str = ",".join(
            plane for plane in sorted(self.planes_enabled, key=int) if self.planes_enabled[plane]
        ) or "none"
        scintillators_enabled_str = "SC" if self.sc_enabled else "none"
        mode_label = MODE_LABELS.get(self.mode_value, f"mode {self.mode_value}")

        rows: list[tuple[str, str]] = [
            ("run_number", str(getattr(self, "run_identifier", ""))),
            ("run_state", run_state),
            ("run_start", self._run_started_at_wall.isoformat(timespec="seconds")),
            ("run_end", run_end.isoformat(timespec="seconds")),
            ("duration_seconds", f"{duration_seconds:.3f}"),
            ("mode_label", mode_label),
            ("mode_value", str(self.mode_value)),
            ("uri", str(getattr(self.bridge, "uri", ""))),
            ("poll_interval_ms", str(int(self.poll_interval * 1000))),
            ("max_rate_enabled", str(self.max_rate_enabled)),
            ("max_rate_hz", str(self.max_rate_hz)),
            ("max_rate_ns", str(hz_to_ns(self.max_rate_hz))),
            ("planes_enabled", planes_enabled_str),
            ("scintillators_enabled", scintillators_enabled_str),
        ]

        for channel in ["SC", *PLANE_IDS, "L1A"]:
            rows.append((f"veto_ns.{channel}", str(self.veto_ns.get(channel, ""))))
        for channel in ["SC", *PLANE_IDS, "L1A"]:
            rows.append((f"width_ns.{channel}", str(self.width_ns.get(channel, ""))))

        # Main/MALTA counter(s): always shown (not gated on "plane enabled").
        for register in dict.fromkeys(
            [self.monitor_counter] + ([self.trig_to_malta_register] if self.trig_to_malta_register else [])
        ):
            rows.extend(self._counter_summary_rows(register, final_values, enabled=True))

        # Per-plane counters: "NC" instead of a misleading 0/rate when not
        # applicable. Trigger planes (1/2/3) are "active" per planes_enabled;
        # busy lines (4/5/6) are "active" only if their corresponding
        # trigger plane is enabled (see BUSY_LINE_FOR_PLANE / busy_registers).
        for plane in PLANE_IDS:
            register = self.all_plane_registers.get(plane)
            if register is None:
                continue  # register doesn't exist in the address table at all
            if plane in self.busy_registers:
                active = True
            elif plane in BUSY_LINE_FOR_PLANE.values():
                active = False  # a busy line whose trigger plane is disabled
            else:
                active = self.planes_enabled.get(plane, False)
            rows.extend(self._counter_summary_rows(register, final_values, enabled=active))

        os.makedirs(self.log_folder, exist_ok=True)
        filename = os.path.join(
            self.log_folder,
            f"tlu_run_{getattr(self, 'run_identifier', 'unknown')}_{time.strftime('%Y%m%d_%H%M%S')}_summary.csv",
        )
        with open(filename, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["field", "value"])
            writer.writerows(rows)
        self.log.info(f"Run summary written to {filename}")

    def _counter_summary_rows(
        self, register: str, final_values: dict[str, int], enabled: bool
    ) -> list[tuple[str, str]]:
        """Build the four counter.<register>.* rows for one register.
        Returns "NC" for all four fields if the channel is disabled."""
        if not enabled:
            return [
                (f"counter.{register}.count", "NC"),
                (f"counter.{register}.current_rate_hz", "NC"),
                (f"counter.{register}.average_rate_hz", "NC"),
                (f"counter.{register}.peak_rate_hz", "NC"),
            ]

        count = int(final_values.get(register, 0))
        duration_seconds = max(0.0, time.monotonic() - self.t0)
        current_rate = self._last_rates.get(register, 0.0)
        average_rate = (count / duration_seconds) if duration_seconds > 0 else 0.0
        peak_rate = self._peak_rates.get(register, 0.0)
        return [
            (f"counter.{register}.count", str(count)),
            (f"counter.{register}.current_rate_hz", f"{current_rate:.3f}"),
            (f"counter.{register}.average_rate_hz", f"{average_rate:.3f}"),
            (f"counter.{register}.peak_rate_hz", f"{peak_rate:.3f}"),
        ]
    
    def do_reconfigure(self, partial_config: Configuration) -> str:
        
        #Apply a partial configuration update while in ORBIT, without
        #going through do_initializing/do_launching again. Only the keys
        #actually present in partial_config are touched; anything not
        #included keeps its current value."""
        
        if partial_config.has("log_folder"):
            self.log_folder = partial_config.get("log_folder", return_type=str)
        if partial_config.has("poll_interval_s"):
            self.poll_interval = partial_config.get("poll_interval_s", return_type=float)
        if partial_config.has("status_every_s"):
            self.status_interval = partial_config.get("status_every_s", return_type=float)
        if partial_config.has("telemetry_interval_s"):
            self.telemetry_interval = partial_config.get("telemetry_interval_s", return_type=float)
        if partial_config.has("mode"):
            self.mode_value = partial_config.get("mode", return_type=int)
 
        for plane in PLANE_IDS:
            key = f"plane_{plane}"
            if partial_config.has(key):
                self.planes_enabled[plane] = partial_config.get(key, return_type=bool)
 
        if partial_config.has("sc_enabled"):
            self.sc_enabled = partial_config.get("sc_enabled", return_type=bool)
 
        for plane in PLANE_IDS:
            key = f"veto_{plane}"
            if partial_config.has(key):
                self.veto_ns[plane] = partial_config.get(key, return_type=int)
        if partial_config.has("L1A"):
            self.veto_ns["L1A"] = partial_config.get("L1A", return_type=int)
 
        for plane in PLANE_IDS:
            key = f"width_{plane}"
            if partial_config.has(key):
                self.width_ns[plane] = partial_config.get(key, return_type=int)
        if partial_config.has("width_L1A"):
            self.width_ns["L1A"] = partial_config.get("width_L1A", return_type=int)
 
        if partial_config.has("max_rate_hz"):
            self.max_rate_hz = partial_config.get("max_rate_hz", return_type=int)
        if partial_config.has("max_rate_enabled"):
            self.max_rate_enabled = partial_config.get("max_rate_enabled", return_type=bool)
 
        if partial_config.has("reset_counters_on_start"):
            self.reset_counters_on_start = partial_config.get("reset_counters_on_start", return_type=bool)
        if partial_config.has("reset_counters_on_stop"):
            self.reset_counters_on_stop = partial_config.get("reset_counters_on_stop", return_type=bool)
 
        # Re-derive plane_registers from the updated planes_enabled, reusing
        # register_names confirmed at connect() time (no need to reconnect
        # to the bridge for a reconfigure).
        self.plane_registers = {
            plane: PLANE_COUNTER_REGISTERS[plane]
            for plane, enabled in self.planes_enabled.items()
            if enabled and PLANE_COUNTER_REGISTERS[plane] in self.register_names
        }
        for plane in self.plane_registers:
            self.register_metric(f"PLANE_{plane}_COUNT", "counts", f"New counts on plane {plane} since last telemetry update")

        self.busy_registers = {
            busy_plane: PLANE_COUNTER_REGISTERS[busy_plane]
            for trigger_plane, busy_plane in BUSY_LINE_FOR_PLANE.items()
            if self.planes_enabled.get(trigger_plane, False)
            and PLANE_COUNTER_REGISTERS[busy_plane] in self.register_names
        }
        for plane in self.busy_registers:
            self.register_metric(f"BUSY_{plane}_COUNT", "counts", f"New counts on busy line {plane} since last telemetry update")
 
        self.bridge.set_running(False)
        self.bridge.set_mode(self.mode_value)
        self.bridge.apply_configuration(
            planes_enabled=self.planes_enabled,
            scintillators={"SC": self.sc_enabled},
            veto_ns=self.veto_ns,
            width_ns=self.width_ns,
            max_rate_ns=hz_to_ns(self.max_rate_hz),
            max_rate_enabled=self.max_rate_enabled,
        )
        return f"TLU reconfigured (mode={self.mode_value})"

        
        
    def do_landing(self) -> str:
        """Disconnect from the TLU and terminate the bridge subprocess."""
        self.bridge.disconnect()
        self.bridge.close()
        return "TLU disconnected, bridge terminated"

    def fail_gracefully(self) -> str:
        """Stop the run and shut down the bridge safely."""
        try:
            self.bridge.set_running(False)
        except Exception:
            pass
        # Best-effort: only possible if a run was actually started (we have
        # a start timestamp) and the bridge is still reachable.
        if getattr(self, "_run_started_at_wall", None) is not None:
            try:
                self._write_run_summary(run_state="failed")
            except Exception:
                pass
        try:
            self.bridge.close()
        except Exception:
            pass
        return "Safe shutdown performed"

    def _choose_monitor_counter(self, register_names: set) -> str:
        if self.monitor_counter_override is not None:
            if self.monitor_counter_override not in register_names:
                raise RuntimeError(
                    f"Requested monitor_counter '{self.monitor_counter_override}' "
                    "not found in address table registers."
                )
            return self.monitor_counter_override

        for counter in DEFAULT_FALLBACK_COUNTERS:
            if counter in register_names:
                return counter

        raise RuntimeError("Could not find a suitable trigger counter to monitor.")


def main(args=None):
    """Constellation satellite for the MALTA TLU, bridged to a Python 3.9
    subprocess for Herakles compatibility."""
    parser = SatelliteArgumentParser(description=main.__doc__)
    parsed_args = vars(parser.parse_args(args))
    setup_cli_logging(parsed_args.pop("level"))
    s = MaltaTLU(**parsed_args)
    s.run_satellite()


if __name__ == "__main__":
    main()
