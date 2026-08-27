"""MaltaDAQ satellite: launches the MaltaMultiDAQ C++ binary and manages its lifecycle."""

from constellation.core.satellite import Satellite, SatelliteArgumentParser
from constellation.core.configuration import Configuration
from constellation.core.logging import setup_cli_logging

import subprocess
import signal
import time
import os
from pathlib import Path
import threading


class MaltaDAQ(Satellite):

    def _drain_output(self, proc, tag):
        """Separated thread to read the process' stdout and log each lines."""
        try:
            for line in proc.stdout:
                if line:
                    self.log.debug(f"[{tag}] {line.rstrip()}")
        except Exception as e:
            self.log.error(f"Error reading {tag} output: {e}")
            
            

    def do_initializing(self, config):
        #DAQ Config
        self.binary_path = config["binary_path"]#path to MaltaMultiDAQ script (datas acquisition)
        self.config_file = config["daq_config"]#Path to specific config for a sensor
        self.outdir = config.get("output_dir", "/home/itdc/work/Thomas/Constellation/MaltaDAQ/Data_test")#output directory for the rootfile
        self.work_dir = config.get("work_dir", "/home/MaltaSW/MaltaDAQ")#base path for the software
        
        #OnlineMonitor Config 
        self.monitor_script = config.get("monitor_script", "run_onlinemonitor.sh")
        self.monitor_dir = config.get("monitor_dir", self.work_dir)#Path to the monitor directory 

        
        self.proc = None
        self.monitor_proc = None
        self.StrtCorry = 0 #Starting Corry at the beginning of the run, enables to do it only once (Flag of activation)
        self.current_run_number = None

    def do_starting(self, run_id):
        self.StrtCorry = 0#Resetting Corry Flag 
        self.log.debug(f"raw run_id: {run_id!r} (type={type(run_id)})")

        parts = str(run_id).split('_')#Getting prefix of the name of the run and run number
        self.log.debug(f"parts after split: {parts}")

        if len(parts) != 2:
            raise RuntimeError(
                f"run_id '{run_id}' does not match expected 'prefix_number' format"
            )

        prefix, number_str = parts#Separating prefix and run number
        self.log.debug(f"prefix={prefix!r}, number_str={number_str!r}")

        try:
            self.current_run_number = int(number_str)
        except ValueError:
            raise RuntimeError(f"run number '{number_str}' is not an integer")

        self.log.debug(f"self.current_run_number (int): {self.current_run_number}")

        # Repertory for the data corresponding to the prefix name od the run 
        run_outdir = Path(self.outdir) / prefix
        self.log.debug(f"target outdir: {run_outdir}")

        if not run_outdir.exists():#Creating repertory if needed
            self.log.info(f"Prefix '{prefix}' never used before, creating {run_outdir}")
            run_outdir.mkdir(parents=True, exist_ok=True)
        else:
            self.log.debug(f"outdir already exists for prefix '{prefix}'")
        
        cmd = [
            self.binary_path,
            "-r", str(self.current_run_number),
            "-c", self.config_file,
            "-o", str(run_outdir),
        ]
        self.log.info(f"Launching DAQ: {' '.join(cmd)}")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=self.work_dir,
        )
        # Dedicated Thread to drain the DAQ continuously
        self.daq_reader_thread = threading.Thread(
            target=self._drain_output, args=(self.proc, "DAQ"), daemon=True
        )
        self.daq_reader_thread.start()

        deadline = time.time() + 60#The DAQ soft cqn take long to start that's why we wait for 60sec
        started = False
        while time.time() < deadline:
            if self.proc.poll() is not None:#Checking if the soft is dead or not
                raise RuntimeError(
                    f"DAQ binary exited early with code {self.proc.returncode}"
                )
            #Reading lines coming from soft and printing the in the debug 
            line = self.proc.stdout.readline()
            if line:
                self.log.debug(f"[DAQ] {line.rstrip()}")
            if "Start" in line:
                started = True
                break

        if not started:
            self.do_stopping()  # cleanup before exception
            raise RuntimeError("Timeout waiting for DAQ binary to start")
    
    def do_run(self) -> str:
        if self.StrtCorry == 0:
            self.log.debug("Launching online monitor (first call in this run)")
            self.StrtCorry = 1
            

            cmd = ["bash", self.monitor_script, str(self.current_run_number)]#Command to Start the OnlineMonitor 

            try:
                self.log.debug(f"Monitor script path: {Path(self.monitor_dir) / self.monitor_script}")
                #Opening a subprocess for the online monitor
                self.monitor_proc = subprocess.Popen(
                    cmd,
                    cwd=self.monitor_dir,
                    stdout=subprocess.PIPE,#redirecting the standard output of the binary file 
                    stderr=subprocess.STDOUT,#fusion of stdout and stderr
                    text=True,#Python conversion of bytes on stdout in str
                    bufsize=1,#sends each line finished by "\n" instantly

                )
                self.log.info(f"Online monitor started, PID={self.monitor_proc.pid}")

                # Verification if it is still alive after 2 seconds 
                time.sleep(2)
                ret = self.monitor_proc.poll()
                if ret is not None:
                    # Online Monitor dead  reading the errors
                    output = self.monitor_proc.stdout.read()
                    self.log.error(f"Online monitor died immediately, code={ret}")
                    self.log.error(f"Output:\n{output}")
                else:
                    self.log.debug("Online monitor still alive after 2s")

            except Exception as e:
                self.log.error(f"Failed to launch online monitor: {e}")
        else:
            self.log.debug("Online monitor already running, skipping relaunch")
        
        self.monitor_reader_thread = threading.Thread(
            target=self._drain_output, args=(self.monitor_proc, "OM"), daemon=True
        )
        self.monitor_reader_thread.start()
            

        return "Monitoring stopped"
    
    def do_stopping(self):
        self.StrtCorry = 0 #reset of the flag 
        if self.proc and self.proc.poll() is None: #Closing DAQ binary
            self.log.info("Sending SIGTERM to DAQ binary")
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=180)
                self.log.info(f"DAQ binary exited cleanly, code {self.proc.returncode}")
            except subprocess.TimeoutExpired:
                self.log.error("DAQ binary did not stop cleanly, killing it")
                self.proc.kill()
                self.proc.wait()
                raise RuntimeError("DAQ binary did not stop cleanly, had to kill it")
        if self.monitor_proc and self.monitor_proc.poll() is None:
            self.log.info("Sending SIGINT (Ctrl+C) to online monitor")
            self.monitor_proc.send_signal(signal.SIGINT)
            try:
                self.monitor_proc.wait(timeout=10)
                self.log.info(f"Online monitor exited cleanly, code {self.monitor_proc.returncode}")
            except subprocess.TimeoutExpired:
                self.log.warning("Online monitor did not stop cleanly after SIGINT, killing it")
                self.monitor_proc.kill()
                self.monitor_proc.wait()

    def do_landing(self):
        self.proc = None


def main(args=None):
    parser = SatelliteArgumentParser(description=main.__doc__)
    parsed_args = vars(parser.parse_args(args))
    setup_cli_logging(parsed_args.pop("level"))
    s = MaltaDAQ(**parsed_args)
    s.run_satellite()


if __name__ == "__main__":
    main()
