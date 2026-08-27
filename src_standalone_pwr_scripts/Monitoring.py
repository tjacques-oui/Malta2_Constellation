import usb.core
import usb.util
import time
import matplotlib.pyplot as plt

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

# --- Graph setup ---
plt.ion()  # The graph is actualising without blocking the software
fig, ax = plt.subplots()

t_data = []
IAVdd_data  = []
IPWell_data = []
IDVdd_data  = []
ISub_data   = []

line_IAVdd,  = ax.plot([], [], label="IAVdd")
line_IPWell, = ax.plot([], [], label="IPWell")
line_IDVdd,  = ax.plot([], [], label="IDVdd")
line_ISub,   = ax.plot([], [], label="ISub")

ax.set_xlabel("Time (s)")
ax.set_ylabel("Current (A)")
ax.set_title("Current Consumption through time")
ax.legend()
ax.grid(True)

t0 = time.time()

try:
    while True:
        send("PW 1,SRMODE1")  # activates service request function
        recv()

        send("PW 1,ST4")  # Status request
        VDt = recv()

        if VDt is None:
            print("No Answer (timeout), Trying again...")
            time.sleep(.5)
            continue

        ACrntVal = bytes(VDt).decode('utf-8').split(',')

        try:
            IAVdd  = float(ACrntVal[3])
            IPWell = float(ACrntVal[5])
            IDVdd  = float(ACrntVal[7])
            ISub   = float(ACrntVal[9])
        except (IndexError, ValueError) as e:
            print("Parsing error :", ACrntVal, e)
            time.sleep(.5)
            continue

        t = time.time() - t0
        print("IAVdd = %f, IPWell = %f, IDVdd = %f, ISub = %f" % (IAVdd, IPWell, IDVdd, ISub))

        # --- Data actualisation ---
        t_data.append(t)
        IAVdd_data.append(IAVdd)
        IPWell_data.append(IPWell)
        IDVdd_data.append(IDVdd)
        ISub_data.append(ISub)

        line_IAVdd.set_data(t_data, IAVdd_data)
        line_IPWell.set_data(t_data, IPWell_data)
        line_IDVdd.set_data(t_data, IDVdd_data)
        line_ISub.set_data(t_data, ISub_data)

        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw()
        fig.canvas.flush_events()

        time.sleep(.5)

except KeyboardInterrupt:
    FCloseManual()
    plt.ioff()
    plt.show()  # Keep the window open after end of script
