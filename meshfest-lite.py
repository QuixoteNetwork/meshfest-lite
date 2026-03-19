#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# ┌──────────────────────────────────────────────────────────────┐
# │                         MeshFest-Lite                        │
# │        Meshtastic ↔ HF Lightweight Communication             │
# │                  HF Station & Bridge Engine                  │
# └──────────────────────────────────────────────────────────────┘
#
# Author:      Quixote Network
# Project:     MesHFest-Lite
# Version:     1.0
# License:     MIT
#
# Description:
#   MeshFest-Lite is a lightweight bridge designed to interconnect
#   Meshtastic mesh networks with HF digital modes such as VARA HF.
#
#   The application provides:
#     • Direct Message (DM) forwarding
#     • Intelligent RELAY formatting (@DEST → RELAY > DEST:)
#     • ACK handling (Stop-and-Wait logic)
#     • Optional destination filtering
#     • Anti-echo protection
#     • Clean logging and routing control
#
#   This software is intended for experimental and educational use
#   within permitted radio services. Ensure compliance with your
#   local telecommunications regulations before operation.
#
# ----------------------------------------------------------------
#   Quixote Network — Decentralized Communications Network
# ----------------------------------------------------------------
#


import argparse
import os
import sys
import socket
import struct
import threading
import time
import zlib
import re
import hashlib
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Callable
from collections import deque

# ---------------- KISS constants ----------------
FEND  = 0xC0
FESC  = 0xDB
TFEND = 0xDC
TFESC = 0xDD
# ---------------- AX.25 UI constants ----------------
AX25_UI  = 0x03
AX25_PID = 0xF0
# ---------------- App protocol ----------------
MAGIC = b"QXT1"  # 4 bytes
# Packet types:
T_MSG  = 1
T_ACK  = 2
T_FILE = 3
T_FEND = 4  # file end

# MAGIC(4) | TYPE(1) | FLAGS(1) | SRC(10) | DST(10) | MSGID(4) | SEQ(2) | TOT(2) | PAYLEN(2) | CRC32(4)
HDR_FMT = "!4sBB10s10sIHHHI" #QXT1 Protocol Binary Header Format
HDR_LEN = struct.calcsize(HDR_FMT) #QXT1 Protocol Binary Header Length

FLAG_BROADCAST = 0x01

