import usb.core
import usb.util
import time
import matplotlib.pyplot as plt
import os

VID = 0x098F
PID = 0x1001
#================ USB communication configuration =================
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
#================ Sending and receiving functions ==> debug print to inform user what is happening =================
def send(cmd):
    print("TX:", cmd)
    dev.write(EP_OUT, (cmd + "\r\n").encode("ascii"))

def recv():
    try:
        data = dev.read(EP_IN, 64, timeout=1000)
        return data
    except usb.core.USBTimeoutError:
        return None

#================ FCloseManual =================
#goal : detects that the user wants to stop the software (CTRL + C), puts the power station in manual mode back and closes USB connection 
def FCloseManual():
    print("CTRL+C detected -> putting power station in Manual mode before closing")
    
    #Emptying slave Buffer
    DtBfrCls = recv()
    while DtBfrCls is not None:
        DtBfrCls = recv()
    
    send("PW 1,LC1")  # activates local mode
    recv()
    usb.util.release_interface(dev, intf.bInterfaceNumber)
    usb.util.dispose_resources(dev)

    
#================ Configuration of the curve and the log file =================
# --- log file configuration ---
# Name of the folder 
folder = "logs"

# Creates the folder if it is not already done 
os.makedirs(folder, exist_ok=True)
filename = os.path.join(folder, "current_log_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
logfile = open(filename, "w")
logfile.write("Time(s)\tIAVdd(A)\tIPWell(A)\tIDVdd(A)\tISub(A)\n")
logfile.flush()

# --- Curve configuration ---
plt.ion()  # The graph is being actualised without blocking the software
fig, ax = plt.subplots()

# --- Creation of the arrays that will contain the datas ---
ATDt = []#Array that will contain the timings of each acquisitions 
AIAvddDt  = []#Array that will contain the current consumption on the AVDD output
AIPwellDt = []#Array that will contain the current consumption on the PWELL output
AIDvddDt  = []#Array that will contain the current consumption on the DVDD output
AISubDt   = []#Array that will contain the current consumption on the SUB output

# --- Creation of the different curves ---
line_IAVdd,  = ax.plot([], [], label="IAVdd")
line_IPWell, = ax.plot([], [], label="IPWell")
line_IDVdd,  = ax.plot([], [], label="IDVdd")
line_ISub,   = ax.plot([], [], label="ISub")

# --- Naming the different axis and the curve ---
ax.set_xlabel("Time (s)")
ax.set_ylabel("Current (A)")
ax.set_title("Current consumption through time")
ax.legend()
ax.grid(True)

t0 = time.time() # getting the time

#============================ Main ============================
#Remark : Running until a Ctrl + C is detected 
#==============================================================
try:
    while True:
        send("PW 1,SRMODE1")  # activates service request function
        recv()

        send("PW 1,ST4")  # Request status
        VDt = recv()

        if VDt is None: # no datas were sent by the power station 
            print("No answer trying again")
            time.sleep(.5)
            continue

        ACrntVal = bytes(VDt).decode('utf-8').split(',') # Saving the data read back from the power station
	# Getting all datas (IAVDD, IDVDD, IPWELL, ISUB) individually
        try:
            VIAVdd  = float(ACrntVal[3])
            VIPWell = float(ACrntVal[5])
            VIDVdd  = float(ACrntVal[7])
            VISub   = float(ACrntVal[9])
        except (IndexError, ValueError) as e: # The power station didn't send a correct word 
            print("Parsing error, data not saved", ACrntVal, e)
            time.sleep(.5)
            continue

        Vt = time.time() - t0 # getting the time that passed between now and last acquisition 
        print("IAVdd = %f, IPWell = %f, IDVdd = %f, ISub = %f" % (VIAVdd, VIPWell, VIDVdd, VISub)) # Debug print ==> Can be removed

        # --- Valid data tracing ---
        ATDt.append(Vt)
        AIAvddDt.append(VIAVdd)
        AIPwellDt.append(VIPWell)
        AIDvddDt.append(VIDVdd)
        AISubDt.append(VISub)

        line_IAVdd.set_data(ATDt, AIAvddDt)
        line_IPWell.set_data(ATDt, AIPwellDt)
        line_IDVdd.set_data(ATDt, AIDvddDt)
        line_ISub.set_data(ATDt, AISubDt)

        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw()
        fig.canvas.flush_events()
        
        # --- Writing Valid Data in a file  ---
        logfile.write("%.3f\t%.5f\t%.5f\t%.5f\t%.5f\n" % (Vt, VIAVdd, VIPWell, VIDVdd, VISub))
        logfile.flush()

        time.sleep(.5)

except KeyboardInterrupt:
    FCloseManual()
    plt.ioff()
    plt.show()  # keeps window open after the monitoring stops 
    logfile.close()
