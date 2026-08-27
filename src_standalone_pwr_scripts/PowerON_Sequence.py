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
#==================Function to Read messagess from the slave power station==================================
def recv():
    try:
        data = dev.read(EP_IN, 64, timeout=1000)
        print("RX:", bytes(data))
        return data
    except usb.core.USBTimeoutError:
        pass
    #TJ 30/07/26
    time.sleep(0.1)# Delay of 100ms between two (Commands+Message) 

#==================Function to read status of PowerStation to define zether or not an initialisation should be made==================================
#Parameter : max_retries (default value = 10) : retries ensure that we read back a status and no wrong data 
#	     delay (default = 0.2 => 200ms)   : Delay between 2 readings of status
def recv_status(max_retries=10, delay=0.2):
    for attempt in range(max_retries):
        data = recv() #Reading Data 
        if data is not None and len(data) > 20: #If the slave sent something
            #FEmptyBuf() # Emptying buffer to ensure there's no  message shift
            return bytes(data)
        else:
            send("PW 1,ST2")	
        time.sleep(delay)
    return b''  # Nothing was received at the end of the trials 

#==================Function to empty the slave's message buffer==================================
def FEmptyBuf():
	print("Emptying Buffer")
	DtBfrCls = recv()
	while DtBfrCls is not None:
		DtBfrCls = recv()
#==================Function to Stop remote piloting of the powerstation==================================        	
def FCloseManual():
    print("CTRL+C detected -> putting power station in Manual mode before closing")
    FEmptyBuf()#Emptying buffer
    send("PW 1,LC1") #activates local mode
    recv()
    send("PW 1,SW0") #Main Output off
    recv()
    usb.util.release_interface(dev, intf.bInterfaceNumber)
    usb.util.dispose_resources(dev) 
#==============================Goal=========================
#Ensuring no outputs are on, and all outputs setup correctly
#===========================================================
def FInit():
	send("PW 1,ST2")  # Reading Slave's status to evaluate if ze have to do an initialisation or not
	data_bytes = recv_status()
	#Statuses that indicate that all the powersources are already well parametered
	valid_values =  [b'MS2,01,1,0,0000,0,0000,0,1.8,0.7,0.0,0.05,1.8,0.7,0.0,0.05,0,0,0',b'MS2,01,2,0,0000,0,0000,0,1.8,0.7,0.0,0.05,1.8,0.7,0.0,0.05,0,0,0',b'MS2,01,3,0,0000,0,0000,0,1.8,0.7,0.0,0.05,1.8,0.7,0.0,0.05,0,0,0',b'MS2,01,4,0,0000,0,0000,0,1.8,0.7,0.0,0.05,1.8,0.7,0.0,0.05,0,0,0']
	if data_bytes in valid_values: #The powerstation is already "initialised"
    		print("==================No need to do Init again, skipping it...=========================")
	else: #Initialising power station
		print("============= Need to do Init ==============================")
		send("PW 1,SW0") #Main Output off
		recv()
		#Putting off all outputs
		send("PW 1,OA0") #output A off
		recv()
		send("PW 1,OB0") #output B off
		recv()
		send("PW 1,OC0") #output C off
		recv()
		send("PW 1,OD0") #output D off
		recv()
		
		#Configuring the maximum currents on all outputs
		send("PW 1,AA0070") #AVDD 0.7A 
		recv()
		send("PW 1,AB0005") #PWELL 0.05A 
		recv()
		send("PW 1,AC0070") #DVDD 0.7A 
		recv()
		send("PW 1,AD0005") #SUB 0.05A 
		recv()

		#configuring the voltage of each sources 
		send("PW 1,VA0180") #AVDD 1.8V 
		recv()
		send("PW 1,VB0000") #PWELL 0V 
		recv()
		send("PW 1,VC0180") #DVDD 1.8V 
		recv()
		send("PW 1,VD0000") #SUB 0V 
		recv()
		
		#Sending the configuration 
		send("PW 1,PR0")
		recv()
		
		#Checking configuration visually
		send("PW 1,DS1") #output A (AVDD) display
		recv()
		time.sleep(0.5)
		send("PW 1,DS2") #output B (PWELL) display
		recv()
		time.sleep(0.5)
		send("PW 1,DS3") #output C (DVDD)  display
		recv()
		time.sleep(0.5)
		send("PW 1,DS4") #output D (SUB)  display
		recv()
#==============================Goal=========================
#Enabling SUB and PWELL outputs
#===========================================================
def FOnFirst():
	send("PW 1,OB1") #output B on
	recv()
	send("PW 1,OD1") #output D on
	recv()
#==============================Goal=========================
#Enabling DVDD and AVDD outputs
#===========================================================
def FOnSec():
	send("PW 1,OC1") #output B on
	recv()
	send("PW 1,OA1") #output D on
	recv()
#==============================Goal=========================
#Configuring PWELL and SUB to the voltage needed step by step 
#===========================================================	
def FSeqSub(Strt, Stp):
	send("PW 1,DS4") #output B (SUB) display
	recv()
	#Sequence 
	for i in range(Strt, Stp, 100):
	    	send("PW 1,VD"+ str(i).zfill(4))
	    	recv()
	    	send("PW 1,PR0")
	    	recv()
	    	time.sleep(1)
def FSeqPWELL(Strt, Stp):
	send("PW 1,DS2") #output B (PWELL) display
	recv()
	#Sequence 
	for i in range(Strt, Stp, 100):
	    	send("PW 1,VB" + str(i).zfill(4))
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

	FInit()
	send("PW 1,SW1") #Main Output off
	recv()
	FOnFirst()
	VStrt = 100
	VStp = 300
	for i in range(0,3,1):
		FSeqSub(VStrt, VStp)
		FSeqPWELL(VStrt, VStp)
		VStrt = VStp
		VStp = VStp + 200
	time.sleep(1)
	FOnSec()
	
	

		
#==============================Goal=========================
#CTRL+C detected 
#===========================================================		
except KeyboardInterrupt:
    FCloseManual()
    
send("PW 1,LC1") #activates local mode  
usb.util.release_interface(dev, intf.bInterfaceNumber)
usb.util.dispose_resources(dev)  