# ---------------- Colours in text (no dependencies needed)
ANSI_RED = "\x1b[31m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_CYAN = "\x1b[36m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_RESET = "\x1b[0m"

# ---------------- Regular Expresion
AT_CALL_RE = re.compile(r"^@([A-Za-z0-9_!.-]{2,16})\s*[:,]?\s*(.*)$")

# ---------------- Tuneables
MAX_PAYLOAD = 250        # Bytes per file chun. Default: 180
MAX_RETRIES = 3          # default: 3

# --- Adaptive ACK timeout tuning ---
MIN_ACK_TIMEOUT = 25.0      # default 20 seg mínimo, aunque el mensaje sea pequeño
MAX_ACK_TIMEOUT = 60.0      # default: 60 seconds per try
BASE_ACK_TIMEOUT = 6.0      # margen base (turnaround/cola/ARQ)
ACK_MARGIN = 4.0            # margen extra fijo default 6.0
RTT_MULT = 2.0              # multiplicador el RTT (Round Trip Time) estimado para timeout
EWMA_ALPHA = 0.25           # suavizado (0.1-0.3 suele ir bien) EWMA = Exponential Weighted Moving Average
DEFAULT_RTT = 35.0          # Tiempo desde envio mensaje a ACK recibido (Round Trip Time)
EFFICIENCY = 0.5            # eficiencia útil HF factor (0–1) para bajar a “útil”
EST_BITS_PER_SEC = 88.0     # medido/configurado bitrate (bps)
ACK_DELAY_SEC = 0.20        # default: 0.20 (200 ms). Delay before send ACK



# ------------------ Interactive/ Language Messages UI ----------------
MESHFEST_HEADER = """

.....................................................................................
: '##::::'##:'########::'######::'##::::'##:'########:'########::'######::'########::
:: ###::'###: ##.....::'##... ##: ##:::: ##: ##.....:: ##.....::'##... ##:... ##..:::
:: ####'####: ##::::::: ##:::..:: ##:::: ##: ##::::::: ##::::::: ##:::..::::: ##:::::
:: ## ### ##: ######:::. ######:: #########: ######::: ######:::. ######::::: ##:::::
:: ##. #: ##: ##...:::::..... ##: ##.... ##: ##...:::: ##...:::::..... ##:::: ##:::::
:: ##:.:: ##: ##:::::::'##::: ##: ##:::: ##: ##::::::: ##:::::::'##::: ##:::: ##:::::
:: ##:::: ##: ########:. ######:: ##:::: ##: ##::::::: ########:. ######::::: ##:::::
::..:::::..::........:::......:::..:::::..::..::::::::........:::......::::::..::::::
-------------------------------------- LITE v 1.0 -----------------------------------

                                    by Quixote Network
                                           

""".strip()

# ------------ LANG TEXTS -------------
TEXTS = {
    "es": {
        "help_body": """
    Comandos:
      ALL: <mensaje>                 Enviar broadcast (sin ACK)
      CALLSIGN: <mensaje>            Enviar directo (con ACK)
      CALLSIGN > NODO: <mensaje>     Enviar directo a Nodo (con ACK)
      SEND <CALLSIGN> <ruta>         Enviar fichero
      WHOAMI                         Muestra tu callsign
      HELP                           Esta ayuda
      EXIT                           Salir

    Ejemplos:
      ALL: hola a todos
      EA4XYZ: mensaje de prueba
      SEND EA4XYZ C:\\temp\\foto.jpg
      EA4XYZ: BBS
      EA4XYZ: DOWNLOAD 1

-------------------------------------------------------------------------------------""",
        "service_starting": "[INFO] ✅ MeshFest-Lite iniciando el servicio...",
        "connected_vara": "[INFO] ✅ Conectado a VARA HF {host}:{port}",
        "vara_connect_error": "[ERR] ❌ No se pudo conectar a VARA HF vía KISS en {host}:{port}: {error}",
        "meshbridge_active": "[INFO] ✅ Puente Meshtastic ACTIVO: {mesh} --> {vara} a {to}",
        "meshbridge_active_ch": "[INFO] ✅ Puente Meshtastic ACTIVO: {vara} <-- {mesh} en ch={ch}",
        "mesh_connecting": "[INFO] ✅ Conectando a Meshtastic...",
        "mesh_ready": "[INFO] ✅ Meshtastic Listo",
        "retry_no_ack": "[RETRY] ⚠️ Sin ACK de {dst} tras {timeout:.0f}s (msgid={msgid} seq={seq} len={payload_len}) intento {attempt}/{max_retries}",
        "fail_no_ack": "[FAIL] ❌ Sin ACK tras {max_retries} reintentos: {dst} msgid={msgid} seq={seq}",
        "err_invalid_format": "[ERR] ❌ Formato inválido. Usa 'ALL: mensaje' o 'CALL: mensaje'",
        "ack_dm": "[ACK DM] ✅ Mensaje confirmado por {dst}",
        "fail_not_delivered": "[FAIL] ❌ No entregado a {dst}: {msg}",
        "err_send_file_all": "[ERR] ❌ Para enviar fichero debes usar un destinatario concreto (no ALL).",
        "err_file_not_found": "[ERR] ❌ No existe fichero: {path}",
        "warn_rx_file_stale": "[WARN] ⚠️ RX FILE stale: descartando {filename} msgid={msgid} (sin actividad)",
        "rx_file_start": "[RX FILE] Iniciando {filename} ({filesize} bytes) desde {src} msgid={msgid}",
        "warn_rx_file_end_unknown": "[WARN] ⚠️ RX FILE fin recibido pero total desconocido: {filename} msgid={msgid}. No guardo.",
        "warn_rx_file_incomplete": "[WARN] ⚠️ RX FILE incompleto: {filename} desde {src} msgid={msgid} recibido={received}/{expected_total} bytes={recv_bytes}/{filesize} faltan={preview}{more}. No guardo.",
        "info_mesh_dest_confirmed": "[INFO] ✅ Destino Meshtastic confirmado: {dest_input} ({dest_id})",
        "err_mesh_dest_id_invalid": "[ERR] ❌ No se pudo usar destinationId '{dest_id}'. Aborto.",
        "warn_mesh_not_ready": "[WARN] ⚠️ Meshtastic no confirmó readiness, continúo igualmente...",
        "warn_mesh_not_ready_retry": "[WARN] ⚠️ Meshtastic aún no listo ({error}) intento {attempt}/3",
        "err_bridge_mesh_missing_iface": "[ERR] ❌ Para --bridge-mesh debes indicar --mesh-serial o --mesh-host",
        "info_monitor_on": "[INFO] ✅ Monitor ON: Muestra mensajes aunque no vayan a mí/ALL (sin ACK)",
        "err_no_kiss_connection": "[ERR] ❌ No hay conexión KISS. Saliendo.",
        "warn_nodeid_not_found": "[ERR] ❌ NodeId para '{dest}' no encontrado en DB (iface.nodes). DM no enviado.",
        "warn_forward_incomplete": "[WARN] ⚠️ Forward incompleto desde {src}: {text}",
        "warn_forward_malformed": "[WARN] ⚠️ Reenvío malformado desde {src}: {text}",
        "info_mesh_forward_policy": "[INFO] ✅ Política FORWARD LoRa: Nodo/s Meshtastic permitido/s → {nodes}",
        "info_hf_output_policy": "[INFO] ✅ Política OUTPUT HF: Nodo/s Meshtastic permitido/s → {nodes}",
        "debug_log_open_ok": "[DEBUG] LOG abierto correctamente: {path}",
        "debug_tx_noack": "[DEBUG] [TX NOACK] {src} -> {dest}: {message}",
        "hf_tx_deny_dest": "[DENY] ❌ HF TX: destino '@{dest}' no permitido. (Permitidos: {allowed})",
        "ack_forward_received": "[ACK FWD] ✅ Relay {relay} recibió solicitud para {mesh_name} {dest}",
        "relay_no_delivery": "[FAIL] ❌ Sin entrega al Relay {relay} (para {mesh_name} {dest})",
        "debug_send_file": "[DEBUG] SEND to={dest} path={path}",
        "tx_file_start": "[TX FILE] Iniciando transferencia de {filename} ({size} bytes) -> {dest}",
        "tx_file_header_sent": "[TX FILE] Cabecera enviada {filename} ({size} bytes) -> {dest}",
        "tx_file_progress": "[TX FILE] ENVIADO ({pct}%) {filename} {sent}/{size} bytes",
        "tx_file_completed": "[TX FILE] Completado: {filename} en {mins}m {secs}s ({bps} bps)",
        "rx_monitor": "[RX MON] {src} -> {dest}: {text}",
        "tx_ack": "[TX ACK] -> {dest} (rx msgid={msgid} seq={seq})",
        "meshbridge_tx_error": "[ERR] ❌ MeshBridge TX {mesh_name} error: {error}",
        "bridge_deny_dest": "[DENY] ❌ {vara_name} -> {mesh_name} destino '{dest}' no permitido (permitidos: {allowed})",
        "no_station_ack": "Mensaje para {dst} NO entregado (sin ACK de la Estación)",
        "vara_mesh_channel_allow": "[INFO] ✅ VARA-> Canal MESH permitido desde Nodos/Estaciones={nodes}",
        "mesh_forward_channel": "[INFO] ✅ Reenviando mensajes desde Canal='{channel}' Indice de Canal={index}",
        "mesh_channel_src_deny": "[DENY] ❌ Canal MESH -> Origen VARA '{src}' ({src_id}) NO permitido",
        "vara_mesh_ch_src_deny": "[DENY] ❌ VARA -> Canal MESH origen NO permitido (Estacion={station}, Node={node})",
        "vara_mesh_dest_deny_unresolved": "[DENY] ❌ {vara} -> {mesh} Destino '{dest}' NO permitido (no se pudo resolver shortName; Permitidos: {allow})",
        "info_bbs_enabled": "[INFO] ✅ BBS habilitado en: {bbs_path}",
        

    },
    "en": {
        "help_body": """
    Commands:
      ALL: <message>                 Send broadcast (no ACK)
      CALLSIGN: <message>            Send direct message (with ACK)
      CALLSIGN > NODE: <message>     Send direct to Node (with ACK)
      SEND <CALLSIGN> <path>         Send file
      WHOAMI                         Show your callsign
      HELP                           Show this help
      EXIT                           Exit

    Examples:
      ALL: hello everyone
      EA4XYZ: test message
      SEND EA4XYZ C:\\temp\\photo.jpg
      EA4XYZ: BBS
      EA4XYZ: DOWNLOAD 1

-------------------------------------------------------------------------------------""",
        "service_starting": "[INFO] ✅ MeshFest-Lite Starting Service...",
        "connected_vara": "[INFO] ✅ Connected to VARA HF {host}:{port}",
        "vara_connect_error": "[ERR] ❌ Could not connect to VARA HF via KISS at {host}:{port}: {error}",
        "meshbridge_active": "[INFO] ✅ Meshtastic Bridge ACTIVE: {mesh} --> {vara} to {to}",
        "meshbridge_active_ch": "[INFO] ✅ Meshtastic Bridge ACTIVE: {vara} <-- {mesh} to ch={ch}",
        "mesh_connecting": "[INFO] ✅ Connecting to Meshtastic...",
        "mesh_ready": "[INFO] ✅ Meshtastic Ready",
        "retry_no_ack": "[RETRY] ⚠️ No ACK from {dst} after {timeout:.0f}s (msgid={msgid} seq={seq} len={payload_len}) attempt {attempt}/{max_retries}",
        "fail_no_ack": "[FAIL] ❌ No ACK after {max_retries} retries: {dst} msgid={msgid} seq={seq}",
        "err_invalid_format": "[ERR] ❌ Invalid format. Use 'ALL: message' or 'CALL: message'",
        "ack_dm": "[ACK DM] ✅ Message acknowledged by {dst}",
        "fail_not_delivered": "[FAIL] ❌ Not delivered to {dst}: {msg}",
        "err_send_file_all": "[ERR] ❌ To send a file you must use a specific recipient (not ALL).",
        "err_file_not_found": "[ERR] ❌ File not found: {path}",
        "warn_rx_file_stale": "[WARN] ⚠️RX FILE stale: discarding {filename} msgid={msgid} (no activity)",
        "rx_file_start": "[RX FILE] Starting {filename} ({filesize} bytes) from {src} msgid={msgid}",
        "warn_rx_file_end_unknown": "[WARN] ⚠️ RX FILE end received but total unknown: {filename} msgid={msgid}. Not saving.",
        "warn_rx_file_incomplete": "[WARN] ⚠️ RX FILE incomplete: {filename} from {src} msgid={msgid} received={received}/{expected_total} bytes={recv_bytes}/{filesize} missing={preview}{more}. Not saving.",
        "info_mesh_dest_confirmed": "[INFO] ✅ Meshtastic destination confirmed: {dest_input} ({dest_id})",
        "err_mesh_dest_id_invalid": "[ERR] ❌ Could not use destinationId '{dest_id}'. Aborting.",
        "warn_mesh_not_ready": "[WARN] ⚠️ Meshtastic did not confirm readiness, continuing anyway...",
        "warn_mesh_not_ready_retry": "[WARN] ⚠️ Meshtastic not ready yet ({error}) attempt {attempt}/3",
        "err_bridge_mesh_missing_iface": "[ERR] ❌ For --bridge-mesh you must specify --mesh-serial or --mesh-host",
        "info_monitor_on": "[INFO] ✅ Monitor ON: Display messages even if not addressed to me/ALL",
        "err_no_kiss_connection": "[ERR] ❌ No KISS connection available. Exiting.",
        "warn_nodeid_not_found": "[ERR] ❌ NodeId for '{dest}' not found in DB (iface.nodes). DM not sent.",
        "warn_forward_incomplete": "[WARN] ⚠ Incomplete forward from {src}: {text}",
        "warn_forward_malformed": "[WARN] ⚠️ Malformed forward from {src}: {text}",
        "info_mesh_forward_policy": "[INFO] ✅ LoRa FORWARD policy: allowed Meshtastic Node/s → {nodes}",
        "info_hf_output_policy": "[INFO] ✅ HF OUTPUT policy: allowed Meshtastic Node/s → {nodes}",
        "debug_log_open_ok": "[DEBUG] Log file opened successfully: {path}",
        "debug_tx_noack": "[DEBUG] [TX NOACK] {src} -> {dest}: {message}",
        "hf_tx_deny_dest": "[DENY] ❌ HF TX: destination '@{dest}' not allowed. (Allowed: {allowed})",
        "ack_forward_received": "[ACK FWD] ✅ Relay {relay} received request for {mesh_name} {dest}",
        "relay_no_delivery": "[FAIL] ❌ No delivery to Relay {relay} (for {mesh_name} {dest})",
        "debug_send_file": "[DEBUG] SEND to={dest} path={path}",
        "tx_file_start": "[TX FILE] Starting file transfer {filename} ({size} bytes) -> {dest}",
        "tx_file_header_sent": "[TX FILE] SENT Header {filename} ({size} bytes) -> {dest}",
        "tx_file_progress": "[TX FILE] SENT ({pct}%) {filename} {sent}/{size} bytes",
        "tx_file_completed": "[TX FILE] Completed: {filename} in {mins}m {secs}s ({bps} bps)",
        "rx_monitor": "[RX MON] {src} -> {dest}: {text}",
        "tx_ack": "[TX ACK] -> {dest} (rx msgid={msgid} seq={seq})",
        "meshbridge_tx_error": "[ERR] ❌ MeshBridge TX {mesh_name} error: {error}",
        "bridge_deny_dest": "[DENY] ❌ {vara_name} -> {mesh_name} destination '{dest}' not allowed (allowed: {allowed})",
        "no_station_ack": "Message to {dst} not delivered (NO Station ACK)",
        "vara_mesh_channel_allow": "[INFO] ✅ VARA->MESH Channel allowed Nodes/Stations={nodes}",
        "mesh_forward_channel": "[INFO] ✅ Forwarding Messages from Channel='{channel}' Channel Index={index}",
        "mesh_channel_src_deny": "[DENY] ❌ MESH Channel -> VARA Source '{src}' ({src_id}) NOT Allowed",
        "vara_mesh_ch_src_deny": "[DENY] ❌ VARA -> MESH Channel source NOT allowed (Station={station}, Node={node})",
        "vara_mesh_dest_deny_unresolved": "[DENY] ❌ {vara} -> {mesh} Destination '{dest}' NOT allowed (shortName could not be resolved; Allowed: {allow})",
        "info_bbs_enabled": "[INFO] ✅ BBS enabled at: {bbs_path}",
    },
}

# ARGS UTILS
def validate_args(args, ap):
    # --- call ---
    if not (args.call and str(args.call).strip()):
        ap.error("--call is required (either pass it in CLI or define it in --config)")

    # Normaliza callsign
    args.call = str(args.call).strip().upper()
    
    # Si es servicio, obligatorio log a fichero
    if getattr(args, "run_as_service", False):
        if args.log_mode == "console":
            args.log_mode = "file"
        if not args.log_file:
            ap.error("--log-file must be set for service mode")

    # --- mesh-host format ---
    if args.mesh_host:
        s = str(args.mesh_host).strip()
        if not s:
            ap.error("--mesh-host is empty")
        # allow HOST o HOST:PORT
        if ":" in s:
            host, port = s.rsplit(":", 1)
            if not host.strip():
                ap.error("--mesh-host invalid: missing host before ':'")
            try:
                p = int(port)
                if p < 1 or p > 65535:
                    raise ValueError()
            except Exception:
                ap.error("--mesh-host invalid port (must be 1..65535)")
        args.mesh_host = s

    # --- channel: not allow index and name at the same time ---
    if args.mesh_channel_index is not None and args.mesh_channel_name:
        ap.error("Choose Meshtastic channel by --mesh-channel-index OR --mesh-channel-name, not both")

    if args.mesh_channel_index is not None:
        if args.mesh_channel_index < 0 or args.mesh_channel_index > 30:
            ap.error("--mesh-channel-index out of range (expected 0..30)")

    if args.mesh_channel_name:
        args.mesh_channel_name = str(args.mesh_channel_name).strip()

    # --- mesh-dest-id basic format ---
    if args.mesh_dest_id:
        s = str(args.mesh_dest_id).strip()
        # in Meshtastic is "!abcdef01"
        if not (s.startswith("!") and len(s) >= 2):
            ap.error("--mesh-dest-id should look like '!abcdef01'")
        args.mesh_dest_id = s

    # --- bridge mesh ---
    if args.bridge_mesh:
        # --- mesh connection: necesitas serial o host ---
        if not (args.mesh_serial or args.mesh_host):
            ap.error("You must specify Meshtastic connection: --mesh-serial or --mesh-host (or set in --config)")

        if args.mesh_serial and args.mesh_host:
            ap.error("Choose only one Meshtastic connection method: either --mesh-serial OR --mesh-host, not both")
        # to VARA frome Meshtastic
        d = (args.bridge_mesh_to_vara or "").strip().upper()
        if not d:
            ap.error("--bridge-mesh-to-vara is empty")
        # Allowed: ALL or callsign-like (simple)
        if d != "ALL":
            # soft validation callsign AX.25 
            # Allow A-Z0-9 and optional -SSID
            if not re.fullmatch(r"[A-Z0-9]{3,10}(-\d{1,2})?", d):
                ap.error("--bridge-mesh-to-vara must be 'ALL' or a callsign like 'EA1ABC' or 'EA1ABC-7'")
        args.bridge_mesh_to_vara = d

        # prefixes: make sure are strings
        args.bridge_varato_mesh_prefix = str(args.bridge_varato_mesh_prefix or "")
        args.bridge_meshto_vara_prefix = str(args.bridge_meshto_vara_prefix or "")

    # --- allow lists: normalize to CSV no spaces and Uppercase ---
    args.allow_from_vara_to_node = norm_csv(args.allow_from_vara_to_node)
    args.allow_from_mesh_via_vara_to_node = norm_csv(args.allow_from_mesh_via_vara_to_node)
    args.mesh_channel_allow_src = norm_csv(getattr(args, "mesh_channel_allow_src", None))
    args.mesh_channel_allow_from = norm_csv(getattr(args, "mesh_channel_allow_from", None))
    
    # --- logging ---
    if args.verbose not in (0, 1, 2):
        ap.error("--verbose must be 0, 1, or 2")

    if args.log_mode not in ("console", "file", "both"):
        ap.error("--log-mode must be: console, file, or both")

    if args.log_mode in ("file", "both"):
        lf = str(args.log_file or "").strip()
        if not lf:
            ap.error("--log-file must be set when --log-mode is file/both")
        args.log_file = lf

    # --- lang ---
    if args.lang not in ("es", "en"):
        ap.error("--lang must be 'es' or 'en'")

    return args
    
# ------------- YAML UTILS -----------
try:
    import yaml
except Exception:
    yaml = None

def load_yaml(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("Missing 'pyyaml'. Install: pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cli_has(flag: str) -> bool:
    # detect if user pass it on CLI
    return flag in sys.argv


def as_csv(v):
    # (Comma-Separated Values)
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return ",".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()

def norm_csv(v, upper=True):
    """
    Accept string CSV or list/tupla YAML and return normalize CSV.
    Ej:
      " xyz6, !abc123  " -> "XYZ6,!ABC123"   (if upper=True)
      ["XYZ6", "!abc123"] -> "XYZ6,!ABC123"
    """
    if v is None:
        return None

    if isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v if str(x).strip()]
    else:
        items = [x.strip() for x in str(v).split(",") if x.strip()]

    if upper:
        items = [x.upper() for x in items]

    return ",".join(items) if items else None

def _parse_mesh_src_allow(s):
    """
    Return two sets:
      - ids (!xxxx)
      - shortnames (XYZ6)
    """
    if not s:
        return None, None

    ids = set()
    shorts = set()

    for item in str(s).split(","):
        v = item.strip()
        if not v:
            continue

        if v.startswith("!"):
            ids.add(v.lower())
        else:
            shorts.add(v.upper())

    return ids or None, shorts or None


_RE_NODE_ID = re.compile(r"^![0-9A-F]{8}$", re.IGNORECASE)

# Callsign-like:
# EA4P, EA1ABC, K1ABC, F4XYZ, 30QXT1, EA1ABC-7
_RE_STATION = re.compile(r"^(?:[A-Z0-9]{1,4}\d[A-Z0-9]{0,4})(?:-\d{1,2})?$", re.IGNORECASE)

# Meshtastic shortname: max 4 chars
_RE_SHORTNAME = re.compile(r"^[A-Z0-9]{1,4}$", re.IGNORECASE)

def classify_mesh_allow_token(token: str) -> str | None:
    """
    Classify token as:
      - 'node'
      - 'station'
      - None (empty)

    Rules:
      1) contains '-'              -> station
      2) !xxxxxxxx                 -> node
      3) callsign-like             -> station
      4) short alnum <= 4          -> node
      5) fallback                  -> station
    """
    u = (token or "").strip().upper()
    if not u:
        return None

    if "-" in u:
        return "station"

    if _RE_NODE_ID.fullmatch(u):
        return "node"

    if _RE_STATION.fullmatch(u):
        return "station"

    if _RE_SHORTNAME.fullmatch(u):
        return "node"

    return "station"


def _parse_mesh_channel_allow_from(v):
    """
    Return two sets:
      - nodes: Meshtastic shortnames / node ids
      - stations: VARA / HF stations / callsigns

    Examples:
      QXT3       -> node
      ABC1       -> node
      !e2e5a934  -> node
      EA4P       -> station
      EA1ABC     -> station
      EA1ABC-7   -> station
      30QXT1     -> station
    """
    if not v:
        return None, None

    if isinstance(v, (list, tuple, set)):
        items = [str(x).strip() for x in v if str(x).strip()]
    else:
        items = [x.strip() for x in str(v).split(",") if x.strip()]

    nodes = set()
    stations = set()

    for item in items:
        kind = classify_mesh_allow_token(item)
        u = item.strip().upper()

        if kind == "node":
            nodes.add(u)
        elif kind == "station":
            stations.add(u)

    return nodes or None, stations or None

def _extract_mesh_node_from_text(txt: str):
    """
    Extract shortname mesh from:
      [XYZ6] hola
      XYZ6: hola
    """
    if not txt:
        return None

    s = str(txt).strip()

    m = re.match(r"^\[\s*([A-Za-z0-9_-]{2,20})\s*\]", s)
    if m:
        return m.group(1).strip().upper()

    m = re.match(r"^\s*([A-Za-z0-9_-]{2,20})\s*:\s*", s)
    if m:
        return m.group(1).strip().upper()

    return None

def apply_config(args, cfg: dict):
    """
    Aplica cfg (YAML) a args, sin pisar flags pasados por CLI.
    Estructura YAML esperada:
      call, monitor, lang
      vara: {host, port, axdst}
      mesh: {serial, host, dest_id, channel_index, channel_name, want_ack }
      firewall: {allow_from_mesh_via_vara_to_node, allow_from_vara_to_node}
      bridge: {enable_mesh, vara_to_mesh_prefix, mesh_to_vara_prefix, mesh_to_vara_dest}
      logging: {verbose, mode, file}
    """
    # --- Top-level ---
    if not cli_has("--call"):
        v = cfg.get("call")
        if v:
            args.call = str(v).strip()

    if not cli_has("--monitor"):
        v = cfg.get("monitor")
        if v is not None:
            args.monitor = bool(v)
            
    if not cli_has("--run-as-service"):
        v = cfg.get("run_as_service")
        if v is not None:
            args.run_as_service = bool(v)

    if not cli_has("--tick-hz"):
        v = cfg.get("tick_hz")
        if v is not None:
            args.tick_hz = int(v)  
            
    # --- BBS ---        
    if not cli_has("--bbs"):
        v = cfg.get("bbs")
        if v is True:
            args.bbs = "BBS"
        elif isinstance(v, str) and v.strip():
            args.bbs = v.strip()
            
    # --- lang ---
    if not cli_has("--lang"):
        v = cfg.get("lang", None)
        if v is not None and str(v).strip():
            args.lang = str(v).strip().lower()
            
    # --- VARA ---
    vara = cfg.get("vara", {}) or {}
    if not cli_has("--host"):
        v = vara.get("host")
        if v:
            args.host = str(v).strip()
    if not cli_has("--port"):
        v = vara.get("port")
        if v is not None:
            args.port = int(v)
    if not cli_has("--axdst"):
        v = vara.get("axdst")
        if v:
            args.axdst = str(v).strip()

    # --- Mesh ---
    mesh = cfg.get("mesh", {}) or {}
    if not cli_has("--mesh-serial"):
        v = mesh.get("serial")
        if v:
            args.mesh_serial = str(v).strip()
    if not cli_has("--mesh-host"):
        v = mesh.get("host")
        if v:
            args.mesh_host = str(v).strip()
    if not cli_has("--mesh-dest-id"):
        v = mesh.get("dest_id")
        if v:
            args.mesh_dest_id = str(v).strip()

    if not cli_has("--mesh-channel-index"):
        v = mesh.get("channel_index")
        if v is not None:
            args.mesh_channel_index = int(v)
    if not cli_has("--mesh-channel-name"):
        v = mesh.get("channel_name")
        if v:
            args.mesh_channel_name = str(v).strip()

    if not cli_has("--mesh-want-ack"):
        v = mesh.get("want_ack")
        if v is not None:
            args.mesh_want_ack = bool(v)

    
    # --- Bridge ---
    bridge = cfg.get("bridge", {}) or {}
    if not cli_has("--bridge-mesh"):
        v = bridge.get("enable_mesh")
        if v is not None:
            args.bridge_mesh = bool(v)
            
    if not cli_has("--bridge-varato-mesh-prefix"):
        v = bridge.get("vara_to_mesh_prefix")
        if v is not None:
            args.bridge_varato_mesh_prefix = str(v)

    if not cli_has("--bridge-meshto-vara-prefix"):
        v = bridge.get("mesh_to_vara_prefix")
        if v is not None:
            args.bridge_meshto_vara_prefix = str(v)

    if not cli_has("--bridge-mesh-to-vara"):
        v = bridge.get("mesh_to_vara_dest")
        if v is not None:
            args.bridge_mesh_to_vara = str(v).strip()
            
    if not cli_has("--mesh-rx-channel"):
        v = bridge.get("mesh_rx_channel")
        if v is not None:
            args.mesh_rx_channel = str(v).strip()
            
        
    # --- Firewall ---
    firewall = cfg.get("firewall", {}) or {}
    if not cli_has("--allow-from-vara-to-node"):
        v = firewall.get("allow_from_vara_to_node")
        if v is not None:
            args.allow_from_vara_to_node = as_csv(v)
            
    if not cli_has("--allow-from-mesh-via-vara-to-node"):
        v = firewall.get("allow_from_mesh_via_vara_to_node")
        if v is not None:
            args.allow_from_mesh_via_vara_to_node = as_csv(v)
            
    if not cli_has("--mesh-channel-allow-src"):
        v = firewall.get("mesh_channel_allow_src")
        if v is not None:
            args.mesh_channel_allow_src = norm_csv(v)
            
    if not cli_has("--mesh-channel-allow-from"):
        v = firewall.get("mesh_channel_allow_from")
        if v is not None:
            args.mesh_channel_allow_from = norm_csv(v)
            
            
    # --- Logging ---
    logging_cfg = cfg.get("logging", {}) or {}
    if not cli_has("-v") and not cli_has("--verbose"):
        v = logging_cfg.get("verbose")
        if v is not None:
            args.verbose = int(v)

    if not cli_has("--log-mode"):
        v = logging_cfg.get("mode")
        if v:
            args.log_mode = str(v).strip()

    if not cli_has("--log-file"):
        v = logging_cfg.get("file")
        if v:
            args.log_file = str(v).strip()

    return args

# ---------------- UTILS ----------------
def norm_call(s: str) -> str:
    s = s.strip().upper()
    return (s if s else "NOCALL")[:10]

def pad10(s: str) -> bytes:
    return norm_call(s).encode("ascii", errors="ignore").ljust(10, b" ")

def unpad10(b: bytes) -> str:
    return b.decode("ascii", errors="ignore").strip()

def now() -> float:
    return time.time()
    
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(t: str) -> str:
    return ANSI_RE.sub("", t)

# ----------------------------------------------
# ---------------- KISS FRAMING ----------------
# ----------------------------------------------
def kiss_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out.extend([FESC, TFEND])
        elif b == FESC:
            out.extend([FESC, TFESC])
        else:
            out.append(b)
    return bytes(out)

def kiss_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == FESC and i + 1 < len(data):
            nxt = data[i + 1]
            if nxt == TFEND:
                out.append(FEND)
            elif nxt == TFESC:
                out.append(FESC)
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)

def kiss_wrap(ax25_frame: bytes, port: int = 0) -> bytes:
    cmd = ((port & 0x0F) << 4) | 0x00  # data frame
    payload = bytes([cmd]) + ax25_frame
    return bytes([FEND]) + kiss_escape(payload) + bytes([FEND])

def try_extract_kiss(buf: bytearray) -> Optional[bytes]:
    try:
        start = buf.index(FEND)
    except ValueError:
        buf.clear()
        return None
    if start > 0:
        del buf[:start]
    try:
        end = buf.index(FEND, 1)
    except ValueError:
        return None
    raw = bytes(buf[1:end])
    del buf[:end + 1]
    return raw

def kiss_parse(raw_between_fends: bytes) -> Tuple[int, int, bytes]:
    u = kiss_unescape(raw_between_fends)
    if not u:
        raise ValueError("Empty KISS frame")
    cmd = u[0]
    port = (cmd >> 4) & 0x0F
    command = cmd & 0x0F
    data = u[1:]
    return port, command, data

# ----------------------------------------------
# ------------- AX.25 BUILD/PARSE --------------
# ----------------------------------------------
def ax25_encode_addr(call: str, last: bool) -> bytes:
    call = call.strip().upper()
    ssid = 0
    if "-" in call:
        base, ss = call.split("-", 1)
        call = base
        try:
            ssid = int(ss)
        except ValueError:
            ssid = 0
    call = call.ljust(6)
    addr = bytes([(ord(c) << 1) & 0xFE for c in call[:6]])
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1)
    if last:
        ssid_byte |= 0x01
    return addr + bytes([ssid_byte])

