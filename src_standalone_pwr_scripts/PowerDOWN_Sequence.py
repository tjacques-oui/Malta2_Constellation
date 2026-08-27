import usb.core
import usb.util
import time

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

#==================Function to Send commands to the slave power station==================================
def send(cmd):
    print("TX:", cmd)
    dev.write(EP_OUT, (cmd + "\r\n").encode("ascii"))
#==================Function to Read messages from the slave power station==================================
def recv():
    try:
        data = dev.read(EP_IN, 64, timeout=1000)
        print("RX:", bytes(data))
    except usb.core.USBTimeoutError:
        pass
    #TJ 30/07/26
    time.sleep(0.1)# Delay of 100ms between two (Commands+Message) 
    
#==================Function to Stop remote piloting of the powerstation==================================      
def FCloseManual():
    print("CTRL+C detected -> putting power station in Manual mode before closing")
    send("PW 1,LC1") #activates local mode
    send("PW 1,SW0") #Main Output off
    send("PW 1,TO0")
    usb.util.release_interface(dev, intf.bInterfaceNumber)
    usb.util.dispose_resources(dev) 
    recv()
#==============================Goal=========================
#Disabling AVDD and DVDD outputs
#===========================================================
def FOffFirst():
	send("PW 1,OA0") #output A off
	recv()
	send("PW 1,OC0") #output C off
	recv()
#==============================Goal=========================
#Disabling PWELL and SUB outputs
#===========================================================
def FOffSec():
	send("PW 1,OB0") #output B off
	recv()
	send("PW 1,OD0") #output D off
	recv()
#==============================Goal=========================
#Configuring PWELL and SUB to the voltage needed step by step 
#===========================================================	
def FSeqSUB(Strt, Stp):
	send("PW 1,DS4") #output B (SUB) display
	recv()
	#Sequence 
	for i in range(Strt, Stp, 100):
	    	send("PW 1,VD"+ str(600-i).zfill(4))
	    	recv()
	    	send("PW 1,PR0")
	    	recv()
	    	time.sleep(1)
def FSeqPWELL(Strt, Stp):
	send("PW 1,DS2") #output B (PWELL) display
	recv()
	#Sequence 
	for i in range(Strt, Stp, 100):
	    	send("PW 1,VB" + str(600-i).zfill(4))
	    	recv()
	    	send("PW 1,PR0")
	    	recv()
	    	time.sleep(1)   
#==============================Goal=========================
#Main code for the sequence ==> Runs until the end of the sequence or if a CTRL+C is detected 
#===========================================================  	 	
try:
	send("PW 1,SRMODE1") # activates service request function 
	recv()

	FOffFirst()
	VStrt = 100
	VStp = 300
	for i in range(0,3,1):
		FSeqPWELL(VStrt, VStp)
		FSeqSUB(VStrt, VStp)
		VStrt = VStp
		VStp = VStp + 200
	time.sleep(1)
	FOffSec()
	send("PW 1,SW0") #Main Output off
	recv()
	
	

		
		
except KeyboardInterrupt:
    FCloseManual()
    
send("PW 1,LC1") #activates local mode  
usb.util.release_interface(dev, intf.bInterfaceNumber)
usb.util.dispose_resources(dev)  

