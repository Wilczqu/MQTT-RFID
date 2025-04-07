import paho.mqtt.client as mqtt
import json
import os
import time
import threading
from datetime import datetime
from openpyxl import Workbook, load_workbook

# ------------------------------------------------------------------------------
# Unique Excel File Configuration
# ------------------------------------------------------------------------------
timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
excel_dir = r"Your_Folder_Location"
if not os.path.exists(excel_dir):
    os.makedirs(excel_dir)
EXCEL_FILE = os.path.join(excel_dir, f"MQTTReads_{timestamp_str}.xlsx")

try:
    wb = load_workbook(EXCEL_FILE)
    ws = wb.active
    print(f"Loaded existing workbook: {EXCEL_FILE}")
except FileNotFoundError:
    wb = Workbook()
    ws = wb.active
    # New header: includes Sensor 1, Sensor 2, and Time Difference columns
    ws.append([
        "Timestamp",          # Final read logging time
        "StackType",          # Single/Double
        "Gate",               # e.g. Your Device name 
        "TagData",            # e.g. Prefix for the data you are lookin for 
        "RSSI",               # e.g. -60
        "Antenna",            # e.g. 1
        "User",               # e.g. some user data
        "Sensor 1 Activated", # Time the first sensor went LOW
        "Sensor 2 Activated", # Time the second sensor went LOW
        "Time Difference"     # (seconds) final read time - sensor1 time
    ])
    print(f"Created new workbook: {EXCEL_FILE}")

# ------------------------------------------------------------------------------
# Global Variables and State
# ------------------------------------------------------------------------------
readers = ["Reader1", "Reader2", "Reader3", "Reader4", "Reader5"]

TOPIC_PREFIXES = {
    "tag": "/tevents/",  # looking for events for tag
    "mgmt": "/mevents/",  # looking for events for managment of the reader
    "cmd": "/ctcmds/",    # Writing commands 
    "rsp": "/ctrspn/"    # reading response from pushing commands
}

# State for each gate
state = {reader: "IDLE" for reader in readers}

# Forward/loading flags
forward_triggered = {reader: False for reader in readers}
loading_triggered = {reader: False for reader in readers}

# Reversing flags
reversing_initiated = {reader: False for reader in readers}
reversing_completed = {reader: False for reader in readers}

# Up to 2 tags can be captured
two_tags_captured = {reader: False for reader in readers}

# Tag data structures
collected_tags = {reader: set() for reader in readers}
tag_rssi_map = {reader: {} for reader in readers}
tag_antenna_map = {reader: {} for reader in readers}
tag_user_map = {reader: {} for reader in readers}
logged_tags = {reader: set() for reader in readers}

# Keep track if a tag was scanned on a different gate
global_scanned_tags = {}

# NEW: track the sensor activation times as datetime objects
sensor1_activated_time = {reader: None for reader in readers}
sensor2_activated_time = {reader: None for reader in readers}

# ------------------------------------------------------------------------------
# MQTT Command Payloads
# ------------------------------------------------------------------------------
def gen_command(command, command_id, payload):
    return json.dumps({
        "command": command,
        "command_id": command_id,
        "payload": payload
    })

set_mode_cmd = gen_command("set_mode", "init123", {
    "type": "INVENTORY",
    "antennas": [1, 2, 3, 4],
    "transmitPower": 18,
    "antennaStopCondition": [],
    "tagMetaData": [],
    "rssiFilter": {}
})
start_cmd = gen_command("start", "start1", {})
stop_cmd  = gen_command("stop", "stop1", {})

def get_topic(topic_type, reader):
    return TOPIC_PREFIXES[topic_type] + reader

# ------------------------------------------------------------------------------
# MQTT Callbacks
# ------------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code: {rc}")
    for rd in readers:
        client.subscribe(get_topic("tag", rd))
        client.subscribe(get_topic("mgmt", rd))
        client.subscribe(get_topic("rsp", rd))