def ax25_build_ui(dst: str, src: str, info: bytes, digis: Optional[list] = None) -> bytes:
    digis = digis or []
    addr_fields = []
    addr_fields.append(ax25_encode_addr(dst, last=False))
    addr_fields.append(ax25_encode_addr(src, last=(len(digis) == 0)))
    for i, d in enumerate(digis):
        addr_fields.append(ax25_encode_addr(d, last=(i == len(digis) - 1)))
    return b"".join(addr_fields) + bytes([AX25_UI, AX25_PID]) + info

def ax25_parse_ui(frame: bytes) -> Optional[Tuple[str, str, bytes]]:
    if len(frame) < 16:
        return None
    i = 0
    addrs = []
    while True:
        if i + 7 > len(frame):
            return None
        af = frame[i:i+7]
        i += 7
        addrs.append(af)
        if af[6] & 0x01:
            break
        if len(addrs) > 10:
            return None
    if i + 2 > len(frame):
        return None
    control = frame[i]
    pid = frame[i+1]
    i += 2
    if control != AX25_UI or pid != AX25_PID:
        return None

    def decode_addr(af: bytes) -> str:
        call = "".join(chr((b >> 1) & 0x7F) for b in af[:6]).strip()
        ssid = (af[6] >> 1) & 0x0F
        return f"{call}-{ssid}" if ssid else call

    dst = decode_addr(addrs[0])
    src = decode_addr(addrs[1]) if len(addrs) >= 2 else "NOCALL"
    info = frame[i:]
    return dst, src, info


# ----------------------------------------------
# --- APP PACK / UNPACK (SERIALIZE MESSAGE)-----
# ----------------------------------------------
def app_pack(mtype: int, flags: int, src: str, dst: str, msgid: int, seq: int, tot: int, payload: bytes) -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = struct.pack(
        HDR_FMT, MAGIC, mtype, flags,
        pad10(src), pad10(dst),
        msgid, seq & 0xFFFF, tot & 0xFFFF,
        len(payload) & 0xFFFF,
        crc)
    return header + payload

def app_unpack(data: bytes) -> Optional[Tuple[int, int, str, str, int, int, int, bytes]]:
    if len(data) < HDR_LEN:
        return None
    magic, mtype, flags, src10, dst10, msgid, seq, tot, paylen, crc = struct.unpack(HDR_FMT, data[:HDR_LEN])
    if magic != MAGIC:
        return None
    if len(data) < HDR_LEN + paylen:
        return None
    payload = data[HDR_LEN:HDR_LEN+paylen]
    if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
        return None
    return mtype, flags, unpad10(src10), unpad10(dst10), msgid, seq, tot, payload


# ----------------------------------------------
# Transport LAYER: KISS over TCP (VARA Communication) 
# ----------------------------------------------
class KissTCP:
    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.buf = bytearray()
        self.lock = threading.Lock()  # protect sendall

    def connect(self, app) -> bool:
        if self.sock:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, self.port))
            s.settimeout(0.2)
            self.sock = s
            app.log(app.var_text("connected_vara", host=self.host, port=self.port), level=1)

            return True
        except Exception as e:
            app.log(app.var_text("vara_connect_error", host=self.host, port=self.port, error=str(e)), level=0)
            self.sock = None
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def send_ax25(self, ax25_frame: bytes):
        if not self.sock:
            raise RuntimeError("KISS not connected")
        data = kiss_wrap(ax25_frame)
        with self.lock:
            self.sock.sendall(data)

    def recv_ax25(self) -> Optional[bytes]:
        if not self.sock:
            raise RuntimeError("KISS not connected")
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            chunk = b""
        except OSError:
            self.close()
            return None
        if chunk:
            self.buf.extend(chunk)
        raw = try_extract_kiss(self.buf)
        if raw is None:
            return None
        _, cmd, data = kiss_parse(raw)
        if cmd != 0x00:
            return None
        return data

# ----------------------------------------------
# ---------------- Application -----------------
# ----------------------------------------------
@dataclass
class IncomingFile:
    src: str
    filename: str
    total: int
    received: Dict[int, bytes]
    filesize: int
    last_update: float

