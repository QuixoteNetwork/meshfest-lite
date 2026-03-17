![meshfest-lite-v1](https://github.com/user-attachments/assets/47a2aec6-0193-45ea-89e2-839fcbd40c36)
# MesHFest (Meshtastic-to-HF communication bridge)
## Index

[Application Summary](#application-summary) • [Architecture Diagram](#-architecture-diagram-hf--mesh-hybrid-model) • [Syntax & Examples](#syntax--examples) • [Example Configurations](#-example-configurations) • [Installation & Execution](#installation-and-execution-guide)

---

## **Application Summary**

MesHFest-lite is a lightweight & simple communication bridge designed to interconnect Meshtastic networks with HF (Designed especially for CB use) digital modes such as VARA HF (and still not JS8Call), enabling seamless message forwarding between radio and mesh infrastructures.

The application acts as an intelligent gateway that can relay, format, acknowledge, and route messages between different technologies, allowing stations operating on HF to communicate with Meshtastic nodes and vice versa.

MesHFest-lite is a simplified version (one file) designed to run as a service or as a simple bridge/chat, making it ideal for unattended stations, portable deployments, or minimal setups where stability and low resource usage are key.

Key features include:
- Bidirectional message bridging (Meshtastic <-> HF)
- Automatic forwarding and acknowledgment handling
- Callsign-aware routing logic
- Lightweight and service-friendly architecture
- Designed for experimentation, emergency comms, and hybrid RF networks
- Send and receive files from station to station (not to Meshtastic).
- BBS for download files from another MesHFest Station (not for Meshtastic)

MesHFest enables the creation of hybrid communication ecosystems where Meshtastic and HF digital radio coexist and complement each other.

If you like this work:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/M4M81CV1EX)

---

## 🔧 Installation and Execution Guide

### Installation
Windows / Linux / MacOS:

1- Clone Repo:
  ```
git clone https://github.com/QuixoteNetwork/meshfest-lite.git
cd meshfest-lite
  ```
2- (Recommended) Create a virtual environment:

  Linux / MacOS:
```
python3 -m venv venv
source venv/bin/activate   # Linux / Raspberry Pi
  ```
  Windows (CMD or PowerShell):
  ```
python -m venv venv
venv\Scripts\activate
  ```
  If PowerShell blocks execution, run: ```Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser```

3- Install Python Dependencies:
  ```
  pip install -r requirements.txt
  ```

4. Configure the system

  Edit your configuration file config.yaml or CLI parameters depending on your setup.
  ```
call: "30QXT1"

vara:
  host: "127.0.0.1"
  port: 8100

mesh:
  host: "192.168.1.25:4403"
  channel_name: Familia
  ```

### Execution

5. Run MeshFest Lite

  Basic execution: `python meshfest-lite.py` or using CLI parameters: `python meshfest-lite.py --call 30QXT1 --host 127.0.0.1 --port 8100`


### ⚙️ Optional: Run as a service (Linux / Raspberry Pi)

Create a systemd service: `sudo nano /etc/systemd/system/meshfest.service`

Service file:
```
[Unit]
Description=MeshFest Lite
After=network.target

[Service]
ExecStart=/path/to/venv/bin/python /path/to/meshfest-lite.py
WorkingDirectory=/path/to/meshfest-lite
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

Enable and start:
```
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable meshfest
sudo systemctl start meshfest
```

View logs: `journalctl -u meshfest -f`

---

## 🧠 Architecture Diagram (HF ↔ Mesh Hybrid Model)

```
           ~~~~~~~~~~~~~ HF RF LINK ~~~~~~~~~~~~~
             (AX.25 + QXT1 ACK Protocol Layer)
               │                          │
         ┌─────▼─────┐              ┌─────▼─────┐
         │  VARA HF  │              │  VARA HF  │
         │  Modem A  │              │  Modem B  │
         └─────┬─────┘              └─────┬─────┘
               │                          │
           KISS TCP                     KISS TCP
               │                          │
  ┌────────────▼─────────┐          ┌─────▼────────────────┐
  │    MeshFest-Lite     │          │    MeshFest-Lite     │
  │  HF ↔ Mesh Router A  │          │  HF ↔ Mesh Router B  │
  └────────────┬─────────┘          └──────────┬───────────┘
               │                               │
           Meshtastic                      Meshtastic
           Interface A                    Interface B
               │                               │
        ┌──────▼──────┐                  ┌──-──▼──────┐
        │ LoRa Mesh A │                  │ LoRa Mesh B│
        │ (Nodes)     │                  │ (Nodes)    │
        └─────────────┘                  └────────────┘
        
```

---

### 🔎 Logical Flows

```
Case 1 (Meshtastic to Meshtasic Bridge) -> send / receive messages:
Meshtastic <---> MesHFest-lite <---> VARA HF ((( HF ))) VARA HF <---> MesHFest-lite <---> Meshtastic

Case 2 (MeshFest Station to Meshtastic) -> send / receive messages:
MeshFest-lite <---> VARA HF ((( HF ))) VARA HF <---> MeshFest-lite <---> Meshtastic

Case 3 (MesHFest Station to MesHFest Station) -> send / receive messages and files:
MesHFest-lite <---> VARA HF ((( HF ))) VARA HF <---> MeshFest-lite
```

---

### 📡 Transport Stack (Top → Bottom)

HF Backbone:
- VARA as modem (transport only)
- KISS TCP 
- AX.25 framing
- QXT1 application ACK (stop-and-wait, retries)

Gateway Layer:
- MeshFest-Lite routing engine
- Policy enforcement
- Relay tagging (`>DEST:`)

Access Layer:
- Meshtastic interface (Serial / TCP)
- Meshtastic Mesh


### Transport Model

MeshFest-Lite uses:

- VARA as **physical/modem layer only**
- KISS TCP for AX.25 framing
- QXT1 application-layer protocol:
  - `T_MSG`
  - Message ID
  - Sequence number
  - Stop-and-wait ACK handling
  - Retries

It does **NOT** rely on VARA's internal ARQ session management, this allow to use transceivers without CAT control, just using VOX.


---

### 📦 File Transfer Workflow (Custom Reliable Layer)

MeshFest-Lite file transfer uses:

- Fragmentation
- Message IDs
- Sequence numbers
- Custom ACK handling
- Retries

---

## 🧩 Advanced Usage / Network Design Notes

### Custom Reliability Layer

MeshFest-Lite implements its own:

- Stop-and-wait protocol
- Message tracking
- ACK validation
- Retry logic
- Delivery confirmation logs

This allows:

- Deterministic routing
- Policy-based forwarding
- Hybrid network bridging
- Fine-grained control over message flow

---

### Why Not Native VARA ARQ?

Using KISS + custom protocol allows:

- Full control of routing logic
- Embedded metadata
- Relay tagging (`>DEST:` format)
- Multi-hop style relaying
- Hybrid mesh/HF policy enforcement

It turns VARA into a **transparent transport layer**, not a session controller.

---

## Syntax & Examples

```
python meshfest-lite.py -h
usage: meshfest-lite.py [-h] [--config CONFIG] [--run-as-service] [--tick-hz TICK_HZ] [--call CALL] [--host HOST]
                        [--port PORT] [--axdst AXDST] [--mesh-serial MESH_SERIAL] [--mesh-host MESH_HOST]
                        [--mesh-dest-id MESH_DEST_ID] [--allow-from-vara-to-node ALLOW_FROM_VARA_TO_NODE]
                        [--allow-from-mesh-via-vara-to-node ALLOW_FROM_MESH_VIA_VARA_TO_NODE]
                        [--mesh-channel-index MESH_CHANNEL_INDEX] [--mesh-rx-channel MESH_RX_CHANNEL]
                        [--mesh-channel-name MESH_CHANNEL_NAME] [--mesh-channel-allow-src MESH_CHANNEL_ALLOW_SRC]
                        [--mesh-channel-allow-from MESH_CHANNEL_ALLOW_FROM] [--mesh-want-ack] [--bridge-mesh]
                        [--bridge-varato-mesh-prefix BRIDGE_VARATO_MESH_PREFIX]
                        [--bridge-meshto-vara-prefix BRIDGE_MESHTO_VARA_PREFIX]
                        [--bridge-mesh-to-vara BRIDGE_MESH_TO_VARA] [--monitor] [-v {0,1,2}]
                        [--log-mode {console,file,both}] [--log-file LOG_FILE] [--lang {es,en}] [--bbs [BBS]]

Interactive chat + file transfer over VARA HF (KISS/TCP) & Meshtastic Bridge.

options:
  -h, --help            show this help message and exit
  --config CONFIG       YAML config file path
  --run-as-service      Run as background service (no stdin/UI, only logs)
  --tick-hz TICK_HZ     Main tick rate for service loop (default 2 Hz)
  --call CALL           Your callsign, e.g. EA1ABC
  --host HOST           KISS TCP host (VARA), default 127.0.0.1
  --port PORT           KISS TCP port (VARA), default 8100
  --axdst AXDST         AX.25 destination field (cosmetic), default APVARA
  --mesh-serial MESH_SERIAL
                        Meshtastic serial device (COMx or /dev/ttyUSB0)
  --mesh-host MESH_HOST
                        Meshtastic IP[:PORT] (default 4403)
  --mesh-dest-id MESH_DEST_ID
                        DestinationId (e.g. !abcdef01) to send to a specific node
  --allow-from-vara-to-node ALLOW_FROM_VARA_TO_NODE
                        Comma-separated Meshtastic destination ShortNames allowed for relay (e.g. QXT3,QXT6). If
                        omitted, any destination is allowed.
  --allow-from-mesh-via-vara-to-node ALLOW_FROM_MESH_VIA_VARA_TO_NODE
                        Comma-separated ShortNames allowed as HF TX destinations when using '@DEST ...' (e.g.
                        QXT3,QXT6). If omitted, HF TX to any @DEST is allowed.
  --mesh-channel-index MESH_CHANNEL_INDEX
                        Meshtastic channel (index) forwarding all from VARA to this Meshtastic Channel
  --mesh-rx-channel MESH_RX_CHANNEL
                        Meshtastic channel NAME or INDEX to accept for forwarding Meshtastic to VARA. If omitted,
                        don't accept any channel.
  --mesh-channel-name MESH_CHANNEL_NAME
                        Meshtastic channel (name)
  --mesh-channel-allow-src MESH_CHANNEL_ALLOW_SRC
                        Comma-separated Meshtastic source ShortNames or ID allowed for relay from channel to VARA
                        (e.g. ABC6,!e2e5a876). If omitted, any source is allowed.
  --mesh-channel-allow-from MESH_CHANNEL_ALLOW_FROM
                        Comma-separated allowlist for VARA->Meshtastic channel relay. Accepts mesh node shortnames
                        (e.g. ABC6) or VARA stations/callsigns (e.g. 30XYZ0, EA1ABC-7).
  --mesh-want-ack       Request ACK when sending to a specific node (destinationId)
  --bridge-mesh         Enable Meshtastic <-> VARA bridge
  --bridge-varato-mesh-prefix BRIDGE_VARATO_MESH_PREFIX
                        Prefix for traffic from VARA to Mesh
  --bridge-meshto-vara-prefix BRIDGE_MESHTO_VARA_PREFIX
                        Prefix for traffic from Mesh to VARA
  --bridge-mesh-to-vara BRIDGE_MESH_TO_VARA
                        VARA destination for traffic coming from Mesh (ALL or CALL)
  --monitor             Monitor mode: show readable messages even if not addressed to me/ALL (no ACK)
  -v, --verbose {0,1,2}
                        Log level: 0=errors, 1=normal, 2=debug
  --log-mode {console,file,both}
                        Log destination: console, file, or both
  --log-file LOG_FILE   Log file path (if --log-mode includes file)
  --lang {es,en}        Language of messages: es (Spanish) | en (English)
  --bbs [BBS]           Enable simple BBS mode. Optional path to BBS folder. If omitted, uses ./BBS
```


To exit the program, type `exit` or press `Ctrl+C`.

### 1️⃣ Core HF / VARA Configuration

- Your station callsign: `--call [CALLSIGN] (required)`

  Example:
  ```bash
  --call EA1ABC
  ```

- KISS TCP host (usually VARA running locally).  `--host [IP]` . Default: `127.0.0.1`.

  Example:
  ```bash
  --host 127.0.0.1
  ```
 
- KISS TCP port used by VARA.  `--port [1234]` . Default: `8100`.
  
  Example:
  ```bash
  --port 8100
  ```

- AX.25 destination field (cosmetic only).  `--axdst [APP_NAME]` . Default: `APVARA`.
  
  Example:
  ```bash
  --axdst VARA-HF
  ```

---

### 2️⃣ Meshtastic Interface Configuration

- Serial device for Meshtastic. `--mesh-serial [COM]`
  
  Examples:
    Linux:
    ```bash
    --mesh-serial /dev/ttyUSB0
    ```
    Windows:
    ```bash
    --mesh-serial COM5
    ```

- Connect to Meshtastic via TCP.  `--mesh-host [IP:PORT]` . Default port: `4403`.

  Example:
  ```bash
  --mesh-host 192.168.1.25:4403
  ```

- Send directly to a specific node ID. `--mesh-dest-id [!aaaaaaa]` .
  
  Example:
  ```bash
  --mesh-dest-id !abcdef01
  ```

- Select channel by index. `--mesh-channel-index [1]`.
  
  Example:
  ```bash
  --mesh-channel-index 1
  ```

- Select channel by name. `--mesh-channel-name [ChannelName]`.

  Example:
  ```bash
  --mesh-channel-name "MediumFast"
  ```

- Request ACK when sending to a specific node. `--mesh-want-ack`.

  Example:
  ```bash
  --mesh-want-ack
  ```

---

### 3️⃣ Security & Policy Controls (Firewall)

- Restricts which Meshtastic shortnames can be used as relay destinations (HF → Mesh). `--mesh-allow-dest-shortname [MSH]`  . If omitted, any destination is allowed.

  Example:
  ```bash
  --mesh-allow-dest-shortname MSH3,MSH6
  ```

- Restricts which `@DEST` nodes can be transmitted over HF. `--hf-allow-tx-dest-shortname [MSH]`.
  
  Example:
  ```bash
  --hf-allow-tx-dest-shortname MSH4
  ```

- Restricts which nodes can send messages to HF from a Meshtastic channel.

  Example:
  ```
  --mesh-channel-allow-src ABC1
  ```
  
- Restricts which nodes can send messages to a Meshtastic channel from HF station/node.
  
  Example:
  ```
  --mesh-channel-allow-from XYZ,30ABC3
  ```
  
  - Practical Example:

    If running with: ```--hf-allow-tx-dest-shortname MSH4```

    Then If you write on CLI: ``` EA1ABC > @MSH3: test  ``` Will be blocked.

    But If you write on CLI:  ``` EA1ABC > @MSH4: test ``` Will be transmitted.

---

### 4️⃣ Bridge Configuration (VARA ↔ Meshtastic)


- Enable Meshtastic ↔ VARA bridging. `--bridge-mesh`.
  
  Example:
  ```bash
  --bridge-mesh
  ```

- VARA destination for traffic coming from Mesh.  `--bridge-mesh-to-vara [CALLSIGN]` . Default: `ALL`.

  Example:
  ```bash
  --bridge-mesh-to-vara EA1ABC
  ```

- Prefix for traffic from VARA to Mesh. `--bridge-varato-mesh-prefix`.

  Example:
  ```bash
  --bridge-varato-mesh-prefix "VARA HF"
  ```

- Prefix for traffic from Mesh to VARA. `--bridge-meshto-vara-prefix`.

  Example:
  ```bash
  --bridge-meshto-vara-prefix "MESHTASTIC"
  ```

---

### 5️⃣ Monitoring & Logging

- Monitor mode (shows readable traffic not addressed to you). `--monitor`.

  Example:
  ```bash
  --monitor
  ```

- Log level `-v / --verbose [num]`: `0` = errors  `1` = normal   `2` = debug  

  Example:
  ```bash
  -v 2
  ```

- Logging in a file, console or both. `--log-mode [OPTION]` Options: `console file both`

  Example:
  ```bash
  --log-mode both
  ```

- Log file path.  `--log-file [file.log]`. Default: `meshfest.log`. To use this flag you need activate file logging with `--log-mode file` or `--log-mode both`.
  
  Example:
  ```bash
  --log-file mylog.txt
  ```

---

### 6️⃣ Language

Interface language: `--lang [LANG}` . Default: `en`. Options: English `en` or Spanish `es`.

  Example:
  ```bash
  --lang es
  ```

### 7️⃣ BBS

Interface language: `--bbs {/folder/path}` . Default: `./BBS`. Options: empty for current folder or path for folder you want.

  Example:
  ```bash
  --bbs /home/meshfest-lite/BBS
  ```


---

## 🔧 Full Example

```bash
python meshfest-lite.py \
  --call EA1ABC \
  --host 127.0.0.1 \
  --port 8100 \
  --bridge-mesh \
  --mesh-host 192.168.1.25:4403 \
  --bridge-meshto-vara-prefix "MESHTASTIC" \
  --bridge-varato-mesh-prefix "VARA HF" \
  --mesh-want-ack \
  --bridge-mesh-to-vara EA9XYZ \
  --mesh-allow-dest-shortname MSH3,MSH6 \
  --hf-allow-tx-dest-shortname MSH4, MSH5 \
  --log-mode both \
  --log-file mylog.txt \
  --monitor \
  --verbose 2
  --lang es
  --bbs
```


---

# 🧪 Example Configurations

## Minimal HF Mode (MesHFest Station)
Ideal for communicating with other MeshFest stations and MeshFest Meshtastic bridges.

```bash
python meshfest-lite.py \
  --call EA1ABC \
  --host 127.0.0.1 \
  --port 8100
```

## Full Bridge with Security Policy (Inbound & Outbond Firewall)

```bash
python meshfest-lite.py \
  --call EA1ABC \
  --bridge-mesh \
  --mesh-host 192.168.1.25:4403 \
  --mesh-want-ack \
  --bridge-mesh-to-vara EA9XYZ \
  --mesh-allow-dest-shortname MSH3,MSH6 \
  --hf-allow-tx-dest-shortname MSH4 \
  --log-mode file \
  --log-file meshfest.log \
  --verbose 0
```