def on_message(client, userdata, msg):
    topic_parts = msg.topic.split("/")
    if len(topic_parts) < 3:
        print("Unexpected topic structure:", msg.topic)
        return
    reader = topic_parts[2]
    
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"Error decoding JSON from topic {msg.topic}: {e}")
        return

    # --------------------------------------------------------------------------
    # Process management events (GPI events)
    # --------------------------------------------------------------------------
    if msg.topic.startswith(TOPIC_PREFIXES["mgmt"]):
        if data.get("type") == "gpi":
            pin = data["data"].get("pin")
            sensor_state = data["data"].get("state")  # "HIGH" or "LOW"
            print(f"[{reader}] GPI event: Pin {pin} is {sensor_state}")

            custom_gate = (reader in ["RFGate1", "RFGate5"])

            # For standard gates (2,3,4):
            #   Sensor1 = Pin1 (HIGH->LOW)
            #   Sensor2 = Pin2 (HIGH->LOW)
            # For custom gates (1,5):
            #   Sensor1 = Pin2 (HIGH->LOW)
            #   Sensor2 = Pin1 (HIGH->LOW)

            # --- Forward/Loading Sequence ---
            if not custom_gate:
                # Gates 2,3,4
                if pin == 1:
                    # If going from HIGH->LOW => sensor1
                    if sensor_state == "LOW":
                        sensor1_activated_time[reader] = datetime.now()
                        print(f"[{reader}] Sensor 1 Activated (Pin1 LOW) at {sensor1_activated_time[reader]}")
                    if state[reader] == "IDLE":
                        if sensor_state == "LOW":
                            forward_triggered[reader] = True
                        elif sensor_state == "HIGH" and forward_triggered[reader]:
                            print(f"[{reader}] Starting tag read (Pin1 cycle).")
                            client.publish(get_topic("cmd", reader), start_cmd)
                            state[reader] = "READING"
                            forward_triggered[reader] = False
                            two_tags_captured[reader] = False

                elif pin == 2:
                    # If going from HIGH->LOW => sensor2
                    if sensor_state == "LOW":
                        sensor2_activated_time[reader] = datetime.now()
                        print(f"[{reader}] Sensor 2 Activated (Pin2 LOW) at {sensor2_activated_time[reader]}")
                    if state[reader] == "READING":
                        if sensor_state == "LOW":
                            loading_triggered[reader] = True
                        elif sensor_state == "HIGH" and loading_triggered[reader]:
                            # Finalize
                            print(f"[{reader}] Loading sequence completed.")
                            if not two_tags_captured[reader]:
                                client.publish(get_topic("cmd", reader), stop_cmd)
                            state[reader] = "IDLE"
                            loading_triggered[reader] = False
                            two_tags_captured[reader] = False
                            process_tags_and_log(reader)
            else:
                # Gates 1,5
                if pin == 2:
                    # Sensor1 = Pin2
                    if sensor_state == "LOW":
                        sensor1_activated_time[reader] = datetime.now()
                        print(f"[{reader}] (Custom) Sensor 1 Activated (Pin2 LOW) at {sensor1_activated_time[reader]}")
                    if state[reader] == "IDLE":
                        if sensor_state == "LOW":
                            forward_triggered[reader] = True
                        elif sensor_state == "HIGH" and forward_triggered[reader]:
                            print(f"[{reader}] (Custom) Starting tag read (Pin2 cycle).")
                            client.publish(get_topic("cmd", reader), start_cmd)
                            state[reader] = "READING"
                            forward_triggered[reader] = False
                            two_tags_captured[reader] = False

                elif pin == 1:
                    # Sensor2 = Pin1
                    if sensor_state == "LOW":
                        sensor2_activated_time[reader] = datetime.now()
                        print(f"[{reader}] (Custom) Sensor 2 Activated (Pin1 LOW) at {sensor2_activated_time[reader]}")
                    if state[reader] == "READING":
                        if sensor_state == "LOW":
                            loading_triggered[reader] = True
                        elif sensor_state == "HIGH" and loading_triggered[reader]:
                            print(f"[{reader}] (Custom) Loading sequence completed.")
                            if not two_tags_captured[reader]:
                                client.publish(get_topic("cmd", reader), stop_cmd)
                            state[reader] = "IDLE"
                            loading_triggered[reader] = False
                            two_tags_captured[reader] = False
                            process_tags_and_log(reader)

            # --- Reversing Sequence ---
            # Standard: reversal initiates on Pin2, completes on Pin1.
            # Custom: reversal initiates on Pin1, completes on Pin2.
            if not custom_gate:
                if state[reader] == "IDLE":
                    if pin == 2:
                        if sensor_state == "LOW" and not reversing_initiated[reader]:
                            reversing_initiated[reader] = True
                            print(f"[{reader}] Reversal initiated (Pin2 LOW).")
                        elif sensor_state == "HIGH" and reversing_initiated[reader]:
                            print(f"[{reader}] Reversal in progress. Next is Pin1.")
                if pin == 1:
                    if reversing_initiated[reader]:
                        if sensor_state == "LOW" and not reversing_completed[reader]:
                            reversing_completed[reader] = True
                            print(f"[{reader}] Reversal finishing (Pin1 LOW).")
                        elif sensor_state == "HIGH" and reversing_completed[reader]:
                            print(f"[{reader}] Forklift reversing sequence completed.")
                            reversing_initiated[reader] = False
                            reversing_completed[reader] = False
            else:
                # Custom gates 1,5
                if state[reader] == "IDLE":
                    if pin == 1:
                        if sensor_state == "LOW" and not reversing_initiated[reader]:
                            reversing_initiated[reader] = True
                            print(f"[{reader}] (Custom) Reversal initiated (Pin1 LOW).")
                        elif sensor_state == "HIGH" and reversing_initiated[reader]:
                            print(f"[{reader}] (Custom) Reversal in progress. Next is Pin2.")
                if pin == 2:
                    if reversing_initiated[reader]:
                        if sensor_state == "LOW" and not reversing_completed[reader]:
                            reversing_completed[reader] = True
                            print(f"[{reader}] (Custom) Reversal finishing (Pin2 LOW).")
                        elif sensor_state == "HIGH" and reversing_completed[reader]:
                            print(f"[{reader}] (Custom) Forklift reversing sequence completed.")
                            reversing_initiated[reader] = False
                            reversing_completed[reader] = False

    elif msg.topic.startswith(TOPIC_PREFIXES["tag"]):
        if state[reader] == "READING":
            handle_tag_data_message(data, reader)
    elif msg.topic.startswith(TOPIC_PREFIXES["rsp"]):
        print(f"Received RFID command response from {reader}: {data}")

