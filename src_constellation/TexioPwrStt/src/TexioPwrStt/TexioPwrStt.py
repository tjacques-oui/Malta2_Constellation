import os
import time

import usb.core
import usb.util

from constellation.core.satellite import Satellite, SatelliteArgumentParser
from constellation.core.configuration import Configuration
from constellation.core.logging import setup_cli_logging


class PowerStation(Satellite):
    """Satellite piloting powersource Texio PW-A (IF-41USB).

    - do_initializing : Starting connection with powersource
    - do_launching    : PowerOn sequence
    - do_run          : Current monitoring, telemetry sent to TelemetryConsole
    - do_landing      : PowerOff sequence
    """

    # ------------------------------------------------------------------
    # FSM transitions
    # ------------------------------------------------------------------
    
    # --------------- Initialisation ==> Connection to PowerSource --------------------------------
    def do_initializing(self, config: Configuration) -> str:
        vid = config.get_int("vendor_id", 0x098F)
        pid = config.get_int("product_id", 0x1001)
        self.PwrAdd = config.get_str("PwrSttAdd", "PW 1").strip()
        self.log_folder = config.get_str("log_folder", "logs")
        self.poll_interval = config.get_float("poll_interval", 0.5)
        self.v_dvdd = config.get_float("v_dvdd", 1.80)
        self.v_avdd = config.get_float("v_avdd", 1.80)
        self.i_dvdd = config.get_float("i_dvdd", 0.7)
        self.i_avdd = config.get_float("i_avdd", 0.7)
        self.v_pwell = config.get_float("v_pwell", 6.0)
        self.v_sub = config.get_float("v_sub", 6.0)
        self.i_pwell = config.get_float("i_pwell", 0.05)
        self.i_sub = config.get_float("i_sub", 0.05)
        # Short-circuit detection: an output is considered shorted if its
        # measured voltage drops below this fraction of its configured
        # setpoint (e.g. 0.5 = below 50% of the target voltage), rather
        # than a fixed absolute threshold that wouldn't scale between the
        # ~1.8V (AVDD/DVDD) and ~6V (PWELL/SUB) outputs.
        self.short_circuit_threshold_pct = config.get_float("short_circuit_threshold_pct", 0.5)

        # do_initializing can be called again (NEW/INIT/ERROR/SAFE): close
        # any previous connection before opening a new one.
        if getattr(self, "dev", None):
            try:
                usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
            except Exception:
                pass
            usb.util.dispose_resources(self.dev)
            self.dev = None

        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is None:
            raise RuntimeError(f"Unable to find powerstation ({vid:#06x}:{pid:#06x})")

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                dev.detach_kernel_driver(intf.bInterfaceNumber)
        except Exception:
            pass

        usb.util.claim_interface(dev, intf.bInterfaceNumber)

        self.dev = dev
        self.intf = intf
        self.ep_out = 0x02
        self.ep_in = 0x81
        
        # Flush any stale data left over from a previous session/connection
        # BEFORE querying the address, otherwise we might read an old
        # leftover response (e.g. from a previous ST2/ST4 query) instead of
        # the real answer to "PW?".
        self._empty_buffer()
        APW_station = bytes(self._recv_status(10, 0.2, 4, "PW?")).decode('utf-8').split(',')
        VerifOk = 0
        for i in range(0, len(APW_station), 1):
            # .strip() removes any leading/trailing whitespace or control
            # characters (\r, \n, padding bytes...) that the log viewer
            # doesn't render but that break a strict string comparison.
            VName = APW_station[i].strip()
            # repr() shows hidden characters explicitly (e.g. '\r', '\x00')
            # -- useful to confirm exactly what byte pattern is received.
            self.log.debug(f"VName raw: {APW_station[i]!r} -> stripped: {VName!r}")
            if VName == self.PwrAdd:
                VerifOk = 1
                break
            else:
                continue

        if VerifOk == 0:
            raise RuntimeError(f"No powerstation found for specified address {self.PwrAdd!r}")
        else:
            self.log.debug("PowerStation found : " + VName)
	    
        # Register the metrics once so they show up in the TelemetryConsole.
        # register_metric can be called repeatedly (do_initializing may be
        # re-entered), Constellation ignores duplicate registrations.
        self.register_metric("IAVDD", "A", "Current on the AVDD output")
        self.register_metric("IPWELL", "A", "Current on the PWELL output")
        self.register_metric("IDVDD", "A", "Current on the DVDD output")
        self.register_metric("ISUB", "A", "Current on the SUB output")

        return f"Connected to powerstation ({vid:#06x}:{pid:#06x})"
        
    # ------------------------ Launching ==> PowerOn Sequence ------------------------------------------
    def do_launching(self) -> str:
   
        self._send(str(self.PwrAdd) + ",SRMODE1")  # enable remote control
        self._recv()
	
	# Configuring the initial state of the power sources if necessary 
        self._finit()

        self._send(str(self.PwrAdd) +",SW1")  # enable main output
        self._recv()

        self._on_first()  # enable PWELL (B) and SUB (D)

	# Sequence for PWell and Sub PowerOn
        v_strt, v_stp = 100, 300
        for _ in range(3):
            self._seq_sub_on(v_strt, v_stp)
            self._seq_pwell_on(v_strt, v_stp)
            v_strt = v_stp
            v_stp += 200
        time.sleep(1) #last delay to ensure all the powersources are well configured 

        self._on_sec()  # enable DVDD (C) and AVDD (A)

        return "PowerOn sequence successful"
    
    # ----------------------------- Starting (Transition) ==> Opening logfile to save current consumption ------------------------------------------
    def do_starting(self, run_identifier: str) -> None:
   
        os.makedirs(self.log_folder, exist_ok=True)
        filename = os.path.join(
            self.log_folder,
            f"current_log_{run_identifier}_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        self.logfile = open(filename, "w")
        self.logfile.write("Time(s)\tIAVdd(A)\tIPWell(A)\tIDVdd(A)\tISub(A)\n")
        self.logfile.flush()
        self.t0 = time.time()

    # ----------------------------- run ==> Monitoring current (sent to Telemetry) and saving it (logfile) ------------------------------------------   
    def do_run(self) -> str:
        while not self.stop_requested():
            self._send(str(self.PwrAdd) + ",ST4")  # status request ==> can do it directly the remote piloting has not been stopped
            data = self._recv()

            if data is None:
                self.log.warning("No answer from powerstation")
                time.sleep(self.poll_interval)
                continue

            values = bytes(data).decode("utf-8").split(",") #Decoding string chain and keeping Amps values
            try:
                v_avdd = float(values[2])
                i_avdd = float(values[3])
                v_pwell = float(values[4])
                i_pwell = float(values[5])
                v_dvdd = float(values[6])
                i_dvdd = float(values[7])
                v_sub = float(values[8])
                i_sub = float(values[9])
            except (IndexError, ValueError) as e:
                self.log.warning(f"Parsing error, data not saved: {values} ({e})")
                time.sleep(self.poll_interval)
                continue

            # Safety checks: warn on high current, raise on suspected short
            # circuit (low voltage). Raising here stops do_run and triggers
            # fail_gracefully, which turns the output off.
            self._check_currents(i_avdd, i_pwell, i_dvdd, i_sub)
            self._check_voltages(v_avdd, v_pwell, v_dvdd, v_sub)

            t = time.time() - self.t0
            self.log.info(
                f"IAVdd={i_avdd:.5f}A IPWell={i_pwell:.5f}A "
                f"IDVdd={i_dvdd:.5f}A ISub={i_sub:.5f}A"
            )

            # Send values to the TelemetryConsole via the registered metrics.
            self.stat("IAVDD", i_avdd)
            self.stat("IPWELL", i_pwell)
            self.stat("IDVDD", i_dvdd)
            self.stat("ISUB", i_sub)

            self.logfile.write(
                f"{t:.3f}\t{i_avdd:.5f}\t{i_pwell:.5f}\t{i_dvdd:.5f}\t{i_sub:.5f}\n"
            )
            self.logfile.flush()

            time.sleep(self.poll_interval)

        return "Monitoring stopped"
    # ----------------------------- stopping (Transition) ==> Closing log file, empyting slave's buffer ------------------------------------------
    def do_stopping(self) -> None:
        
        if getattr(self, "logfile", None):
            self.logfile.close()
            self.logfile = None
        self._empty_buffer()
    # ----------------------------- landing (transition) ==> Power Down Sequence ------------------------------------------
    def _power_down_sequence(self) -> None:
        """Full, graceful power-down sequence: disable DVDD/AVDD first,
        ramp PWELL/SUB down progressively, disable PWELL/SUB, then cut the
        main output and switch back to local mode. Shared by do_landing
        (normal shutdown) and fail_gracefully (error shutdown), so an
        error never causes an abrupt SW0 cut instead of the proper ramp."""
        self._off_first()  # disable DVDD (C) and AVDD (A)

        # Power-down sequence for PWELL and SUB
        v_strt, v_stp = 100, 300
        for _ in range(3):
            self._seq_pwell_down(v_strt, v_stp)
            self._seq_sub_down(v_strt, v_stp)
            v_strt = v_stp
            v_stp += 200
        time.sleep(1)

        self._off_sec()  # disable PWELL (B) and SUB (D)

        self._send(str(self.PwrAdd) + ",SW0")  # disable main output
        self._recv()

        self._empty_buffer()  # empties slave's buffer before closing connection

        self._send(str(self.PwrAdd) + ",LC1")  # switch back to local (manual) mode
        self._recv()

    def do_landing(self) -> str:
    	#TEST
        #self._send(str(self.PwrAdd) +",SRMODE1")  # enable remote control
        #self._recv()

        self._power_down_sequence()
   
        usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
        usb.util.dispose_resources(self.dev)
        self.dev = None

        return "Powerstation switched off, USB released"

    def fail_gracefully(self) -> str:
        """Run the full power-down sequence (same as do_landing) and close
        the USB connection, rather than just cutting the output abruptly."""
        try:
            self._power_down_sequence()
        except Exception:
            # Best-effort: if the graceful sequence itself fails (e.g. the
            # device stopped responding), fall back to a hard SW0 cut so we
            # at least attempt to turn the output off.
            try:
                self._send(str(self.PwrAdd) + ",SW0")
                self._recv()
                self._send(str(self.PwrAdd) + ",LC1")
                self._recv()
            except Exception:
                pass
        if getattr(self, "dev", None):
            try:
                usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
            except Exception:
                pass
            usb.util.dispose_resources(self.dev)
            self.dev = None
        if getattr(self, "logfile", None):
            self.logfile.close()
            self.logfile = None
        return "Safe shutdown performed"

    # ------------------------------------------------------------------
    # Low-level USB communication
    # ------------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        self.log.debug(f"TX: {cmd}")
        self.dev.write(self.ep_out, (cmd + "\r\n").encode("ascii"))

    def _recv(self):
        try:
            data = self.dev.read(self.ep_in, 64, timeout=1000)
            self.log.debug(f"RX: {bytes(data)}")
            return data
        except usb.core.USBTimeoutError:
            return None
        finally:
            time.sleep(0.1)  # 100ms delay between command and response (TJ 30/07/26)

    def _recv_status(self, max_retries: int = 10, delay: float = 0.2, data_length : int = 20, cmd : str = "PW 1,ST2"):
        """Retry reading the status until a valid frame is received."""
        for _ in range(max_retries):
            data = self._recv()
            if data is not None and len(data) > data_length:
                return bytes(data)
            self._send(str(cmd))
            time.sleep(delay)
        return b""

    def _check_currents(self, i_avdd: float, i_pwell: float, i_dvdd: float, i_sub: float) -> None:
        """Log a warning for any output whose measured current is at or
        above 90% of its configured max current (self.i_avdd, etc.),
        signaling the output is approaching its current limit."""
        readings = {
            "AVDD": (i_avdd, self.i_avdd),
            "PWELL": (i_pwell, self.i_pwell),
            "DVDD": (i_dvdd, self.i_dvdd),
            "SUB": (i_sub, self.i_sub),
        }
        for name, (measured, max_current) in readings.items():
            if max_current > 0 and measured >= 0.9 * max_current:
                self.log.warning(
                    f"{name} current {measured:.3f}A is at or above 90% of the "
                    f"configured limit ({max_current:.3f}A)"
                )

    def _check_voltages(self, v_avdd: float, v_pwell: float, v_dvdd: float, v_sub: float) -> None:
        """Raise an error if any output voltage drops below a fraction of
        its configured setpoint (self.short_circuit_threshold_pct), a
        strong indicator of a short circuit. A relative threshold is used
        rather than a fixed absolute value, since AVDD/DVDD (~1.8V) and
        PWELL/SUB (~6V) have very different nominal voltages. Raising here
        (from do_run) stops the run and triggers fail_gracefully, which
        turns the output off."""
        readings = {
            "AVDD": (v_avdd, self.v_avdd),
            "PWELL": (v_pwell, self.v_pwell),
            "DVDD": (v_dvdd, self.v_dvdd),
            "SUB": (v_sub, self.v_sub),
        }
        for name, (measured, setpoint) in readings.items():
            threshold = setpoint * self.short_circuit_threshold_pct
            if setpoint > 0 and measured < threshold:
                raise RuntimeError(
                    f"Possible short circuit on {name}: voltage {measured:.3f}V "
                    f"is below {self.short_circuit_threshold_pct * 100:.0f}% of the "
                    f"configured setpoint ({setpoint:.3f}V)"
                )

    def _empty_buffer(self) -> None:
        data = self._recv()
        while data is not None:
            data = self._recv()

    # ------------------------------------------------------------------
    # PowerOn sequence
    # ------------------------------------------------------------------

    def _fmt_setpoint(self, value: float) -> str:
        """Converts a setpoint value (V or A) into the 4-digit string the
        powerstation expects, e.g. 1.80 -> "0180", 0.7 -> "0070"."""
        return f"{round(value * 100):04d}"

    def _valid_statuses(self) -> list:
        """Statuses indicating that the outputs are already correctly
        configured with the CURRENT (config-driven) setpoints. Built at
        runtime (not a class attribute) because it needs self.i_avdd etc.,
        which are only known after do_initializing has read the config.

        CAUTION: the powerstation may format its own status reply with a
        different number of decimals than Python's default str(float)
        (e.g. it might report "0.70" while Python prints "0.7"). If this
        check never matches even when the outputs ARE already correct,
        that mismatch is the likely cause -- adjust the formatting below
        (e.g. f"{self.i_avdd:.2f}") to match what you observe in the RX
        debug logs.
        """
        entries = []
        for channel in ("1", "2", "3", "4"):
            entries.append(
                f"MS2,01,{channel},0,0000,0,0000,0,"
                f"{self.v_avdd},{self.i_avdd},0.0,{self.i_pwell},"
                f"{self.v_dvdd},{self.i_dvdd},0.0,{self.i_sub},0,0,0"
            )
        return [entry.encode("ascii") for entry in entries]

    def _finit(self) -> None:
        """Check the powerstation status and (re)configure the outputs if needed."""
        self._send(str(self.PwrAdd) +",ST2")
        status = self._recv_status(10,0.2,20,str(self.PwrAdd) +",ST2")#max retries = 10, delay between 2 send = 0.2 seconds, awaited data length = 20
        self._send(str(self.PwrAdd) +",SW0")
        self._recv()
        
        if status in self._valid_statuses():
            self.log.info("Configuration already correct, no need to reinitialize")
            self._empty_buffer()
            return

        self.log.info("Configuration required, initializing outputs")
        

        for cmd in (str(self.PwrAdd) +",OA0", str(self.PwrAdd) +",OB0", str(self.PwrAdd) +",OC0", str(self.PwrAdd) +",OD0"):
            self._send(cmd)
            self._recv()

        # Max currents: AVDD/PWELL/DVDD/SUB, from config
        for cmd in (
            f"{self.PwrAdd},AA{self._fmt_setpoint(self.i_avdd)}",
            f"{self.PwrAdd},AB{self._fmt_setpoint(self.i_pwell)}",
            f"{self.PwrAdd},AC{self._fmt_setpoint(self.i_dvdd)}",
            f"{self.PwrAdd},AD{self._fmt_setpoint(self.i_sub)}",
        ):
            self._send(cmd)
            self._recv()

        # Voltages: AVDD/DVDD from config (PWELL/SUB stay at 0V here, ramped
        # up progressively later in do_launching).
        for cmd in (
            f"{self.PwrAdd},VA{self._fmt_setpoint(self.v_avdd)}",
            f"{self.PwrAdd},VB0000",
            f"{self.PwrAdd},VC{self._fmt_setpoint(self.v_dvdd)}",
            f"{self.PwrAdd},VD0000",
        ):
            self._send(cmd)
            self._recv()

        self._send(str(self.PwrAdd) +",PR0")  # send configuration
        self._recv()

        for cmd in (str(self.PwrAdd) +",DS1", str(self.PwrAdd) +",DS2", str(self.PwrAdd) +",DS3", str(self.PwrAdd) +",DS4"):
            self._send(cmd)
            self._recv()
            time.sleep(0.5)
        self._empty_buffer()

    def _on_first(self) -> None:
        """Enable PWELL (B) and SUB (D)."""
        for cmd in (str(self.PwrAdd) +",OB1", str(self.PwrAdd) +",OD1"):
            self._send(cmd)
            self._recv()

    def _on_sec(self) -> None:
        """Enable DVDD (C) and AVDD (A)."""
        for cmd in (str(self.PwrAdd) +",OC1", str(self.PwrAdd) +",OA1"):
            self._send(cmd)
            self._recv()

    def _seq_sub_on(self, start: int, stop: int) -> None:
        """Ramp the SUB (D) voltage up in steps of 100."""
        self._send(str(self.PwrAdd) +",DS4")
        self._recv()
        for i in range(start, stop, 100):
            self._send(f"{self.PwrAdd},VD{i:04d}")
            self._recv()
            self._send(str(self.PwrAdd) +",PR0")
            self._recv()
            time.sleep(1)

    def _seq_pwell_on(self, start: int, stop: int) -> None:
        """Ramp the PWELL (B) voltage up in steps of 100."""
        self._send(str(self.PwrAdd) +",DS2")
        self._recv()
        for i in range(start, stop, 100):
            self._send(f"{self.PwrAdd},VB{i:04d}")
            self._recv()
            self._send(str(self.PwrAdd) +",PR0")
            self._recv()
            time.sleep(1)

    # ------------------------------------------------------------------
    # PowerOff sequence
    # ------------------------------------------------------------------

    def _off_first(self) -> None:
        """Disable DVDD (A) and AVDD (C) outputs."""
        for cmd in (str(self.PwrAdd) +",OA0", str(self.PwrAdd) +",OC0"):
            self._send(cmd)
            self._recv()

    def _off_sec(self) -> None:
        """Disable PWELL (B) and SUB (D) outputs."""
        for cmd in (str(self.PwrAdd) +",OB0", str(self.PwrAdd) +",OD0"):
            self._send(cmd)
            self._recv()

    def _seq_sub_down(self, start: int, stop: int) -> None:
        """Ramp the SUB (D) voltage down in steps of 100."""
        self._send(str(self.PwrAdd) +",DS4")
        self._recv()
        for i in range(start, stop, 100):
            self._send(f"{self.PwrAdd},VD{600 - i:04d}")
            self._recv()
            self._send(str(self.PwrAdd) +",PR0")
            self._recv()
            time.sleep(1)

    def _seq_pwell_down(self, start: int, stop: int) -> None:
        """Ramp the PWELL (B) voltage down in steps of 100."""
        self._send(str(self.PwrAdd) +",DS2")
        self._recv()
        for i in range(start, stop, 100):
            self._send(f"{self.PwrAdd},VB{600 - i:04d}")
            self._recv()
            self._send(str(self.PwrAdd) +",PR0")
            self._recv()
            time.sleep(1)


def main(args=None):
    """Constellation satellite for the Texio PW-A powerstation."""
    parser = SatelliteArgumentParser(description=main.__doc__)
    args = vars(parser.parse_args(args))
    setup_cli_logging(args.pop("level"))
    s = PowerStation(**args)
    s.run_satellite()


if __name__ == "__main__":
    main()
