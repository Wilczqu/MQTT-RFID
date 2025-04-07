MQTT Python Application Documentation
1. Prerequisites
To install and run this application, ensure the following prerequisites are met:
•	Python 3.7 or later
•	MQTT broker (e.g., Mosquitto)
•	Required Python libraries:
pip install paho-mqtt openpyxl
•	Directory for Excel files: C:\script\pythonscript (will be created automatically if not existing)
2. MQTT Server Setup & Communication
The application communicates with an MQTT broker using the MQTT v3.1.1 protocol:
•	Broker IP and Port: Configure your ip for your broker & add Port 1883  
•	MQTT Topics Structure:
o	Tag data events: /tevents/{ReaderName}
o	Management (GPI events): /mevents/{ReaderName}
o	Command responses: /ctrspn/{ReaderName}
o	Commands sent to readers: /ctcmds/{ReaderName}
MQTT Command Payloads
The application sends JSON-formatted commands:
•	set_mode: Sets reader to "Inventory" mode, antennas 1-4, power 18 dBm.
•	start: Begins tag reading.
•	stop: Stops tag reading.
3. Code Logic Explained
Initialization
On startup, the application:
•	Creates an Excel workbook with a timestamped filename to log events.
•	Subscribes to topics for each RFID reader.
•	Configures readers in inventory mode with a set_mode command.
State Machine
Each RFID gate (Reader1-Reader5) maintains a state:
•	IDLE: Awaiting sensor activations.
•	READING: Actively collecting tag data.
State transitions occur based on GPIO pin events (HIGH → LOW transitions trigger sensor activations) received via MQTT messages:
•	Forward/Loading sequence:
o	Sensor 1 activates: Begins preparation for reading.
o	Sensor 1 returns HIGH: Reader begins RFID tag collection (start command).
o	Sensor 2 activates then returns HIGH: Tag reading stops (stop command), and collected data is logged to Excel.
•	Reversing sequence:
o	Initiated and completed by reversed sensor order, used to detect forklift reversal actions without tag collection.
Tag Data Handling
Collected tag data undergoes validation and filtering:
•	Only tags starting with prefix and with RSSI ≤ -70 are accepted.
•	Duplicate and previously scanned tags are ignored.
•	Tag data, RSSI, antenna, user data, and sensor activation timestamps are recorded.
Logging
Upon completion of each forward/loading sequence:
•	Tag data and timestamps are logged into an Excel file.
•	Data includes:
o	Timestamp (final read)
o	Stack type (Single/Double)
o	Gate (Reader)
o	Tag Data, RSSI, Antenna, User
o	Sensor activation times and duration
Manual Reset
A background thread listens for user input "reset" to manually reset the state machines and clear the data structures.




4. Subscriber Model
The application employs a subscriber pattern for MQTT:
•	Subscriptions:
o	/tevents/{Reader} for tag data.
o	/mevents/{Reader} for GPIO (sensor) events.
o	/ctrspn/{Reader} for command responses.
•	Event Handling:
o	on_connect: Establishes subscriptions upon successful connection.
o	on_message: Routes incoming messages based on topic structure:
	Tag data is processed and stored in memory.
	Management events trigger state transitions and RFID read cycles.
•	Data Processing:
o	Tag information is captured, validated, filtered, and temporarily stored until sensor sequences conclude, triggering data logging.
MQTT endpoint zebra reader configuration steps: 
By accessing Zebra reader admin panel, we can configure IP for the endpoint which now will be IP you have allocated select TCP port 1883.
Client ID is the name of the reader helps to define and re use for logging purposes else it defaults to mac address of the reader which is not ideal. Hostname is also required for topic subscription.


Topic configuration
/tevents/RFGate2< Tag Event Topic JSON feed. >   This is the tag information in JSON format.
/ctcmds/RFGate2< Reader RFID command/control sink.     >   This is where you issue the start/stop command JSON
/ctrspn/RFGate2< Reader RFID command/control feed.     >   This is the Command "Success/Failure" information in JSON format.

/mevents/RFGate2< Reader Management events feed.        >   This is the Event Management (Heartbeat/ GPI status) information in JSON format.
/rmcmds/RFGate2< Reader Management [RM] command/control sink. >   This is where you issue the Reader Management -rm- commands in JSON format.
/rmrspn/RFGate2< Reader Management JSON Response Feed. >   This is the RM Command "Success/Failure" information in JSON format.
 s
 

Lastly we need to select interfaces which we configured for each event it is as follows replicated over 5 bays. 
 
Useful links 
Zebra IOT Documentation 
Introduction — Zebra IoT Connector documentation
Connecting to MQTT Broker 
Connect Fixed Readers to MQTT Broker — Zebra IoT Connector documentation
Different operating modes for the reader, preferred way is “Inventory” please see below snipped from Zebra engineer as to why:

To effectively control the reader via MQTT - the UI on the reader at Danone should have all the interfaces connected to the MQTT broker definition, for correct control. 
{namely Start Inventory and Stop Inventory - which your use-case and python code may actually need. )
The Above topics only give you: A) Tag Data, B) Reader passive status.
Missing is the Topics for issuing RFID controls (Start / Stop) and Reader Management controls -- We would normally suggest to also have these available so you have clear control over the RFID reader.

Since you only listed the two topics - this means that the reader Control and config is "Reader Local” and you would need a nearby system with Tools like PostMan to issue config / control to the reader after logging in.  ( Or logging into the web console and updating web pages. )

At first glance the [Portal] - Operating mode - may seem to be what you can use - but I may recommend to not use Portal - since this is designed for "Drive-Thru" use cases.
To effectively control the reader via MQTT - the UI on the reader at Danone should have all the interfaces connected to the MQTT broker definition, for correct control. 
{namely Start Inventory and Stop Inventory - which your use-case may actually need. )
The Above topics only give you: A) Tag Data,  B) Reader passive status.
Missing is the Topics for issuing RFID controls (Start / Stop)  and Reader Management controls  -- We would normally suggest to also have these available so you have clear control over the RFID reader.

Since you only listed the two topics - this means that the reader Control and config is "Reader Local"  and you would need a nearby system with Tools like PostMan to issue config / control to the reader after logging in.  (Or logging into the web console and updating web pages. )

At first glance the [Portal] - Operating mode - may seem to be what you can use - but I may recommend to not use Portal - since this is  designed for "Drive-Thru" use cases.
https://zebradevs.github.io/rfid-ziotc-docs/introduction/operating_modes/index.html

Instead - the [Inventory] - Operating mode - may be wiser - to use: This means you need to "ACTION" the inventory - to start - when you see the GPI to confirm that the READER needs to Activate - and then when the GPI Clears to then stop and use that data. 

Instead - the [Inventory] - Operating mode - may be wiser - to use: This means you need to "ACTION" the inventory - to start - when you see the GPI to confirm that the READER needs to Activate - and then when the GPI Clears to then stop and use that data.
MQTT payloads 
RAW MQTT Payload Schemas — Zebra IoT Connector documentation