def handle_tag_data_message(data, reader):
    if isinstance(data, list):
        tags = data
    elif "tags" in data:
        tags = data["tags"]
    else:
        tags = [data]
    
    for tag_event in tags:
        print(f"[{reader}] DEBUG: Raw tag event:", tag_event)
        tag_data = None
        if "tag_id" in tag_event:
            tag_data = str(tag_event["tag_id"])
        elif "id" in tag_event:
            tag_data = str(tag_event["id"])
        elif "epc" in tag_event:
            tag_data = tag_event["epc"]
        elif "data" in tag_event:
            if isinstance(tag_event["data"], dict) and "idHex" in tag_event["data"]:
                tag_data = tag_event["data"]["idHex"]
            else:
                tag_data = str(tag_event["data"])
        elif "barcode" in tag_event:
            tag_data = tag_event["barcode"]
        
        if tag_data is None:
            print(f"[{reader}] DEBUG: No tag data found in this event.")
            continue
        
        tag_data = tag_data.strip()
        print(f"[{reader}] DEBUG: Extracted tag data:", tag_data)
        
        if not tag_data.startswith("00353"):
            print(f"[{reader}] DEBUG: Tag '{tag_data}' does not start with '76543'; ignoring.")
            continue

        if tag_data in global_scanned_tags:
            prev_gate, prev_time = global_scanned_tags[tag_data]
            if prev_gate != reader:
                print(f"[{reader}] number {tag_data} already scanned on {prev_gate} at {prev_time}. Ignoring.")
                continue

        if len(collected_tags[reader]) >= 2:
            print(f"[{reader}] Already have 2 numbers in this session; ignoring extra number {tag_data}.")
            continue

        if tag_data in logged_tags[reader]:
            print(f"[{reader}] DEBUG: Tag '{tag_data}' was already scanned previously on this gate; ignoring duplicate.")
            continue

        if tag_data in collected_tags[reader]:
            print(f"[{reader}] DEBUG: Tag '{tag_data}' is already collected in this session; ignoring duplicate.")
            continue

        antenna = None
        user = None
        if "data" in tag_event and isinstance(tag_event["data"], dict):
            antenna = tag_event["data"].get("antenna")
            user = tag_event["data"].get("USER")
        
        rssi = None
        if "data" in tag_event and isinstance(tag_event["data"], dict):
            if "peakRssi" in tag_event["data"]:
                rssi = float(tag_event["data"]["peakRssi"])
            elif "rssi" in tag_event["data"]:
                rssi = float(tag_event["data"]["rssi"])
        if rssi is None:
            if "peakRssi" in tag_event:
                rssi = float(tag_event["peakRssi"])
            elif "peak_rssi" in tag_event:
                rssi = float(tag_event["peak_rssi"])
            elif "peakRSSI" in tag_event:
                rssi = float(tag_event["peakRSSI"])
            elif "rssi" in tag_event:
                rssi = float(tag_event["rssi"])
        print(f"[{reader}] DEBUG: Extracted RSSI:", rssi)
        
        if rssi is not None and rssi > -70:
            print(f"[{reader}] DEBUG: Tag {tag_data} has RSSI {rssi} > -70, ignoring.")
            continue
        
        collected_tags[reader].add(tag_data)
        tag_rssi_map[reader][tag_data] = rssi
        tag_antenna_map[reader][tag_data] = antenna
        tag_user_map[reader][tag_data] = user
        print(f"[{reader}] Collected tag {tag_data} with RSSI {rssi}, Antenna {antenna}, User {user}")

        if tag_data not in global_scanned_tags:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            global_scanned_tags[tag_data] = (reader, now_str)

        if len(collected_tags[reader]) == 2 and not two_tags_captured[reader]:
            print(f"[{reader}] Reached 2 unique numbers. Stopping radio, but waiting for final sensor to finish forward sequence.")
            client.publish(get_topic("cmd", reader), stop_cmd)
            two_tags_captured[reader] = True