# ----------------------------------------------
# APP LAYER: send/receive messages/files, receive frames(poll_one), keep RTT status
# ----------------------------------------------
class HubApp:
    def __init__(self, mycall: str, kiss_host: str, kiss_port: int, ax25_dst: str = "APVARA", verbose = 1, log_mode="console", log_file="meshfest.log", lang="en"):
        self.mycall = norm_call(mycall)
        self.kiss = KissTCP(kiss_host, kiss_port)
        self.ax25_dst = ax25_dst
        self._msgid = int(time.time()) & 0x7FFFFFFF
        
        self.rtt_ewma: Dict[str, float] = {}             # dst -> rtt medio
        self.send_times: Dict[Tuple[int, int], float] = {}  # (msgid,seq) -> t_send

        self.ack_events: Dict[Tuple[int, int], threading.Event] = {}
        self.ack_lock = threading.Lock()

        self.in_files: Dict[int, IncomingFile] = {}

        self.download_dir = os.path.join(os.getcwd(), "downloads")
        os.makedirs(self.download_dir, exist_ok=True)
        
        #bbs
        self.bbs_enabled = False
        self.bbs_dir = None

        # RX worker uses this to print without interleaving too badly
        self.print_lock = threading.Lock()
        
        # Meshtastic Bridge
        self.bridge = None  
        
        # Monitor /sniffer
        self.monitor = False
        
        # msgid -> shortname origen mesh
        self.pending_mesh_forwards: Dict[int, str] = {}
        
        # Logs
        self.verbose = verbose
        self.log_mode = log_mode
        self.log_file = log_file
        self._log_fh = None
        
        # Language option/flag
        self.lang = lang
        
        # Create / Read the file
        if self.log_mode in ("file", "both"):
            log_path = os.path.abspath(self.log_file)
            log_dir = os.path.dirname(log_path)
            if log_dir and not os.path.isdir(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            self._log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
            self.log(self.var_text("debug_log_open_ok", path=os.path.abspath(self.log_file)), level=2)
        
        # Default names
        self.vara_name = "VARA"
        self.mesh_name = "MESH"
    
    # BBS ---------------------------------------------------
    def is_bbs_message(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False

        u = t.upper()

        if u == "BBS":
            return True
        if re.match(r"^\s*DOWNLOAD\s+\d+\s*$", t, flags=re.IGNORECASE):
            return True
        if u.startswith("BBS INDEX"):
            return True
        if u == "BBS EMPTY":
            return True
        if u.startswith("BBS DOWNLOAD:"):
            return True
        if u.startswith("BBS ERROR:"):
            return True

        return False
        
    def bbs_send_file_to_station(self, dst: str, file_id: int, path: str, filename: str):
        try:
            self.log(f"[BBS TX] Starting file [{file_id}] {filename} -> {dst}", level=1)

            ok = self.send_file(dst, path)

            if ok:
                self.log(f"[BBS TX] File [{file_id}] {filename} sent to {dst}", level=1)
            else:
                self.log(f"[BBS ERR] File [{file_id}] {filename} not fully confirmed to {dst}", level=0)

        except Exception as e:
            self.log(f"[BBS ERR] send_file_to_station failed: {e}", level=0)
            
    def bbs_list_files(self):
        if not self.bbs_enabled or not self.bbs_dir:
            return []

        try:
            entries = []
            for name in sorted(os.listdir(self.bbs_dir)):
                full = os.path.join(self.bbs_dir, name)
                if os.path.isfile(full):
                    size = os.path.getsize(full)
                    entries.append({
                        "name": name,
                        "path": full,
                        "size": size,
                    })
            return entries
        except Exception as e:
            self.log(f"[ERR] BBS list error: {e}", level=0)
            return []

    def bbs_render_index(self) -> str:
        files = self.bbs_list_files()

        if not files:
            return "BBS EMPTY"

        lines = ["BBS INDEX"]
        for idx, item in enumerate(files, start=1):
            lines.append(f"[{idx}] {item['name']} ({item['size']} bytes)")
        return "\n".join(lines)
        
    def bbs_get_file_by_id(self, file_id: int):
        files = self.bbs_list_files()
        if file_id < 1 or file_id > len(files):
            return None
        return files[file_id - 1]
        
        
    def close(self):
        if self._log_fh:
            try:
                self._log_fh.close()
            finally:
                self._log_fh = None


    def var_text(self, key: str, **kwargs) -> str:
        # fallback: si falta clave en idioma elegido, intenta EN, si no, devuelve la key
        tmpl = TEXTS.get(self.lang, {}).get(key) or TEXTS["en"].get(key) or key
        try:
            return tmpl.format(**kwargs)
        except Exception:
            # si alguien olvida un placeholder, que no reviente el programa
            return tmpl


    def estimate_ack_timeout(self, dst: str, payload_len: int) -> float:
        # 1) velocidad efectiva en bytes/s
        bytes_per_sec = max(1.0, (EST_BITS_PER_SEC / 8.0) * EFFICIENCY)

        # 2) tiempo estimado por tamaño
        size_based = BASE_ACK_TIMEOUT + (payload_len / bytes_per_sec) + ACK_MARGIN

        # 3) RTT histórico o por defecto
        rtt = self.rtt_ewma.get(dst.upper(), DEFAULT_RTT)
        hist_based = rtt * RTT_MULT

        # 4) elegir el mayor (seguridad HF)
        t = max(size_based, hist_based)

        # 5) limitar
        if t < MIN_ACK_TIMEOUT:
            t = MIN_ACK_TIMEOUT
        elif t > MAX_ACK_TIMEOUT:
            t = MAX_ACK_TIMEOUT
        return t


    def log(self, s: str, level: int = 1):
        """
        level:
          0 = critic errors
          1 = normal (default)
          2 = verbose / debug
        """

        if self.verbose < level:
            return

        # Timestamp (año 2 cifras)
        now = datetime.now().strftime("[%d/%m/%y-%H:%M:%S]")

        # Sanitiza para que no te machaque el timestamp
        s = str(s).replace("\r", "").rstrip("\n")

        with self.print_lock:
            if s.startswith("[RX"):
                color = ANSI_GREEN
            elif s.startswith("[TX") or s.startswith("[ACK"):
                color = ANSI_YELLOW
            elif s.startswith("[RETRY") or s.startswith("[FAIL")  or s.startswith("[DENY") or s.startswith("[FWD FAIL"):
                color = ANSI_RED
            elif s.startswith("[ERR"):
                color = ANSI_MAGENTA #purple color
            elif s.startswith("[DEBUG"):
                color = ANSI_CYAN
            else:
                color = ANSI_CYAN

            #print(f"{ANSI_RESET}{now} {color}{s}{ANSI_RESET}", flush=True)
            line_plain = f"{now} {s}"
            line_console = f"{ANSI_RESET}{now} {color}{s}{ANSI_RESET}"

            # consola
            if self.log_mode in ("console", "both"):
                print(line_console, flush=True)

            # archivo (sin ANSI, siempre limpio)
            if self.log_mode in ("file", "both") and self._log_fh:
                self._log_fh.write(strip_ansi(line_plain) + "\n")


    def send_dm(self, to_call: str, msg: str, *, wait_ack: bool = True, retries: int = 0) -> bool:
        to_call = (to_call or "").strip().upper()
        if not to_call or not msg:
            return False

        msgid = self.next_msgid()
        payload = msg.encode("utf-8", errors="replace")
        pkt = app_pack(T_MSG, 0, self.mycall, to_call, msgid, 0, 0, payload)

        if not wait_ack:
            self.send_ui(pkt, dst_ax25=to_call)
            self.log(self.var_text("debug_tx_noack", src=self.mycall, dest=to_call, message=msg), level=2)
            return True

        return self._send_with_ack(
            pkt=pkt,
            dst=to_call,
            msgid=msgid,
            seq=0,
            payload_len=len(payload),
            dst_ax25=to_call,
            retries=retries,
        )

    
    def next_msgid(self) -> int:
        self._msgid = (self._msgid + 1) & 0x7FFFFFFF
        return self._msgid


    def send_ui(self, info: bytes, dst_ax25: Optional[str] = None):
        """
        Envía un frame AX.25 UI por KISS.
        dst_ax25 controla el DEST que vera VARA: SRC -> DEST
        """
        axdst = dst_ax25 or self.ax25_dst
        frame = ax25_build_ui(axdst, self.mycall, info)
        self.kiss.send_ax25(frame)


    def send_ack(self, to_call: str, msgid: int, seq: int):
        pkt = app_pack(T_ACK, 0, self.mycall, to_call, msgid, seq, 0, b"")
        self.send_ui(pkt, dst_ax25=to_call)


    def _send_with_ack(
        self,
        pkt: bytes,
        dst: str,
        msgid: int,
        seq: int,
        payload_len: int,
        dst_ax25: Optional[str] = None,
        src: Optional[str] = None,
        retries: Optional[int] = None,
        on_fail: Optional[Callable[[str, int, int, str], None]] = None,) -> bool:
        """
        Send VARA stop-and-wait with ACK.
        - If it fails call to on_fail(dst_u, msgid, seq, reason) .
        """
        dst_u = (dst or "").strip().upper()
        if not dst_u:
            return False

        # Use global MAX_RETRIES or attribute if dont parse other
        if retries is None:
            retries = globals().get("MAX_RETRIES", 1)

        key = (msgid, seq)

        ev = threading.Event()
        with self.ack_lock:
            self.ack_events[key] = ev

        try:
            attempts_total = max(1, retries + 1)

            for attempt_idx in range(attempts_total):
                attempt_no = attempt_idx + 1  # 1..attempts_total

                # MUY recomendable: limpiar el evento antes del envío
                try:
                    ev.clear()
                except Exception:
                    pass

                t_send = now()
                self.send_times[key] = t_send

                self.send_ui(pkt, dst_ax25=dst_ax25)

                timeout = self.estimate_ack_timeout(dst_u, payload_len)
                # Si es transferencia de fichero, aumentar timeout 
                ptype = pkt[4] if pkt and len(pkt) >= 5 else None
                if ptype in (T_FILE, T_FEND):
                    timeout *= 1.5 # aumento del timeout
                    self.log(f"[DEBUG] file timeout x2 applied: ptype={ptype} seq={seq} timeout={timeout:.1f}s", level=2)
                else:
                    self.log(f"[DEBUG] normal timeout: ptype={ptype} seq={seq} timeout={timeout:.1f}s", level=2)

                if ev.wait(timeout=timeout):
                    rtt = now() - self.send_times.get(key, t_send)
                    prev = self.rtt_ewma.get(dst_u)
                    if prev is None:
                        self.rtt_ewma[dst_u] = rtt
                    else:
                        alpha = globals().get("EWMA_ALPHA", 0.25)
                        self.rtt_ewma[dst_u] = (1.0 - alpha) * prev + alpha * rtt
                    return True
                    
                 # NO ACK
                if attempt_no < attempts_total:
                    # Aqui si hay retry, y anunciamos el PROXIMO intento:
                    self.log(self.var_text("retry_no_ack", dst=dst_u, timeout=timeout, msgid=msgid, seq=seq, payload_len=payload_len, 
                    attempt=attempt_no, max_retries=retries), level=1)
                    
                else:
                    # Ultimo intento agotado: esto NO es RETRY, es FAIL
                    self.log(self.var_text("fail_no_ack", max_retries=retries, dst=dst_u, msgid=msgid, seq=seq), level=0)
                    # If there source device (Meshtastic) send back a message to notify didnt deliver the message
                    if src != None:
                        origin = src.strip().upper()
                        if self.bridge and hasattr(self.bridge, "send_to_mesh_shortname") and origin:
                            notify = self.var_text("no_station_ack", dst=dst_u)
                            self.bridge.send_to_mesh_shortname(origin, notify)
                    if on_fail:
                        try:
                            on_fail(dst_u, msgid, seq, "NO_ACK")
                        except Exception as cb_err:
                            self.log(f"[WARN] on_fail callback error: {cb_err}", level=1)
                    return False

        finally:
            self.send_times.pop(key, None)
            with self.ack_lock:
                self.ack_events.pop(key, None)


    def send_text_line(self, line: str):
        """
        Send a line in text format:
          - "ALL: message"                      (broadcast without ACK)
          - "CALL: message"                     (directo with ACK)
          - "RELAY > @DEST: message"        (send by VARA to RELAY for forwarding to @DEST)

          - "@DEST mensaje"                     (auto convierte a RELAY > DEST usando self.ax25_dst como relay)
            DEST can be shortname (QXT3) or NodeId (!abcdef01)
            Allow: "@DEST: msg" / "@DEST, msg"
        """

        line = (line or "").strip()
        if not line:
            return

        # --------------------------------------------------
        # Fast mode to @DEST message  (general)
        # --------------------------------------------------
        m_at = AT_CALL_RE.match(line.lstrip())
        if m_at:
            mesh_dest = (m_at.group(1) or "").strip()
            rest = (m_at.group(2) or "").strip()
           
            if not mesh_dest or not rest:
                return
            
            allowed = getattr(self, "hf_allowed_tx_shortnames", None)
            dst_norm = mesh_dest.strip().upper().lstrip("@")

            if allowed is not None and dst_norm not in allowed:
                self.log(self.var_text("hf_tx_deny_dest", dest=dst_norm, allowed=",".join(sorted(allowed))), level=0)
                return
            
            relay_call = self.ax25_dst.strip().upper()
            if not relay_call:
                self.log(self.var_text("err_invalid_format"), level=0)
                return
                
            # Pretty Log: SRC > RELAY > DEST: msg
            # En este modo rapido el SRC es la estacion (self.mycall)
            self.log(f"[TX DM] {self.mycall} > {relay_call} > {mesh_dest}: {rest}", level=1)

            msgid = self.next_msgid()
            origin = None
            # Guardamos que este msgid viene de este nodo Mesh
            if hasattr(self, "bridge") and self.bridge:
                origin = getattr(self.bridge, "last_mesh_origin", None)
                if origin:
                    self.pending_mesh_forwards[msgid] = origin
            fwd_text = f">{mesh_dest}: {rest}"
            payload = fwd_text.encode("utf-8", errors="replace")
            pkt = app_pack(T_MSG, 0, self.mycall, relay_call, msgid, 0, 0, payload)
           
            ok = self._send_with_ack(
                pkt=pkt,
                dst=relay_call,          # <-- ACK lo da el RELAY
                msgid=msgid,
                seq=0,
                payload_len=len(payload),
                dst_ax25=relay_call,
                src=origin,
                on_fail=None)

            if ok:
                self.log(self.var_text("ack_forward_received", relay=relay_call, mesh_name=self.mesh_name, dest=mesh_dest), level=1)

            else:
                self.log(self.var_text("relay_no_delivery", relay=relay_call, mesh_name=self.mesh_name, dest=mesh_dest), level=0)

            return

        # --------------------------------------------------
        # FORMATO CLASICO
        # --------------------------------------------------
        if ":" not in line:
            self.log(self.var_text("err_invalid_format"), level=0)
            return

        left, msg = line.split(":", 1)
        left = left.strip()
        msg = msg.strip()
        if not msg:
            return

        # --------------------------------------------------
        # MODO RELAY EXPLICITO: "RELAY > MESHDEST: mensaje"
        # --------------------------------------------------
        if ">" in left:
            relay_part, mesh_part = left.split(">", 1)
            relay_call = relay_part.strip().upper()
            mesh_dest = mesh_part.strip()

            if not relay_call or not mesh_dest:
                self.log(self.var_text("err_invalid_format"), level=0)
                return        
            
            allowed = getattr(self, "hf_allowed_tx_shortnames", None)
            dst_norm = mesh_dest.strip().upper().lstrip("@")

            if allowed is not None and dst_norm not in allowed:
                self.log(self.var_text("hf_tx_deny_dest", dest=dst_norm, allowed=",".join(sorted(allowed))), level=0)
                return
            
            # ---------------------------
            # PRETTY LOG BONITO FOR RELAY
            # Sequence:
            #   [TX] <ULTIMO_HOP> > <RELAY> > <DEST>: <MSG>
            # If message come with tag:
            #   "[QXT6>30QXT1] message"
            # we use the last hop from tag
            # ---------------------------
            pretty_src = self.mycall
            pretty_msg = msg

            m_route = re.match(r"^\[([^\]]{2,120})\]\s*(.*)$", msg)
            if m_route:
                route = (m_route.group(1) or "").strip()
                pretty_msg = (m_route.group(2) or "").strip()

                hops = [h.strip().upper() for h in route.split(">") if h.strip()]
                if hops:
                    candidate = hops[-1]

                    # Si el tag termina en el relay_call (destino), NO lo uso como origen TX.
                    # En ese caso el origen TX real es esta estacion (self.mycall).
                    if candidate == relay_call:
                        pretty_src = self.mycall
                    else:
                        pretty_src = candidate

            self.log(f"[TX DM] {pretty_src} > {relay_call} > {mesh_dest}: {pretty_msg}", level=1)

            # Payload real: mantenemos msg TAL CUAL (incluye tag si venia)
            fwd_text = f">{mesh_dest}: {msg}"

            msgid = self.next_msgid()
            payload = fwd_text.encode("utf-8", errors="replace")
            pkt = app_pack(T_MSG, 0, self.mycall, relay_call, msgid, 0, 0, payload)

            ok = self._send_with_ack(
                pkt,
                relay_call,
                msgid,
                0,
                payload_len=len(payload),
                dst_ax25=relay_call,
                src=pretty_src)

            if ok:
                self.log(self.var_text("ack_forward_received", relay=relay_call, mesh_name=self.mesh_name, dest=mesh_dest), level=1)

            else:
               self.log(self.var_text("relay_no_delivery", relay=relay_call, mesh_name=self.mesh_name, dest=mesh_dest), level=0)
            return

        # --------------------------------------------------
        # MODO NORMAL
        # --------------------------------------------------
        to_call = left.upper()
        
        # --------------------------------------------------
        # HF TX policy (también para el caso "RELAY: @DEST msg")
        # Si el mensaje que vas a mandar por HF empieza por @DEST,
        # bloquea si DEST no está permitido por --hf-allow-tx-dest-shortname
        # --------------------------------------------------
        allowed = getattr(self, "hf_allowed_tx_shortnames", None)
        m_cmd = re.match(r"^\s*@([A-Za-z0-9_!.-]{2,16})\s*[:,]?\s*(.+)\s*$", msg or "")
        if m_cmd:
            cmd_dest = (m_cmd.group(1) or "").strip().upper().lstrip("@")
            if allowed is not None and cmd_dest not in allowed:
                self.log(self.var_text("hf_tx_deny_dest", dest=cmd_dest, allowed=",".join(sorted(allowed))), level=0)
                return

        if to_call == "ALL":
            msgid = self.next_msgid()
            pkt = app_pack(
                T_MSG,
                FLAG_BROADCAST,
                self.mycall,
                "ALL",
                msgid,
                0,
                0,
                msg.encode("utf-8", errors="replace")
            )
            self.send_ui(pkt, dst_ax25="ALL")
            self.log(f"[TX ALL] {self.mycall} -> ALL: {msg}", level=1)
            return

        # DM VARA to VARA
        self.log(f"[TX] {self.mycall} -> {to_call}: {msg}", level=1)

        msgid = self.next_msgid()
        payload = msg.encode("utf-8", errors="replace")
        pkt = app_pack(T_MSG, 0, self.mycall, to_call, msgid, 0, 0, payload)

        ok = self._send_with_ack(
            pkt,
            to_call,
            msgid,
            0,
            payload_len=len(payload),
            dst_ax25=to_call,
        )

        if ok:
            self.log(self.var_text("ack_dm", dst=to_call), level=1)
        else:
            self.log(self.var_text("fail_not_delivered", dst=to_call, msg=msg), level=0)


    def send_file(self, to_call: str, path: str) -> bool:
        to_call = to_call.strip().upper()

        # DEBUG: to see exactly what receive
        self.log(self.var_text("debug_send_file", dest=to_call, path=path), level=2)

        if to_call == "ALL":
            self.log(self.var_text("err_send_file_all"), level=0)
            return False

        if not os.path.isfile(path):
            self.log(self.var_text("err_file_not_found", path=path), level=0)
            return False

        filename = os.path.basename(path)
        with open(path, "rb") as f:
            data = f.read()

        filesize = len(data)
        t0 = time.time()
        msgid = self.next_msgid()

        self.log(
            self.var_text("tx_file_start", filename=filename, size=filesize, dest=to_call),
            level=1
        )

        # header: filename\0 + filesize(4)
        header_payload = filename.encode("utf-8", errors="replace") + b"\0" + struct.pack("!I", filesize)
        header_pkt = app_pack(T_FILE, 0, self.mycall, to_call, msgid, 0, 0, header_payload)

        if not self._send_with_ack(
            header_pkt,
            to_call,
            msgid,
            0,
            payload_len=len(header_payload),
            dst_ax25=to_call
        ):
            return False

        self.log(
            self.var_text("tx_file_header_sent", filename=filename, size=filesize, dest=to_call),
            level=1
        )

        chunks = [data[i:i + MAX_PAYLOAD] for i in range(0, len(data), MAX_PAYLOAD)]
        total = len(chunks)

        for idx, chunk in enumerate(chunks, start=1):
            pkt = app_pack(T_FILE, 0, self.mycall, to_call, msgid, idx, total, chunk)

            if not self._send_with_ack(
                pkt,
                to_call,
                msgid,
                idx,
                payload_len=len(chunk),
                dst_ax25=to_call
            ):
                return False

            pct = (idx / total) * 100.0 if total > 0 else 100.0
            sent_bytes = min(idx * MAX_PAYLOAD, filesize)

            self.log(
                self.var_text(
                    "tx_file_progress",
                    pct=f"{pct:.1f}",
                    filename=filename,
                    sent=sent_bytes,
                    size=filesize
                ),
                level=1
            )

        end_pkt = app_pack(T_FEND, 0, self.mycall, to_call, msgid, total + 1, total, b"")

        if not self._send_with_ack(
            end_pkt,
            to_call,
            msgid,
            total + 1,
            payload_len=0,
            dst_ax25=to_call
        ):
            return False

        elapsed = time.time() - t0
        mins = int(elapsed // 60)
        secs = elapsed - mins * 60
        bits_per_sec = (filesize * 8.0) / elapsed if elapsed > 0 else 0.0

        self.log(
            self.var_text(
                "tx_file_completed",
                filename=filename,
                mins=mins,
                secs=f"{secs:.1f}",
                bps=f"{bits_per_sec:.1f}"
            ),
            level=1
        )

        return True

    
    def _handle_fwd_deny(self, src: str, text: str):
        self.log(f"[FWD FAIL] {src}: {text}", level=0)
        try:
            parts = (text or "").split()
            if len(parts) < 3:
                return

            deny_msgid = int(parts[1])
            deny_dest = parts[2]
            reason = parts[3] if len(parts) >= 4 else "NOT_ALLOWED"

            # NO borrar aun
            origin = self.pending_mesh_forwards.get(deny_msgid)
            if not origin:
                return

            if self.bridge and hasattr(self.bridge, "send_to_mesh_shortname"):
                notify = f"Message to {deny_dest} not delivered ({reason})"
                ok = self.bridge.send_to_mesh_shortname(origin, notify)

                # Borrar SOLO si se notifico bien
                if ok:
                    self.pending_mesh_forwards.pop(deny_msgid, None)

        except Exception as e:
            self.log(f"[ERR] FWD_DENY handling: {e}", level=0)
    
    
    def bbs_send_index_to_station(self, dst: str):
        try:
            reply = self.bbs_render_index()
            self.send_dm(dst, reply, wait_ack=True)
            self.log(f"[TX BBS] Index sent to {dst}", level=1)
        except Exception as e:
            self.log(f"[ERR BBS] send_index_to_station failed: {e}", level=0)
           
           
    def poll_once(self):
        FILE_RX_STALE_SECS = 10 * 60  # 10 min sin actividad => descartar recepcion a medias (ajustable)

        try:
            stale = []
            tnow = now()
            for mid, inc in list(self.in_files.items()):
                if (tnow - inc.last_update) > FILE_RX_STALE_SECS:
                    stale.append((mid, inc))
            for mid, inc in stale:
                self.log(self.var_text("warn_rx_file_stale", filename=inc.filename, msgid=mid), level=1)
                self.in_files.pop(mid, None)
        except Exception:
            pass

        frame = self.kiss.recv_ax25()
        if not frame:
            return

        parsed = ax25_parse_ui(frame)
        if not parsed:
            return

        _, _, info = parsed

        app = app_unpack(info)
        if not app:
            return

        mtype, flags, src, dst, msgid, seq, tot, payload = app

        # Normalizacion para evitar mismatches por case/espacios
        src = (src or "").strip().upper()
        dst = (dst or "").strip().upper()
        my  = (self.mycall or "").strip().upper()

        # ACK recibido (despierta al emisor)
        if mtype == T_ACK:
            self.log(f"[RX ACK] src={src} dst={dst} msgid={msgid} seq={seq}", level=2)

            key = (msgid, seq)
            with self.ack_lock:
                ev = self.ack_events.get(key)
            if ev:
                ev.set()
            else:
                self.log(f"[RX ACK] orphan (no waiter) msgid={msgid} seq={seq}", level=1)
            return
            
        # Ver + procesar FWD_DENY aunque no vaya dirigido a mi
        if mtype == T_MSG:
            try:
                t = payload.decode("utf-8", errors="replace")
                if t.startswith("!FWD_DENY"):
                    self._handle_fwd_deny(src, t)
                    return  # importante: no sigue con el filtro/ack/bridge para este control
            except Exception:
                pass
        # --- MONITOR ON: si no va a mi/ALL, aun asi mostrar T_MSG legibles (sin ACK) ---
        # (usar 'my' normalizado, no self.mycall)
        if dst not in (my, "ALL"):
            if getattr(self, "monitor", False) and mtype == T_MSG:
                text = payload.decode("utf-8", errors="replace")
                # Monitor/sniffer RX from HF
                self.log(self.var_text("rx_monitor", src=src, dest=dst, text=text), level=1)
            return

        is_bcast = bool(flags & FLAG_BROADCAST) or (dst == "ALL")
        
        # For DM, reply with ACK (No for Broadcast to ALL)
        if not is_bcast:
            self.log(self.var_text("tx_ack", dest=src, msgid=msgid, seq=seq), level=1)
            # Delay before send ACK
            time.sleep(ACK_DELAY_SEC)
            self.send_ack(src, msgid, seq)

        # ---------------- Text Message ----------------
        if mtype == T_MSG:
            text = payload.decode("utf-8", errors="replace")
            tag = "ALL" if is_bcast else "DM"

            pretty_src = src
            pretty_msg = text
            
            # ---------------- BBS COMMANDS ----------------
            if self.bbs_enabled and not is_bcast:
                cmd = text.strip()

                # LISTADO BBS
                if cmd.upper() == "BBS":
                    self.log(f"[BBS RX] Request from {src}: BBS", level=1)

                    threading.Thread(
                        target=self.bbs_send_index_to_station,
                        args=(src,),
                        daemon=True).start()
                    return

                # DOWNLOAD <id> (sending requested file)
                m_dl = re.match(r"^\s*DOWNLOAD\s+(\d+)\s*$", cmd, flags=re.IGNORECASE)
                if m_dl:
                    file_id = int(m_dl.group(1))
                    item = self.bbs_get_file_by_id(file_id)

                    if not item:
                        self.send_dm(src, f"BBS ERROR: file id {file_id} not found", wait_ack=True)
                        self.log(f"[BBS] Invalid download id {file_id} from {src}", level=1)
                        return

                    self.log(f"[TX BBS] Queueing file [{file_id}] {item['name']} to {src}", level=1)

                    threading.Thread(
                        target=self.bbs_send_file_to_station,
                        args=(src, file_id, item["path"], item["name"]),
                        daemon=True).start()
                    return

            # ---------------------------------------------------------
            # Si es forwarding tipo >DEST: [ROUTE] mensaje
            # ---------------------------------------------------------
            if text.startswith(">") and ":" in text:
                body = text[1:]
                dest_part, msg_part = body.split(":", 1)
                msg_part = msg_part.strip()

                m_route = re.match(r"^\[([^\]]{2,120})\]\s*(.*)$", msg_part)
                if m_route:
                    route = m_route.group(1).strip()
                    real_msg = m_route.group(2).strip()

                    hops = [h.strip().upper() for h in route.split(">") if h.strip()]
                    ax_src = (src or "").strip().upper()      # quien lo entrego por VARA (ej: 30ABC1)
                    me = (self.mycall or "").strip().upper()  # esta estacion (ej: 30ABC3)

                    origin = hops[0] if hops else ax_src

                    # Segundo hop: idealmente el ultimo del tag (relay emisor),
                    # pero si coincide con "me" o con el destino, usamos ax_src (quien lo entrego)
                    relay_prev = None
                    if len(hops) >= 2:
                        candidate = hops[-1]
                        if candidate != me:
                            relay_prev = candidate

                    if not relay_prev:
                        relay_prev = ax_src or "?"

                    # Ruta final: ORIG > RELAY_PREV > ME
                    full_route = f"{origin} > {relay_prev} > {me}".strip()
                    pretty_src = full_route
                    pretty_msg = real_msg

            self.log(f"[RX {tag}] {pretty_src}: {pretty_msg}")
            
            # Cortar trafico BBS al bridge
            if self.is_bbs_message(text):
                self.log(f"[BBS] Local-only traffic from {src}: {text}", level=2)
                return

            
            if self.bridge:
                try:
                    self.bridge.on_vara_text(src=src, dst=dst, text=text, is_bcast=is_bcast, msgid=msgid)
                except Exception as e:
                    self.log(f"[ERR] Bridge on_vara_text: {e}", level=0)
            return

        # ---------------- Tramas de fichero ----------------
        if mtype == T_FILE:
            # seq=0: cabecera del fichero: filename\0 + filesize(4)
            if seq == 0:
                if b"\0" not in payload or len(payload) < 6:
                    self.log(f"[RX FILE] header corrupted {src}")
                    return

                fname, rest = payload.split(b"\0", 1)
                filename = fname.decode("utf-8", errors="replace") or "file.bin"

                if len(rest) < 4:
                    self.log(f"[RX FILE] header with no size de {src}")
                    return

                filesize = struct.unpack("!I", rest[:4])[0]
                inc = self.in_files.get(msgid)
                if inc:
                    # Header duplicado: no reiniciar la recepcion
                    inc.last_update = now()
                    self.log(f"[DEBUG] RX FILE duplicate header ignored: {filename} msgid={msgid}", level=2)
                    return

                self.in_files[msgid] = IncomingFile(
                    src=src,
                    filename=filename,
                    total=0,
                    received={},
                    filesize=filesize,
                    last_update=now()
                )
                self.log(self.var_text("rx_file_start", filename=filename, filesize=filesize, src=src, msgid=msgid), level=1)
                return

            # seq>=1: fragmentos de datos
            inc = self.in_files.get(msgid)
            if not inc:
                # si se perdio la cabecera, ignoramos
                return

            inc.total = tot
            inc.last_update = now()

            if seq in inc.received:
                self.log(f"[DEBUG] RX FILE duplicate chunk ignored: msgid={msgid} seq={seq}", level=2)
                return

            inc.received[seq] = payload

            if inc.total > 0:
                got = len(inc.received)
                pct = (got / inc.total) * 100.0
                recv_bytes = sum(len(b) for b in inc.received.values())

                self.log(f"[RX FILE] {inc.filename} {got}/{inc.total} ({pct:.1f}%)", level=1)
                self.log(f"[RX FILE] {inc.filename} {recv_bytes}/{inc.filesize} bytes ({pct:.1f}%)", level=1)

            return

        # ---------------- End Of File ----------------
        if mtype == T_FEND:
            inc = self.in_files.get(msgid)
            if not inc:
                return

            # Si no sabemos total aun (no llego ningun chunk con tot valido), usa el tot del FEND
            if inc.total == 0 and tot > 0:
                inc.total = tot

            expected_total = inc.total

            # Determinar que partes faltan (1..total)
            missing = []
            if expected_total > 0:
                for i in range(1, expected_total + 1):
                    if i not in inc.received:
                        missing.append(i)

            recv_bytes = sum(len(b) for b in inc.received.values())

            # Si faltan chunks, NO guardar (evita archivos corruptos)
            if expected_total == 0:
                self.log(self.var_text("warn_rx_file_end_unknown", filename=inc.filename, msgid=msgid), level=1)
                self.in_files.pop(msgid, None)
                return

            if missing:
                # muestra solo los primeros para no inundar
                preview = missing[:12]
                more = "" if len(missing) <= 12 else f" (+{len(missing)-12} más)"
                self.log(self.var_text(
                    "warn_rx_file_incomplete",
                    filename=inc.filename,
                    src=inc.src,
                    msgid=msgid,
                    received=len(inc.received),
                    expected_total=expected_total,
                    recv_bytes=recv_bytes,
                    filesize=inc.filesize,
                    preview=preview,
                    more=more
                ), level=1)

                # Limpieza para no dejar basura en memoria
                self.in_files.pop(msgid, None)
                return

            # Completo: ensamblar y guardar
            assembled = b"".join(inc.received.get(i, b"") for i in range(1, expected_total + 1))

            out_path = os.path.join(self.download_dir, inc.filename)
            with open(out_path, "wb") as f:
                f.write(assembled)

            self.log(
                f"[RX FILE] Saved: {out_path} "
                f"({len(assembled)}/{inc.filesize} bytes) from {inc.src}",
                level=1
            )

            # Limpieza
            self.in_files.pop(msgid, None)
            return

        return


def input_thread(app: HubApp, stop_evt: threading.Event):

    while not stop_evt.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            stop_evt.set()
            break

        if not line:
            continue
        u = line.upper()

        if u in ("QUIT", "EXIT"):
            stop_evt.set()
            break
        if u == "HELP":
            app.log(MESHFEST_HEADER + app.var_text("help_body"), level=1)
            continue
        if u == "WHOAMI":
            app.log(f"MYCALL={app.mycall}")
            continue
        if u.startswith("SEND "):
            rest = line[5:].strip()  # todo lo que viene después de "SEND "
            # separar DEST del resto (solo 1 split)
            parts = rest.split(" ", 1)
            if len(parts) < 2:
                app.log('Use: SEND <CALLSIGN> "path"', level=0)
                continue

            to_call = parts[0].strip()
            path = parts[1].strip().strip('"')

            app.send_file(to_call, path)
            continue

        # Default: treat as chat line "ALL:" or "CALL:"
        app.send_text_line(line)

def rx_thread(app: HubApp, stop_evt: threading.Event):
    while not stop_evt.is_set():
        try:
            app.poll_once()
            time.sleep(0.01)
        except RuntimeError as e:
            app.log(f"[ERR RX] {e}", level=0)
            time.sleep(0.2)
        except Exception as e:
            app.log(f"[ERR RX] {e}", level=0)
            time.sleep(0.2)


##############################################
# ---------------- MESHTASTIC ----------------
##############################################
try:
    from pubsub import pub
except Exception:
    pub = None

try:
    from meshtastic.serial_interface import SerialInterface as MSerial
except Exception:
    MSerial = None

try:
    from meshtastic.tcp_interface import TCPInterface as MTCP
except Exception:
    MTCP = None


def create_tcp_interface(host: str, port: int):
    if MTCP is None:
        raise RuntimeError("TCPInterface missing. Install/update with: pip install -U meshtastic protobuf pypubsub")
    last_err = None
    for kwargs in (
        {"hostname": host, "portNumber": port},
        {"hostname": host, "portNum": port},
        {"hostname": host},
        {},
    ):
        try:
            return MTCP(**kwargs)
        except TypeError as e:
            last_err = e
        except Exception as e:
            last_err = e
    raise last_err


class Mesh:
    
    _excepthook_installed = False
    
    def __init__(self, serial_path: Optional[str], hostport: Optional[str], app: HubApp):
        if pub is None:
            raise RuntimeError("Missing 'pubsub'. Install: pip install -U pypubsub")
        if not serial_path and not hostport:
            raise RuntimeError("You need to put --mesh-serial or --mesh-host")

        self._serial_path = serial_path
        self._hostport = hostport
        self.iface = None
        self.app = app

        self._reconnect_needed = threading.Event()
        self._reconnect_lock = threading.Lock()

        self._install_thread_excepthook()
        self._install_pubsub_hooks()   

        self._recreate_iface()         # crea iface una sola vez
    
    def tick(self):
        """Llamar periódicamente desde tu loop principal."""
        # 1) reconexión solicitada por hook/evento
        if self._reconnect_needed.is_set():
            self._reconnect_needed.clear()
            self._recreate_iface()
            return

        # 2) watchdog: si el reader interno murió, fuerza reconexión
        if self._reader_dead():
            self._recreate_iface()

    def _reader_dead(self) -> bool:
        iface = self.iface
        if iface is None:
            return True

        # Intentamos detectar el thread reader en varias versiones
        candidates = []
        for attr in ("_reader", "_readerThread", "reader", "_rxThread", "_readThread"):
            t = getattr(iface, attr, None)
            if t is not None:
                candidates.append(t)

        for t in candidates:
            try:
                if hasattr(t, "is_alive") and not t.is_alive():
                    return True
            except Exception:
                pass

        return False
            
    def _install_pubsub_hooks(self):
        try:
            # Según versión, los topics pueden variar; por eso ponemos varios.
            for topic in (
                "meshtastic.connection.lost",
                "meshtastic.connection.closed",
                "meshtastic.connection.failed",
                "meshtastic.connection.error",
                "meshtastic.tcp.disconnected",
            ):
                pub.subscribe(self._on_mesh_conn_event, topic)
        except Exception:
            pass

    def _on_mesh_conn_event(self, interface=None, **kwargs):
        # Cualquier evento de caida: reconexion
        self._reconnect_needed.set()
        
    def _install_thread_excepthook(self):
        # Evita instalarlo varias veces si creo varios Mesh()
        if Mesh._excepthook_installed:
            return

        old_hook = getattr(threading, "excepthook", None)

        def hook(args):
            try:
                exc = args.exc_value

                is_10054 = isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054
                is_pipe  = isinstance(exc, (BrokenPipeError, ConnectionAbortedError))
                is_oserr = isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054, 10057)

                if is_10054 or is_pipe or is_oserr:
                    m = getattr(Mesh, "_last_instance", None)
                    if m is not None:
                        m._reconnect_needed.set()
                    return  # NO traceback
            except Exception:
                pass

            if old_hook:
                old_hook(args)

        threading.excepthook = hook
        Mesh._excepthook_installed = True
        Mesh._last_instance = self
    

    def shortname_from_id(self, node_id: str) -> Optional[str]:
        try:
            n = (self.iface.nodes or {}).get(str(node_id))
            if not n:
                return None
            u = (n.get("user") or {})
            sn = (u.get("shortName") or "").strip()
            return sn or None
        except Exception:
            return None


    def close(self):
        try:
            if hasattr(self.iface, "close"):
                self.iface.close()
        except Exception:
            pass


    def resolve_dest_id(self, dest_id: Optional[str], shortname: Optional[str]) -> Optional[str]:
        if dest_id:
            return dest_id
        if not shortname:
            return None
        sn = shortname.lower()
        try:
            for node_id, n in (self.iface.nodes or {}).items():
                info = (n.get("user") or {})
                if str(info.get("shortName", "")).lower() == sn:
                    return node_id
        except Exception:
            pass
        return None


    def resolve_channel_index(self, idx: Optional[int], name: Optional[str]) -> Optional[int]:
        if idx is not None:
            return idx
        if not name:
            return None

        wanted = name.strip().lower()

        try:
            # Forma moderna
            chs = getattr(self.iface.localNode, "channels", None)
            if not chs:
                return None

            for i, ch in enumerate(chs):
                try:
                    nm = (ch.settings.name or "").strip().lower()
                except Exception:
                    nm = ""

                if nm == wanted:
                    return i

        except Exception:
            pass

        return None


    def get_channels(self):
        """
        Devuelve una lista de dicts con 'name' (compat), o lista vacía si aún no está listo.
        Compatible con SerialInterface y TCPInterface.
        """
        iface = self.iface

        # 1) Si por casualidad existe getChannelList (otras versiones)
        if hasattr(iface, "getChannelList"):
            try:
                return iface.getChannelList() or []
            except Exception:
                pass

        # 2) Camino normal: usar el nodo local
        try:
            ln = getattr(iface, "localNode", None)
            if not ln:
                # algunas builds exponen getNode("^local")
                if hasattr(iface, "getNode"):
                    ln = iface.getNode("^local")
            if not ln:
                return []

            chs = getattr(ln, "channels", None)
            if not chs:
                return []

            # Normaliza a lista de dicts con name
            out = []
            for c in chs:
                if c is None:
                    continue
                # c puede ser dict o un objeto protobuf-like
                if isinstance(c, dict):
                    nm = (c.get("settings", {}).get("name") or c.get("name") or "").strip()
                    out.append({"name": nm, **c})
                else:
                    # intenta atributos comunes
                    nm = ""
                    try:
                        # en muchos casos: c.settings.name
                        nm = (getattr(getattr(c, "settings", None), "name", "") or "").strip()
                    except Exception:
                        nm = ""
                    out.append({"name": nm, "raw": c})
            return out
        except Exception:
            return []


    def _recreate_iface(self):
        if not self._reconnect_lock.acquire(blocking=False):
            return
        try:
            self.close()

            if self._serial_path:
                self.iface = MSerial(self._serial_path)
            else:
                host, port = self._hostport.split(":") if ":" in self._hostport else (self._hostport, "4403")
                self.iface = create_tcp_interface(host, int(port))

            # warm-up
            try:
                t0 = time.time()
                while time.time() - t0 < 5.0:
                    nodes = getattr(self.iface, "nodes", None) or {}
                    if nodes:
                        break
                    time.sleep(0.2)
            except Exception:
                pass

        finally:
            self._reconnect_lock.release()


##########################
#-------BRIDGE------------
##########################
class MeshBridge:
    """
    Puente opcional:
      - VARA RX (T_MSG)  -> Meshtastic sendText()
      - Meshtastic RX    -> VARA send (directo si @CALL ..., si no ALL)
    Anti-eco con prefijos:
      - Lo que entra desde Mesh y sale a VARA va prefijado con mesh_to_vara_prefix
      - Lo que entra desde VARA y sale a Mesh va prefijado con vara_to_mesh_prefix
      - Se ignoran mensajes que ya tengan el prefijo contrario para evitar bucles.
    """

    def __init__(
        self,
        app: "HubApp",
        mesh: Mesh,
        *,
        mesh_channel_index: Optional[int],
        mesh_channel_name: Optional[str],
        mesh_want_ack: bool,
        # en VARA, a donde enviamos lo que venga de Mesh (ALL o CALL)
        vara_out_to: str,
        vara_to_mesh_prefix: str = "[MESH] ",
        allow_from_vara_to_nodes = None,
        mesh_channel_allow_src=None,
        mesh_channel_allow_from=None):
            
        self.app = app
        self.mesh = mesh
        self.mesh_channel_index = 0 if mesh_channel_index is None else mesh_channel_index
        self.mesh_channel_name = (mesh_channel_name or "").strip() or None
        self.mesh_want_ack = mesh_want_ack

        self.vara_out_to = (vara_out_to or "ALL").strip().upper()
        self.vara_to_mesh_prefix = vara_to_mesh_prefix or ""
        
        self.echo_ttl = 180  # segundos
        self._seen = {}      # hash -> timestamp
        
        self.mesh_txt_loose_ttl = 25   # 15-30s va fino evita eco por tiempo mensaje otra malla
        self._mesh_seen_txt_loose = {} # key -> ts
        self.last_mesh_origin = None
        
        # Dedup Meshtastic RX (modo canal): por packet.id y por huella de texto
        self.mesh_rx_dedup_ttl = 60  # segundos, ajustable
        self._mesh_seen_pkt = {}     # pid(int) -> ts
        self._mesh_seen_txt = {}     # key(str) -> ts
        
        # Destinations Allowed
        self.allow_from_vara_to_nodes = allow_from_vara_to_nodes  # set[str] o None

        # Meshtastic suscription
        pub.subscribe(self._on_mesh_packet, "meshtastic.receive")
        
        # Allowed Nodes from Channel to VARA
        self.mesh_channel_allow_src = mesh_channel_allow_src
        self.mesh_channel_allow_src_ids, self.mesh_channel_allow_src_shortnames = _parse_mesh_src_allow(mesh_channel_allow_src)
        
        # Allowed Nodes from VARA to a Channel
        self.mesh_channel_allow_from = mesh_channel_allow_from
        self.mesh_channel_allow_from_nodes, self.mesh_channel_allow_from_stations = _parse_mesh_channel_allow_from(mesh_channel_allow_from)
        if self.mesh_channel_allow_from_nodes or self.mesh_channel_allow_from_stations:
            self.app.log(self.app.var_text("vara_mesh_channel_allow", nodes=self.mesh_channel_allow_from_nodes, stations=self.mesh_channel_allow_from_stations), level=1)
                
        # Send all to VARA from a Meshtastic Channel
        self.mesh_rx_channel_name = (getattr(self.app, "mesh_rx_channel", None) or "").strip()
        self.mesh_rx_channel_index = None

        if self.mesh_rx_channel_name:
            #self.mesh_rx_channel_index = self._resolve_channel_name_to_index(self.mesh_rx_channel_name)
            self.mesh_rx_channel_index = self.mesh.resolve_channel_index(None, self.mesh_rx_channel_name)
            self.app.log(self.app.var_text("mesh_forward_channel", channel=self.mesh_rx_channel_name, index=self.mesh_rx_channel_index), level=1)
            
        self._recent_vara_out = deque(maxlen=30)  # anti-loop: huellas de lo último que mandé a VARA
        
        self.app.log(self.app.var_text("meshbridge_active", mesh=self.app.mesh_name, vara=self.app.vara_name, to=self.vara_out_to), level=1)  
        self.app.log(self.app.var_text("meshbridge_active_ch", vara=self.app.vara_name, mesh=self.app.mesh_name, ch=self.mesh_channel_index), level=1)
        #self.app.log(f"{ANSI_CYAN}-------------------------------------------------------------------------------------{ANSI_RESET}")

    TAG_RE = re.compile(r"\[(?:mesh|vara)@[^]]+\]\s*", re.IGNORECASE)
    
    def _norm_loop_text(self, s: str) -> str:
        """
        Normaliza texto para detección de loop:
        - quita "MYCALL:" al inicio si existe
        - compacta espacios
        """
        s = (s or "").strip()
        my = (getattr(self.app, "mycall", None) or "").strip().upper()
        if my:
            s = re.sub(rf"^\s*{re.escape(my)}\s*:\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()
        return s
    
    def _was_recently_sent_to_vara(self, mesh_txt: str) -> bool:
        n = self._norm_loop_text(mesh_txt)
        return bool(n) and n in self._recent_vara_out

    def _remember_sent_to_vara(self, out_txt: str):
        n = self._norm_loop_text(out_txt)
        if n:
            self._recent_vara_out.append(n)
        
        
    def _packet_channel_index(self, packet):
        """
        Devuelve el channelIndex como int si lo puede obtener.
        En tu packet real aparece la clave 'channel' en la raíz.
        En algunas versiones también aparece 'channelIndex'.
        """
        if packet is None:
            return None

        # 1) Si ya viene como channelIndex explicito
        v = packet.get("channelIndex", None)
        if v is not None:
            try:
                return int(v)
            except Exception:
                pass

        # 2) Caso packet['channel'] suele ser el indice
        v = packet.get("channel", None)
        if v is not None:
            try:
                return int(v)
            except Exception:
                pass

        # 3) Fallback en decoded
        decoded = packet.get("decoded") or {}
        v = decoded.get("channelIndex", None)
        if v is not None:
            try:
                return int(v)
            except Exception:
                pass

        v = decoded.get("channel", None)
        if v is not None:
            try:
                return int(v)
            except Exception:
                pass

        return None
        

    def _channel_matches(self, packet):
        """
        Modo canal: solo matchea si tenemos mesh_rx_channel_index resuelto
        y el packet trae un channelIndex compatible.
        """
        if self.mesh_rx_channel_index is None:
            return False
        pkt_idx = self._packet_channel_index(packet)
        if pkt_idx is None:
            return False
        return pkt_idx == self.mesh_rx_channel_index
    
    
    def _mesh_seen_text_loose(self, ch_idx: int, clean_txt: str) -> bool:
        """
        Dedup flojo: mismo texto en el mismo canal en una ventana corta.
        """
        now = time.time()
        ttl = getattr(self, "mesh_txt_loose_ttl", 25)

        # Fast purge
        dead = [k for k, ts in self._mesh_seen_txt_loose.items() if (now - ts) > ttl]
        for k in dead:
            self._mesh_seen_txt_loose.pop(k, None)

        n = self._norm_loop_text(clean_txt).lower()
        if not n:
            return False

        k = f"ch={int(ch_idx)}|{n}"
        if k in self._mesh_seen_txt_loose:
            return True

        self._mesh_seen_txt_loose[k] = now
        return False
    
    
    def _on_mesh_packet_channel_to_vara(self, packet=None, interface=None, **kwargs):
        """
        Mesh -> VARA (modo canal)
        Todo lo que llegue al canal configurado (mesh_rx_channel_name/index) se manda por VARA a self.vara_out_to
        con formato: "[SHORTNAME] texto"
        """
        if packet is None:
            return

        try:
            # 0) Filtrar por canal (dict completo)
            if not self._channel_matches(packet):
                return
            # 1) Drop si el paquete viene de mi mismo (importante en modo canal tambien)
            try:
                ln = getattr(self.mesh.iface, "localNode", None)
                myid = str(getattr(ln, "nodeId", "") or "").strip() if ln else None
            except Exception:
                myid = None
            from_id = packet.get("fromId")
            if myid and from_id and str(from_id).strip() == myid:
                self.app.log("[DEBUG] DROP mesh loopback (from myself) [channel mode]", level=2)
                return
                
            # 2) Dedup fuerte por packet.id
            if self._mesh_seen_packet_id(packet):
                self.app.log(f"[DEBUG] DROP mesh duplicate (packet.id) [channel mode]", level=2)
                return
                
            # 2) Extraer texto
            decoded = packet.get("decoded") or {}
            txt = decoded.get("text")

            if not txt:
                return

            if not isinstance(txt, str):
                try:
                    txt = txt.decode("utf-8", errors="ignore")
                except Exception:
                    return

            txt = txt.strip()
            if not txt:
                return

            # 3) Anti-loop por prefijo contrario (si lo usas en VARA->Mesh)
            if self.vara_to_mesh_prefix and txt.startswith(self.vara_to_mesh_prefix):
                return

            # 4) Anti-loop fuerte: si es algo que yo acabo de mandar a VARA, no lo reenvíes
            if self._was_recently_sent_to_vara(txt):
                self.app.log(f"[DEBUG] DROP loopback (recent VARA out): {txt!r}", level=2)
                return

            # 5) Anti-loop extra: si viene "MYCALL: ..." al inicio, es reinyección -> fuera
            my = (getattr(self.app, "mycall", None) or "").strip().upper()
            if my and re.match(rf"^\s*{re.escape(my)}\s*:\s*", txt, flags=re.IGNORECASE):
                self.app.log(f"[DEBUG] DROP loopback (starts with mycall): {txt!r}", level=2)
                return

            # 6) Origen Mesh
            from_id = packet.get("fromId")
            src_id = str(from_id or "").strip().lower()

            # intenta resolver shortname real
            src_label = self.mesh.shortname_from_id(from_id) or str(from_id or "?")
            src_short = str(src_label or "").strip().upper()

            # normalización extra por si shortname_from_id devolviera algo tipo "30QXT6"
            # o con prefijos raros; si empieza por "!" no lo tratamos como shortname
            if src_short.startswith("!"):
                src_short_only = ""
            else:
                src_short_only = src_short

            # --- allowlist de emisores permitidos desde canal Meshtastic -> VARA ---
            allow_ids = getattr(self, "mesh_channel_allow_src_ids", None)
            allow_shorts = getattr(self, "mesh_channel_allow_src_shortnames", None)

            self.app.log(f"[DEBUG] allow-src check: src_label={src_label!r} src_short={src_short_only!r} src_id={src_id!r} "
                f"allow_ids={allow_ids!r} allow_shorts={allow_shorts!r}", level=2)

            if allow_ids or allow_shorts:
                allowed = False

                # match por ID exacto
                if allow_ids and src_id in allow_ids:
                    allowed = True

                # match por shortname exacto
                if not allowed and allow_shorts and src_short_only in allow_shorts:
                    allowed = True

                if not allowed:
                    self.app.log(self.app.var_text("mesh_channel_src_deny", src=(src_short_only or src_label), src_id=src_id), level=1)
                    return 
                    
            # 8) Limpieza del texto (si existe)
            clean_txt = self._clean_text(txt)
            clean_txt = (clean_txt or "").strip()
            if not clean_txt:
                return
            # 9) Fallback dedup por texto (por si no hay id / o por reemisiones raras)
            pkt_idx = self._packet_channel_index(packet)
            if pkt_idx is None:
                pkt_idx = -1
                
            # Dedup flojo: corta ecos multi-bridge (fromId puede cambiar)
            if self._mesh_seen_text_loose(pkt_idx if pkt_idx is not None else -1, clean_txt):
                self.app.log("[DEBUG] DROP mesh echo (loose text dedup) [channel mode]", level=2)
                return
                
            if self._mesh_seen_text_fallback(str(from_id or "?"), int(pkt_idx), clean_txt):
                self.app.log(f"[DEBUG] DROP mesh duplicate (text fallback) [channel mode]", level=2)
                return
            # 10) Construir salida con formato pedido
            out_txt = f"[{src_label}] {clean_txt}"

            # 11) Destino VARA (Estacion concreta)
            dest = (self.vara_out_to or "").strip().upper()
            if not dest:
                return

            # 12) Log + remember (antes de enviar, por si vuelve eco inmediato)
            pkt_idx = self._packet_channel_index(packet)
            self.app.log(f"[MESH -> VARA] (ch={pkt_idx}) {src_label} -> {dest} : {out_txt}", level=1)

            self._remember_sent_to_vara(out_txt)
            # --- Anti-eco MESH(ch)->VARA(relay): marca la huella para cortar el rebote VARA->MESH(ch) ---
            k_inj = self._key("meshch2vara", src=dest, dst=f"chan#{pkt_idx}", text=out_txt)
            self._mark(k_inj)
            # 13) Enviar por VARA usando HubApp
            self.app.send_text_line(f"{dest}: {out_txt}")

        except Exception as e:
            self.app.log(f"[ERR] ❌ Mesh->VARA(channel) error: {e}", level=0)

    
    def _on_mesh_packet(self, packet=None, interface=None, **kwargs):
        if not packet:
            return

        try:
            from_id = packet.get("fromId") or packet.get("from")
            if from_id:
                src_label = self.mesh.shortname_from_id(from_id) or str(from_id or "")
                src_label = (src_label or "").strip().upper()
                if src_label and not src_label.startswith("!"):
                    self.last_mesh_origin = src_label
        except Exception:
            pass

        # 1) Primero intentar DM al bridge
        handled_dm = self._on_mesh_packet_dm_to_me(packet=packet, interface=interface, **kwargs)
        if handled_dm:
            self.app.log("[DEBUG] routing -> DM MODE", level=2)
            return

        # 2) Si no era DM, intentar modo canal
        if self.mesh_rx_channel_name and self._channel_matches(packet):
            self.app.log("[DEBUG] routing -> CHANNEL MODE", level=2)
            return self._on_mesh_packet_channel_to_vara(packet=packet, interface=interface, **kwargs)

        return

 
    def send_to_mesh_shortname(self, shortname: str, text: str, use_channel: bool = False):
        try:
            dest_id = self.mesh.resolve_dest_id(None, shortname)
            if not dest_id:
                self.app.log(f"[ERR] Mesh notify fail: shortname '{shortname}' not resolved", level=0)
                return False

            ch_idx = self.mesh_channel_index if use_channel else None

            self.app.log(f"[DEBUG] TX BACK -> shortname={shortname} dest_id={dest_id} ch={ch_idx} text={text!r}", level=2)

            self._mesh_send(
                text,
                destination_id=dest_id,
                channel_index=ch_idx,
                want_ack=True,
            )
            return True

        except Exception as e:
            self.app.log(f"[ERR] Mesh notify fail: {e}", level=0)
            return False

    def _clean_text(self, s: str) -> str:
        # elimina tags técnicos repetidos en cualquier parte
        s = self.TAG_RE.sub("", s or "")
        # compacta espacios
        return " ".join(s.split()).strip()

    def _gc_seen(self):
        t = time.time()
        dead = [k for k, ts in self._seen.items() if (t - ts) > self.echo_ttl]
        for k in dead:
            self._seen.pop(k, None)
            
    def _gc_mesh_seen(self):
        t = time.time()
        ttl = getattr(self, "mesh_rx_dedup_ttl", 60)
        for d in (self._mesh_seen_pkt, self._mesh_seen_txt):
            dead = [k for k, ts in d.items() if (t - ts) > ttl]
            for k in dead:
                d.pop(k, None)    
                
    def _mesh_seen_packet_id(self, packet) -> bool:
        """
        True si ya vimos este paquete (por id) recientemente.
        """
        self._gc_mesh_seen()
        pid = packet.get("id") or packet.get("packetId")
        if pid is None:
            return False
        try:
            pid = int(pid)
        except Exception:
            return False

        if pid in self._mesh_seen_pkt:
            return True
        self._mesh_seen_pkt[pid] = time.time()
        return False

    def _mesh_seen_text_fallback(self, from_id: str, ch_idx: int, clean_txt: str) -> bool:
        """
        Fallback si no hay packet.id (o por seguridad extra):
        dedup por (from_id, canal, texto normalizado).
        """
        self._gc_mesh_seen()
        n = self._norm_loop_text(clean_txt).lower()
        if not n:
            return False
        k = f"{from_id}|ch={ch_idx}|{n}"
        if k in self._mesh_seen_txt:
            return True
        self._mesh_seen_txt[k] = time.time()
        return False
        
    def _key(self, direction: str, src: str, dst: str, text: str) -> str:
        h = hashlib.sha1(f"{direction}|{src}|{dst}|{text}".encode("utf-8", "ignore")).hexdigest()
        return h

    def _mark(self, k: str):
        self._gc_seen()
        self._seen[k] = time.time()

    def _seen_before(self, k: str) -> bool:
        self._gc_seen()
        return k in self._seen
    
    def _mesh_send(self, text: str, *, destination_id=None, channel_index=None, want_ack=False):
        kwargs = {}
        if destination_id:
            kwargs["destinationId"] = destination_id
        if channel_index is not None:
            kwargs["channelIndex"] = channel_index
        if want_ack and destination_id:
            kwargs["wantAck"] = True

        try:
            return self.mesh.iface.sendText(text, **kwargs)
        except TypeError:
            # por si tu versión no soporta wantAck
            kwargs.pop("wantAck", None)
            return self.mesh.iface.sendText(text, **kwargs)

    
    def _notify_mesh_no_delivery(self, mesh_src_short: str, dst_call: str, msgid: int, seq: int, reason: str = "NO_ACK"):
        mesh_src_short = (mesh_src_short or "").strip().upper()
        dst_call = (dst_call or "").strip().upper()
        if not mesh_src_short:
            return

        text = f"!FWD_FAIL {msgid} {dst_call} seq={seq} reason={reason}"

        try:
            ok = self.send_to_mesh_shortname(mesh_src_short, text)
            if ok:
                self.app.log(f"[NDR->MESH] {mesh_src_short} <= {text}", level=1)
            else:
                self.app.log(f"[ERR] NDR->MESH failed to {mesh_src_short}: destination not resolved", level=0)
        except Exception as e:
            self.app.log(f"[ERR] NDR->MESH failed to {mesh_src_short}: {e}", level=0)


    #-----------------------------------------
    # ---------- Meshtastic -> VARA ----------
    #-----------------------------------------
    def _on_mesh_packet_dm_to_me(self, packet=None, interface=None, **kwargs):
        """
        Mesh -> VARA

        SOLO reenvía:
          - Mensajes directos (DM)
          - Dirigidos específicamente a este nodo bridge (este cliente TCP)
          Devuelve: return False -> cuando no aplica
                    return True -> cuando si lo ha procesado
        """
        if not packet:
            return False
            
        try:
            # -------- Identidad del bridge (nodeNum y nodeId) --------
            mynum = None
            myid = None
            try:
                ln = getattr(self.mesh.iface, "localNode", None)
                if not ln and hasattr(self.mesh.iface, "getNode"):
                    ln = self.mesh.iface.getNode("^local")
                if ln:
                    # nodeNum: int
                    try:
                        mynum = int(getattr(ln, "nodeNum"))
                    except Exception:
                        mynum = None
                    # nodeId: suele ser string tipo "!e2e5a934"
                    try:
                        myid = str(getattr(ln, "nodeId", "") or "").strip()
                    except Exception:
                        myid = None
            except Exception:
                pass

            from_id = packet.get("fromId")
            if myid and from_id and str(from_id).strip() == myid:
                # Es un mensaje generado por este mismo bridge → no reenviar
                return False

            # Si no se quien soy, no hago nada mas
            if mynum is None and not myid:
                return False

            # -------- Datos de destino del paquete --------
            to_val = packet.get("to")     # suele ser nodeNum (int)
            to_id  = packet.get("toId")   # a veces viene como "!xxxx"

            # -------- Descarta broadcasts / canal --------
            # broadcast típico: to = 0xFFFFFFFF
            try:
                if to_val is not None and int(to_val) == 0xFFFFFFFF:
                    return
            except Exception:
                pass

            # algunas variantes usan toId="^all"/"all"
            if isinstance(to_id, str) and to_id.strip().lower() in ("^all", "all"):
                return False

            # -------- Acepta SOLO si es DM dirigido a mí --------
            dm_to_me = False

            # Caso 1: comparo por nodeNum
            try:
                if to_val is not None and mynum is not None and int(to_val) == int(mynum):
                    dm_to_me = True
            except Exception:
                pass

            # Caso 2: comparo por nodeId
            if (not dm_to_me) and to_id and myid:
                try:
                    if str(to_id).strip() == myid:
                        dm_to_me = True
                except Exception:
                    pass

            if not dm_to_me:
                return False

            # -------- Extraer texto --------
            decoded = packet.get("decoded") or {}
            txt = decoded.get("text")
            if not txt:
                return False

            if not isinstance(txt, str):
                try:
                    txt = txt.decode("utf-8", errors="ignore")
                except Exception:
                    return False

            txt = txt.strip()
            if not txt:
                return False

            # Obtener origen en formato humano (shortName si existe)
            from_id = packet.get("fromId") or ""
            src_label = self.mesh.shortname_from_id(from_id) or str(from_id)

            # Texto limpio (sin tags tecnicos usando _clean_text)
            clean_txt = self._clean_text(txt) if hasattr(self, "_clean_text") else txt

            # "RX local": esta estacion es quien recibe desde Mesh
            rx_local = (self.app.mycall or "?").strip().upper()

            self.app.log(f"[{self.app.mesh_name} -> {self.app.vara_name}] {src_label} -> {rx_local} : {clean_txt}", level=1)


            # -------- Anti-eco --------
            if self.vara_to_mesh_prefix and txt.startswith(self.vara_to_mesh_prefix):
                return

            # ---------------------------------------------------------
            # PRIORIDAD: @DEST ...  => crear RELAY "RELAY > DEST: ..."
            #  - DEST es shortname (o NodeId si empieza por "!")
            #  - va ANTES de AT_CALL_RE o se lo traga como @CALL normal
            # ---------------------------------------------------------
            t = clean_txt.lstrip()

            # Regex: @DEST [:,] mensaje
            m_at = re.match(r"^@([A-Za-z0-9_!.-]{2,16})\s*[:,]?\s*(.+)\s*$", t)
            if m_at:
                dest = (m_at.group(1) or "").strip()
                body = (m_at.group(2) or "").strip()

                if not dest or not body:
                    return

                # ---------------------------
                # MEJORA: normaliza allowed
                # - None => no filtra
                # - "QXT3,QXT6" => {"QXT3","QXT6"}
                # - ["QXT3","QXT6"] / set(...) => {"QXT3","QXT6"}
                # ---------------------------
                raw_allowed = getattr(self.app, "hf_allowed_tx_shortnames", None)

                allowed_set = None
                try:
                    if raw_allowed is None:
                        allowed_set = None
                    elif isinstance(raw_allowed, str):
                        parts = [p.strip().upper() for p in raw_allowed.split(",") if p.strip()]
                        allowed_set = set(parts) if parts else set()
                    elif isinstance(raw_allowed, (list, tuple, set)):
                        allowed_set = set(str(x).strip().upper() for x in raw_allowed if str(x).strip())
                    else:
                        # cualquier otra cosa: lo convertimos a string y tratamos como CSV
                        s = str(raw_allowed)
                        parts = [p.strip().upper() for p in s.split(",") if p.strip()]
                        allowed_set = set(parts) if parts else set()
                except Exception:
                    # Si algo va mal, no filtramos para no romper RX
                    allowed_set = None

                # destino normalizado para el filtro (shortname)
                dst_norm = dest.strip().upper().lstrip("@")

                # Si es NodeId (!abcd1234), normalmente NO aplica filtro por shortname
                # (puedes cambiarlo si quieres filtrar también NodeId)
                is_nodeid = dst_norm.startswith("!")

                self.app.log(f"[DEBUG] HF_TX_FILTER allowed={allowed_set} dst={dst_norm}", level=2)
                
                # Outbound Filter from Meshtastic nodes to Meshtastic nodes
                if (allowed_set is not None) and (not is_nodeid) and (dst_norm not in allowed_set):
                    self.app.log(self.app.var_text("hf_tx_deny_dest", dest=dst_norm, allowed=",".join(sorted(allowed_set))), level=0)
                    
                    # Send back to Meshastastic Node a message: Destination NOT allowed
                    origin = src_label.strip().upper()
                    if self.app.bridge and hasattr(self.app.bridge, "send_to_mesh_shortname"):
                        notify = f"Message to {dst_norm} not sent (Node NOT allowed)"
                        ok = self.app.bridge.send_to_mesh_shortname(origin, notify)
                    return

                relay_call = (self.vara_out_to or "ALL").strip().upper()
                if relay_call == "ALL":
                    self.app.log(f"[WARN] @{dest} received but vara_out_to=ALL; It cannot relay", level=1)
                    return

                # Normaliza DEST a mayúsculas si es shortname; si es NodeId "!abcd" lo dejamos tal cual
                dest_norm = dest if dest.startswith("!") else dest.upper()

                origin = src_label.strip().upper()
                relay = relay_call.strip().upper()

                # --------- NUEVO: tracking para poder avisar al remitente Mesh si llega !FWD_DENY ---------
                msgid = self.app.next_msgid()
                # msgid VARA -> origen Meshtastic (shortName) para poder avisar si llega !FWD_DENY
                self.app.pending_mesh_forwards[msgid] = src_label.strip().upper()
                # Payload forwarding por VARA
                # mantenemos el tag de ruta para que el receptor lo vea
                fwd_text = f">{dest_norm}: [{origin}>{relay}] {body}"
                payload = fwd_text.encode("utf-8", errors="replace")

                # CUIDADO: dst en el paquete debe ser el relay_call (quien recibe y puede contestar con !FWD_DENY)
                pkt = app_pack(T_MSG, 0, self.app.mycall, relay_call, msgid, 0, 0, payload)
                
                # Log equivalente al de send_text_line (Mesh -> VARA forward)
                self.app.log(f"[TX DM] {self.app.mycall} > {relay_call} > {dest_norm}: {body}", level=1)
                
                # Enviar por VARA con ACK stop&wait (si el relay no responde, tambien avisamos al Mesh)
                ok = self.app._send_with_ack(
                    pkt=pkt,
                    dst=relay_call,              # <- importante: el ACK viene del relay_call
                    msgid=msgid,
                    seq=0,
                    payload_len=len(payload),
                    dst_ax25=relay_call,
                    src=origin,
                    on_fail=lambda d, mid, seq, reason: self._notify_mesh_no_delivery(
                        mesh_src_short=origin,
                        dst_call=dest_norm,
                        msgid=mid,
                        seq=seq,
                        reason=reason
                    ),
                )

                self.app.log(
                    f"[DEBUG][{self.app.mesh_name} -> {self.app.vara_name} RELAY] "
                    f"{src_label} -> {relay_call} > {dest_norm}: {body} (msgid={msgid} ok={ok})",
                    level=2
                )
                return

            # -------- Reenvio a VARA --------
            m = AT_CALL_RE.match(txt)
            if m:
                to_call = (m.group(1) or "").strip().upper()
                body = (m.group(2) or "").strip()
                if not body:
                    return

                relay_call = (
                    (getattr(self, "bridge_mesh_to_vara", None) or getattr(self.app, "bridge_mesh_to_vara", None))
                    or getattr(self, "vara_out_to", None)
                )
                relay_call = (str(relay_call).strip().upper() if relay_call else "")

                origin = src_label.strip().upper()
    
                if relay_call and relay_call != "ALL":
                    payload = f"{origin}>{relay_call}: {body}"
                    out_line = f"{relay_call}: {payload}"
                else:
                    payload = f"{origin}: {body}"
                    out_line = f"{to_call}: {payload}"

                self.app.send_text_line(out_line)
                return

            if self.vara_out_to == "ALL":
                out_line = f"ALL: {txt}".strip()
            else:
                out_line = f"{self.vara_out_to}: {txt}".strip()

            self.app.send_text_line(out_line)
            
            return True

        except Exception as e:
            self.app.log(f"[ERR] ❌ MeshBridge RX {self.app.mesh_name}  error: {e}", level=0)
            return False


    def _deny_reply_vara(self, src_call: str, msgid: int, dest: str, allow_set):
        try:
            if not getattr(self, "mesh_want_ack", False):
                return

            src_call = (src_call or "").strip().upper()
            if not src_call or src_call in ("ALL", "CQ"):
                return

            deny_text = f"!FWD_DENY {msgid if msgid is not None else -1} {dest} NOT_ALLOWED"

            # Enviar sin esperar ACK para evitar bloqueos/retries largos
            self.app.send_dm(src_call, deny_text, wait_ack=False)

        except Exception as e:
            self.app.log(f"[WARN] deny-reply failed: {e}", level=1)



    # ---------- VARA -> Meshtastic ----------
    def on_vara_text(self, src: str, dst: str, text: str, is_bcast: bool, msgid: int = None):
        """
        Recibe texto desde VARA y lo reenvía a Meshtastic SOLO si viene en formato forwarding:
            >DEST: mensaje
        donde DEST puede ser ShortName (QXT3) o NodeId (!abcdef01).

        Objetivo:
          - Que en Mesh se vea: "30QXT3: mensaje" (limpio)
          - Sin prefijos técnicos acumulados
          - Anti-eco robusto (TTL cache)
        """
        try:
            ch_idx = None
            # 1) Limpia tags técnicos que puedan venir pegados de otros bridges
            clean = self._clean_text(text)

            # Forwarding explícito soportado:
            #   >DEST: mensaje   (relay clásico)
            #   @DEST mensaje    (tu atajo)
            #   @DEST: mensaje
            #   @DEST, mensaje
            if clean.startswith(">"):
                fwd = clean
            elif clean.lstrip().startswith("@"):
                t = clean.lstrip()
                # Parse @DEST ...

                m = AT_CALL_RE.match(t)
                if not m:
                    return
                dest = (m.group(1) or "").strip()
                msg  = (m.group(2) or "").strip()
                if not dest or not msg:
                    return
                # Convertimos a tu formato interno: >DEST: mensaje
                fwd = f">{dest}: {msg}"
            else:
                # ---------------------------------------------
                # NUEVO: canal por defecto (--mesh-channel-name)
                # ---------------------------------------------
                try:
                    # Resolver canal por nombre si existe
                    if getattr(self, "mesh_channel_name", None) and hasattr(self.mesh, "resolve_channel_index"):
                        ch_idx = self.mesh.resolve_channel_index(None, self.mesh_channel_name)

                    if ch_idx is None:
                        return  # No hay canal por defecto configurado

                    rendered = f"{src}: {clean}".strip()

                    # ---------------------------------------------------------
                    # FILTRO unificado VARA->MESHTASTIC CH:
                    #   - permite por estación VARA (src)
                    #   - o por nodo "lógico" embebido en el texto, ej: [QXT6] mensaje
                    # ---------------------------------------------------------
                    allow_nodes = getattr(self, "mesh_channel_allow_from_nodes", None)
                    allow_stations = getattr(self, "mesh_channel_allow_from_stations", None)

                    if allow_nodes or allow_stations:
                        src_station = (src or "").strip().upper()
                        tagged_node = _extract_mesh_node_from_text(clean) or _extract_mesh_node_from_text(rendered)

                        allowed = False

                        # 1) Permitir por estación VARA
                        if allow_stations and src_station in allow_stations:
                            allowed = True

                        # 2) Permitir por nodo shortname embebido en el texto
                        if not allowed and allow_nodes and tagged_node and tagged_node in allow_nodes:
                            allowed = True

                        self.app.log(
                            f"[DEBUG] VARA->MESH(CH) allow-from check: "
                            f"src_station={src_station!r} tagged_node={tagged_node!r} " f"allow_nodes={allow_nodes!r} allow_stations={allow_stations!r}", level=2)

                        if not allowed:
                            self.app.log(self.app.var_text("vara_mesh_ch_src_deny", station=src_station, node=(tagged_node or "?")), level=1)
                            return

                    # Anti-eco cache
                    k_fwd = self._key("vara2mesh", src=src, dst=f"chan#{ch_idx}", text=rendered)
                    self._mark(k_fwd)

                    self.app.log(f"[{self.app.vara_name} -> {self.app.mesh_name} CH] {src} -> {self.mesh_channel_name}: {clean}", level=1)

                    self._mesh_send(rendered, destination_id=None, channel_index=ch_idx, want_ack=False)

                    return

                except Exception as e:
                    self.app.log(f"[ERR] {self.app.vara_name} -> {self.app.mesh_name} default channel: {e}", level=0)
                    return


            # 2) Anti-eco "compat" (por si aún entra texto marcado como Mesh->VARA de ESTE bridge)
            local_mesh_prefix = f"[mesh@{self.app.mycall}] "
            if clean.startswith(local_mesh_prefix):
                return

            # 3) Anti-eco robusto por caché (evita rebotes incluso sin tags)
            k_back = self._key("mesh2vara", src=src, dst=dst, text=clean)
            if self._seen_before(k_back):
                return

            # 4) Parse: >DEST: mensaje
            body = fwd[1:].strip()
            if ":" not in body:
                self.app.log(self.app.var_text("warn_forward_malformed", src=src, text=clean), level=1)
                return

            mesh_dest, mesh_msg = body.split(":", 1)
            mesh_dest = mesh_dest.strip()
            mesh_msg = mesh_msg.strip()
            
            
            # ---------------------------------------------------------
            # LIMITADOR (WHITELIST) de destinos Meshtastic
            #   - Si self.allow_from_vara_to_nodes es None => permitir cualquier destino
            #   - Si no => solo permitir esos shortnames
            # ---------------------------------------------------------
            if getattr(self, "allow_from_vara_to_nodes", None):
                allow = self.allow_from_vara_to_nodes

                dest_in = (mesh_dest or "").strip()

                if dest_in.startswith("!"):
                    # El mensaje pide nodeId directo. Intentamos mapearlo a shortName para decidir.
                    dest_sn = None
                    try:
                        nodes = getattr(self.mesh.iface, "nodes", {}) or {}
                        node = nodes.get(dest_in)
                        u = (node or {}).get("user", {}) or {}
                        dest_sn = (u.get("shortName") or "").strip().upper() or None
                    except Exception:
                        dest_sn = None

                    if not dest_sn:
                        self.app.log(self.app.var_text("vara_mesh_dest_deny_unresolved", vara=self.app.vara_name, mesh=self.app.mesh_name, dest=dest_in, allow=",".join(sorted(allow))), level=1)
                        self._deny_reply_vara(src_call=src, msgid=msgid, dest=dest_in, allow_set=allow)  
                        return


                    if dest_sn not in allow:
                        self.app.log(self.app.var_text("bridge_deny_dest", vara_name=self.app.vara_name, mesh_name=self.app.mesh_name, dest=dest_sn, allowed=",".join(sorted(allow))), level=1)
                        self._deny_reply_vara(src_call=src, msgid=msgid, dest=dest_sn, allow_set=allow)  # <-- AÑADIR
                        return

                else:
                    # DEST es shortname
                    dest_sn = dest_in.upper()
                    if dest_sn not in allow:
                        self.app.log(self.app.var_text("bridge_deny_dest", vara_name=self.app.vara_name, mesh_name=self.app.mesh_name, dest=dest_sn, allowed=",".join(sorted(allow))), level=1)

                        self._deny_reply_vara(src_call=src, msgid=msgid, dest=dest_sn, allow_set=allow)
                        return


            if not mesh_dest or not mesh_msg:
                self.app.log(self.app.var_text("warn_forward_incomplete", src=src, text=clean), level=1)
                return

           # ---------------------------------------------------------
            # PASO 2: extraer cabecera de ruta si viene como:
            #   >DEST: [QXT6>30QXT1] mensaje
            # ---------------------------------------------------------
            route = None  # <-- MUY IMPORTANTE

            m_route = re.match(r"^\[([^\]]{2,120})\]\s*(.*)$", mesh_msg)
            if m_route:
                route = (m_route.group(1) or "").strip()
                mesh_msg = (m_route.group(2) or "").strip()

                # Añadir hop local (esta estación) a la ruta
                if route and not route.endswith(self.app.mycall):
                    route = f"{route}>{self.app.mycall}"

            # ---------------------------------------------------------
            # Construcción del texto final para Meshtastic
            # ---------------------------------------------------------
            # 5) Render final (lo que verá Meshtastic)
            if route:
                origin = route.split(">", 1)[0].strip()   # primer nodo = emisor real
                rendered = f"[{origin}] {mesh_msg}".strip()
            else:
                rendered = f"[{src}] {mesh_msg}".strip()

            # 6) Resolver destino: ShortName -> NodeId, o usar NodeId si ya viene con "!"
            dest_in = mesh_dest
            if dest_in.startswith("!"):
                dest_id = dest_in
            else:
                dest_id = self.mesh.resolve_dest_id(None, dest_in)

            if not dest_id:
                self.app.log(self.app.var_text("warn_nodeid_not_found", dest=mesh_dest),level=1)
                return
                
            # --- Anti-eco: si esto es el rebote de lo que yo metí desde MESH(canal)->VARA, no reinyectar al canal ---
            k_loop = self._key("meshch2vara", src=src, dst=f"chan#{ch_idx}", text=rendered)
            if self._seen_before(k_loop):
                self.app.log("[DEBUG] DROP loopback VARA->MESH(CH) (rebote de MESH->VARA(channel))", level=2)
                return
                
            # 7) Marca en caché lo que estamos a punto de meter en Mesh (para no rebotarlo luego)
            k_fwd = self._key("vara2mesh", src=src, dst=dest_id, text=rendered)
            self._mark(k_fwd)

            # 8) Log claro
            # Este log debe reflejar solo el salto actual (esta estación -> destino mesh)
            self.app.log(f"[{self.app.vara_name} -> {self.app.mesh_name}] {self.app.mycall} > {mesh_dest} ({dest_id}): {mesh_msg}", level=1)


            # 9) Envío DM a Mesh (destino resuelto)
            self._mesh_send(rendered, destination_id=dest_id, channel_index=None, want_ack=self.mesh_want_ack)

        except Exception as e:
            self.app.log(self.app.var_text("meshbridge_tx_error", mesh_name=self.app.mesh_name, error=str(e)), level=0)


# ----------------------------------
# ---------- MAIN ------------------
# ----------------------------------

def shutdown(app=None, mesh=None, stop_evt=None, threads=()):
    app.log("📴 Stopping MesHFest-lite...\n")

    # 1) Señal de parada (idempotente)
    try:
        if stop_evt is not None:
            stop_evt.set()
    except Exception:
        pass

    # 2) CORTA EL PUBLISHER primero (TCPInterface) para que deje de generar eventos
    try:
        if mesh is not None:
            iface = getattr(mesh, "iface", None)
            if iface is not None:
                try:
                    iface.close()
                except Exception:
                    pass
    except Exception:
        pass

    # 3) Dejar drenar callbacks/eventos ya encolados (MUY importante)
    try:
        time.sleep(0.2)
    except Exception:
        pass

    # 4) PubSub: desuscribe after close publisher
    try:
        pub.unsubscribe(app.bridge._on_mesh_packet, "meshtastic.receive")
    except Exception as e:
        app.log(f"Error Suscription: {type(e).__name__}: {e}")

    # 5) Close Mesh wrapper (if it has his own close)
    try:
        if mesh is not None and hasattr(mesh, "close"):
            try:
                mesh.close()
            except Exception:
                pass
    except Exception:
        pass

    # 6) Close VARA/KISS/etc.
    for attr in ("kiss", "vara", "sock", "socket", "_sock", "_socket", "control_sock", "ctrl_sock"):
        try:
            obj = getattr(app, attr, None) if app is not None else None
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        except Exception:
            pass

    # 7) Join threads
    for t in threads or ():
        try:
            if t and t.is_alive():
                t.join(timeout=2.0)
        except Exception:
            pass
    
    # 8) Close app resources (log file, etc.)
    try:
        if app is not None and hasattr(app, "close"):
            app.close()
    except Exception:
        pass

def maintenance_thread(app: HubApp, stop_evt: threading.Event, mesh: Optional[Mesh]):
    app.log("[DEBUG] Maintenance thread started", level=2)
    while not stop_evt.is_set():
        try:
            if mesh is not None:
                mesh.tick()
        except Exception as e:
            app.log(f"[WARN] mesh.tick error: {e}", level=1)

        stop_evt.wait(0.5)

    app.log("[DEBUG] Maintenance thread exited", level=2)

# ---------------------------------------
# ---------------- MAIN -----------------
# ---------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Interactive chat + file transfer over VARA HF (KISS/TCP) & Meshtastic Bridge.")
    
    ap.add_argument("--config", help="YAML config file path")
    ap.add_argument("--run-as-service", action="store_true", help="Run as background service (no stdin/UI, only logs)")
    ap.add_argument("--tick-hz", type=int, default=2, help="Main tick rate for service loop (default 2 Hz)")
    
    ap.add_argument("--call", default=None, help="Your callsign, e.g. EA1ABC")
    ap.add_argument("--host", default="127.0.0.1", help="KISS TCP host (VARA), default 127.0.0.1")
    ap.add_argument("--port", type=int, default=8100, help="KISS TCP port (VARA), default 8100")
    ap.add_argument("--axdst", default="APVARA", help="AX.25 destination field (cosmetic), default APVARA")

    ap.add_argument("--mesh-serial", default=None, help="Meshtastic serial device (COMx or /dev/ttyUSB0)")
    ap.add_argument("--mesh-host", default=None, help="Meshtastic IP[:PORT] (default 4403)")
    ap.add_argument("--mesh-dest-id", default=None, help="DestinationId (e.g. !abcdef01) to send to a specific node")
    ap.add_argument("--allow-from-vara-to-node", default=None, help="Comma-separated Meshtastic destination ShortNames allowed for relay (e.g. QXT3,QXT6). If omitted, any destination is allowed.")
    ap.add_argument("--allow-from-mesh-via-vara-to-node",default=None,help="Comma-separated ShortNames allowed as HF TX destinations when using '@DEST ...' (e.g. QXT3,QXT6). ""If omitted, HF TX to any @DEST is allowed.")

    ap.add_argument("--mesh-channel-index", type=int, default=None, help="Meshtastic channel (index) forwarding all from VARA to this Meshtastic Channel")
    ap.add_argument("--mesh-rx-channel", default=None, help="Meshtastic channel NAME or INDEX to accept for forwarding Meshtastic to VARA. If omitted, don't accept any channel.")
    ap.add_argument("--mesh-channel-name", default=None, help="Meshtastic channel (name)")
    ap.add_argument("--mesh-channel-allow-src", default=None,help="Comma-separated Meshtastic source ShortNames or ID allowed for relay from channel to VARA (e.g. ABC6,!e2e5a876). If omitted, any source is allowed.")
    ap.add_argument("--mesh-channel-allow-from", default=None, help="Comma-separated allowlist for VARA->Meshtastic channel relay. Accepts mesh node shortnames (e.g. ABC6) or VARA stations/callsigns (e.g. 30XYZ0, EA1ABC-7).")
    ap.add_argument("--mesh-want-ack", action="store_true", help="Request ACK when sending to a specific node (destinationId)")

    ap.add_argument("--bridge-mesh", action="store_true", help="Enable Meshtastic <-> VARA bridge")
    ap.add_argument("--bridge-varato-mesh-prefix", default="[VARA] ", help="Prefix for traffic from VARA to Mesh")
    ap.add_argument("--bridge-meshto-vara-prefix", default="[MESH] ", help="Prefix for traffic from Mesh to VARA")
    ap.add_argument("--bridge-mesh-to-vara", default="ALL", help="VARA destination for traffic coming from Mesh (ALL or CALL)")

    ap.add_argument("--monitor", action="store_true", help="Monitor mode: show readable messages even if not addressed to me/ALL (no ACK)")
    ap.add_argument("-v", "--verbose", type=int, choices=[0, 1, 2], default=1, help="Log level: 0=errors, 1=normal, 2=debug")
    ap.add_argument("--log-mode", choices=["console", "file", "both"], default="console", help="Log destination: console, file, or both")
    ap.add_argument("--log-file", default="meshfest.log", help="Log file path (if --log-mode includes file)")

    ap.add_argument("--lang", choices=["es", "en"], default="en", help="Language of messages: es (Spanish) | en (English)")
    ap.add_argument("--bbs", nargs="?", const="BBS",default=None, help="Enable simple BBS mode. Optional path to BBS folder. If omitted, uses ./BBS")

    args = ap.parse_args()
    
    # If there a config yaml file
    if args.config:
        cfg = load_yaml(args.config)
        args = apply_config(args, cfg)
    
    # Validate all the args     
    validate_args(args, ap)

    app = HubApp(args.call, args.host, args.port, ax25_dst=args.axdst, verbose=args.verbose, log_mode=args.log_mode, log_file=args.log_file, lang=args.lang)
    
    app.verbose = args.verbose
    # Activar modo Mesh->VARA por canal si viene de CLI/YAML
    app.mesh_rx_channel = getattr(args, "mesh_rx_channel", None)
    # Guardar etiqueta literal para sustituir "VARA" en logs/tags
    app.vara_name = (args.bridge_varato_mesh_prefix or "VARA").replace("{call}", app.mycall)
    # Guardar etiqueta literal para sustituir "MESH" en logs/tags
    app.mesh_name = (args.bridge_meshto_vara_prefix or "MESH").replace("{call}", app.mycall)
    
    if not getattr(args, "run_as_service", False):
        print(" ")
        print(f"{ANSI_CYAN}{MESHFEST_HEADER}{app.var_text('help_body')}{ANSI_RESET}")
    else:
        app.log(app.var_text("service_starting"), level=1)

    if not app.kiss.connect(app):
        app.log(app.var_text("err_no_kiss_connection"), level=0)
        return
    
    # MONITOR ------------------
    app.monitor = bool(args.monitor)
    if app.monitor:
        app.log(app.var_text("info_monitor_on"), level=1)
        
    # BBS
    if args.bbs is not None:
        bbs_path = os.path.abspath(args.bbs)
        os.makedirs(bbs_path, exist_ok=True)
        app.bbs_enabled = True
        app.bbs_dir = bbs_path
        app.log(app.var_text("info_bbs_enabled", bbs_path=bbs_path), level=1)
        
    # FIREWALL RULES-----------------
    # --mesh-allow-dest-shortname
    # Permitir solo reenviar a determinados nodos internos (desde Estacion HF a Malla), sino todos los nodos destino son permitodos
    allowed_shortnames = None
    if args.allow_from_vara_to_node:
        allowed_shortnames = {
            s.strip().upper()
            for s in args.allow_from_vara_to_node.split(",")
            if s.strip()}
            
    # --hf-allow-tx-dest-shortname" 
    # Permite solo emitir desde estacion HF para otra estacion si los nodos estan en la lista, sino hay opcion permite todos los destinos     
    hf_allowed_tx_shortnames = None
    if args.allow_from_mesh_via_vara_to_node:
        hf_allowed_tx_shortnames = {
            s.strip().upper().lstrip("@")
            for s in args.allow_from_mesh_via_vara_to_node.split(",")
            if s.strip()}
    
    mesh = None
    # Global Event Thread
    stop_evt = threading.Event()
    
    if args.bridge_mesh:
        if not args.mesh_serial and not args.mesh_host:
            app.log(app.var_text("err_bridge_mesh_missing_iface"), level=0)
            return

        mesh = Mesh(serial_path=args.mesh_serial, hostport=args.mesh_host, app=app)
        
        # Initial wait for Meshtastic complete TCP handshake
        app.log(app.var_text("mesh_connecting"), level=1)
        time.sleep(5.0)

        for i in range(3):
            # Warm-up real force channels reading
            try:
                ch = mesh.get_channels()
                if ch:
                    app.log(app.var_text("mesh_ready"), level=1)
                    break
            except Exception as e:
                app.log(app.var_text("warn_mesh_not_ready_retry", error=e, attempt=i+1), level=1)
                time.sleep(2.0)
                
        else:
            app.log(app.var_text("warn_mesh_not_ready"), level=1)

        mesh_dest_id = mesh.resolve_dest_id(args.mesh_dest_id, None)
        
        # Comprobamos si mesh_dest_id esta en los nodos permitidos
        fixed_dest_shortname = None
        if mesh_dest_id:
            fixed_dest_shortname = mesh.shortname_from_id(mesh_dest_id)
            if fixed_dest_shortname:
                fixed_dest_shortname = fixed_dest_shortname.strip().upper()

        if mesh_dest_id and allowed_shortnames is not None:
            if not fixed_dest_shortname:
                app.log(
                    f"[ERR] Fixed Meshtastic destination '{mesh_dest_id}' could not be resolved to a shortName, cannot validate allowlist.",
                    level=0
                )
                return

            if fixed_dest_shortname not in allowed_shortnames:
                app.log(
                    f"[ERR] Fixed Meshtastic destination '{fixed_dest_shortname}' is not included in allowlist: {', '.join(sorted(allowed_shortnames))}",
                    level=0
                )
                return
      
        #Comprobamos si el nodo destino ha sido escuchado por el nodo que hara el reenvio:
        if args.mesh_dest_id and not mesh_dest_id:
            app.log(app.var_text("err_mesh_dest_id_invalid", dest_id=args.mesh_dest_id), level=0)
            return
           

        mesh_chan_idx = mesh.resolve_channel_index(args.mesh_channel_index, args.mesh_channel_name)
        
        if args.allow_from_vara_to_node or args.mesh_dest_id is not None:
            app.log(app.var_text("info_mesh_dest_confirmed", dest_input=args.allow_from_vara_to_node or args.mesh_dest_id, dest_id=mesh_dest_id), level=1)

        if allowed_shortnames:
            app.log(app.var_text("info_mesh_forward_policy", nodes=", ".join(sorted(allowed_shortnames))), level=1)

        app.hf_allowed_tx_shortnames = hf_allowed_tx_shortnames  
        if hf_allowed_tx_shortnames:
            app.log(app.var_text("info_hf_output_policy", nodes=", ".join(sorted(hf_allowed_tx_shortnames))), level=1)

        # Dynamic prefixes building
        vara_to_mesh_prefix = args.bridge_varato_mesh_prefix.replace("{call}", app.mycall)
                
        # BRIDGE -----------------
        app.bridge = MeshBridge(
            app=app,
            mesh=mesh,
            mesh_channel_index=mesh_chan_idx,
            mesh_channel_name=args.mesh_channel_name,
            mesh_want_ack=bool(args.mesh_want_ack),
            allow_from_vara_to_nodes=allowed_shortnames,
            mesh_channel_allow_src=args.mesh_channel_allow_src,
            mesh_channel_allow_from=args.mesh_channel_allow_from,
            vara_out_to=args.bridge_mesh_to_vara,
            vara_to_mesh_prefix=vara_to_mesh_prefix,
        )

    # Creating Threads
    t_rx = threading.Thread(target=rx_thread, args=(app, stop_evt), daemon=True)
    t_rx.start()
    threads = [t_rx]
    
    t_maintenance = threading.Thread(target=maintenance_thread, args=(app, stop_evt, mesh), daemon=True)
    t_maintenance.start()
    threads.append(t_maintenance)
    
    if not getattr(args, "run_as_service", False):
        t_in = threading.Thread(target=input_thread, args=(app, stop_evt), daemon=True)
        t_in.start()
        threads.append(t_in)
    else:
        t_in = None

    # keep main() alive
    tick_hz = getattr(args, "tick_hz", 2) or 2
    dt = 1.0 / float(tick_hz)

    try:
        while not stop_evt.is_set():
            time.sleep(dt)

    except KeyboardInterrupt:
        stop_evt.set()
    finally:
        shutdown(app=app, mesh=mesh, stop_evt=stop_evt, threads=tuple(threads))
    

if __name__ == "__main__":
    main()
