import usb.core
import usb.util
import time
import os

VID = 0x098F
PID = 0x1001

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise RuntimeError("IF-41USB not found")

dev.set_configuration()
cfg = dev.get_active_configuration()
intf = cfg[(0, 0)]

try:
    if dev.is_kernel_driver_active(intf.bInterfaceNumber):
        dev.detach_kernel_driver(intf.bInterfaceNumber)
except Exception:
    pass

usb.util.claim_interface(dev, intf.bInterfaceNumber)

EP_OUT = 0x02
EP_IN  = 0x81

def send(cmd):
    print("TX:", cmd)
    dev.write(EP_OUT, (cmd + "\r\n").encode("ascii"))

def recv():
    try:
        data = dev.read(EP_IN, 64, timeout=1000)
        return data
    except usb.core.USBTimeoutError:
        return None

def FCloseManual():
    print("CTRL+C detected -> putting power station in Manual mode before closing")
    send("PW 1,LC1")  # activates local mode
    send("PW 1,TO0")
    recv()
    usb.util.release_interface(dev, intf.bInterfaceNumber)
    usb.util.dispose_resources(dev)

# --- Preparation du fichier de log ---
# Name of the folder 
folder = "logs"

# Creates the folder if it is not already done 
os.makedirs(folder, exist_ok=True)
filename = os.path.join(folder, "current_log_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
logfile = open(filename, "w")
logfile.write("Time(s)\tIAVdd(A)\tIPWell(A)\tIDVdd(A)\tISub(A)\n")
logfile.flush()

t0 = time.time()#Getting actual time

try:
    while True:
        send("PW 1,SRMODE1")  # activates service request function
        recv()
        send("PW 1,ST4")  # Request status
        VDt = recv() #Status sent by Power station 
        if VDt is None:
            print("No answer,trying again...")
            time.sleep(.5)
            continue
        ACrntVal = bytes(VDt).decode('utf-8').split(',')
        try:
            IAVdd  = float(ACrntVal[3])
            IPWell = float(ACrntVal[5])
            IDVdd  = float(ACrntVal[7])
            ISub   = float(ACrntVal[9])
        except (IndexError, ValueError) as e:
            print("Parsing error", ACrntVal, e)
            time.sleep(.5)
            continue

        t = time.time() - t0 #Getting the time of the acqusition since it started 
        print("IAVdd = %f, IPWell = %f, IDVdd = %f, ISub = %f" % (IAVdd, IPWell, IDVdd, ISub))#Printing Data ==> Debug

        # --- Writing Valid Data in a file  ---
        logfile.write("%.3f\t%.5f\t%.5f\t%.5f\t%.5f\n" % (t, IAVdd, IPWell, IDVdd, ISub))
        logfile.flush()

        time.sleep(.5)

except KeyboardInterrupt:
    FCloseManual()
    logfile.close()