def process_tags_and_log(reader):
    """
    This finalizes the read, logging each tag along with sensor times and time difference.
    """
    count = len(collected_tags[reader])
    if count == 0:
        print(f"[{reader}] No valid tags collected this session.")
        # Clear states
        sensor1_activated_time[reader] = None
        sensor2_activated_time[reader] = None
        return

    # Single or Double
    if count == 1:
        stack_type = "Single"
    else:
        stack_type = "Double"

    # The final read time (for the "Timestamp" column)
    final_dt = datetime.now()
    final_time_str = final_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Convert sensor times to strings
    sensor1_str = ""
    sensor2_str = ""
    time_diff_str = ""

    # If we have a sensor1 time, let's store it
    if sensor1_activated_time[reader] is not None:
        sensor1_str = sensor1_activated_time[reader].strftime("%Y-%m-%d %H:%M:%S")

    # If we have a sensor2 time, let's store it
    if sensor2_activated_time[reader] is not None:
        sensor2_str = sensor2_activated_time[reader].strftime("%Y-%m-%d %H:%M:%S")

    # "Time Difference" is final read time - sensor1 time in seconds
    if sensor1_activated_time[reader] is not None:
        diff_seconds = (final_dt - sensor1_activated_time[reader]).total_seconds()
        time_diff_str = f"{diff_seconds:.2f}"

    for tag_data in collected_tags[reader]:
        rssi = tag_rssi_map[reader].get(tag_data, "N/A")
        antenna = tag_antenna_map[reader].get(tag_data, "N/A")
        user = tag_user_map[reader].get(tag_data, "N/A")
        ws.append([
            final_time_str,
            stack_type,
            reader,
            tag_data,
            rssi,
            antenna,
            user,
            sensor1_str,    # Sensor 1 Activated
            sensor2_str,    # Sensor 2 Activated
            time_diff_str   # Time difference in seconds
        ])
        print(f"[{reader}] Logging event -> Time={final_time_str}, Stack={stack_type}, "
              f"Tag={tag_data}, RSSI={rssi}, Antenna={antenna}, User={user}, "
              f"Sensor1={sensor1_str}, Sensor2={sensor2_str}, Diff={time_diff_str}")
        logged_tags[reader].add(tag_data)

    # Save workbook
    wb.save(EXCEL_FILE)

    # Clear session-specific containers
    collected_tags[reader].clear()
    tag_rssi_map[reader].clear()
    tag_antenna_map[reader].clear()
    tag_user_map[reader].clear()

    # Clear sensor times
    sensor1_activated_time[reader] = None
    sensor2_activated_time[reader] = None

def listen_for_reset():
    while True:
        try:
            cmd = input().strip().lower()
        except EOFError:
            break
        if cmd == "reset":
            print("Manual reset triggered!")
            for r in readers:
                if state[r] == "READING":
                    client.publish(get_topic("cmd", r), stop_cmd)
                state[r] = "IDLE"
                forward_triggered[r] = False
                loading_triggered[r] = False
                reversing_initiated[r] = False
                reversing_completed[r] = False
                two_tags_captured[r] = False
                collected_tags[r].clear()
                tag_rssi_map[r].clear()
                tag_antenna_map[r].clear()
                tag_user_map[r].clear()
                sensor1_activated_time[r] = None
                sensor2_activated_time[r] = None
            print("State machines reset to IDLE for all readers.")

# ------------------------------------------------------------------------------
# MQTT Broker Configuration and Client Setup
# ------------------------------------------------------------------------------
BROKER = "BrokerIp address "
PORT = 1883
KEEPALIVE = 60

client = mqtt.Client(protocol=mqtt.MQTTv311)
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, KEEPALIVE)
client.loop_start()

for r in readers:
    client.publish(get_topic("cmd", r), set_mode_cmd)
    print(f"Sent set_mode command to {r} to configure reader to Inventory mode with antennas 1-4 at transmit power 18.")

reset_thread = threading.Thread(target=listen_for_reset, daemon=True)
reset_thread.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Shutting down...")
    client.loop_stop()
    client.disconnect()
    wb.save(EXCEL_FILE)
