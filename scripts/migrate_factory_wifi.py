#!/usr/bin/env python3
"""Recover only factory Wi-Fi credentials from an ESP32 full-flash backup.

Secret values are never printed. The output JSON is created with mode 0600 and
must remain under the ignored `secrets/` directory.
"""

import argparse
import json
import os
import struct
from pathlib import Path

PARTITION_TABLE_OFFSET = 0x8000
PARTITION_ENTRY_SIZE = 32
NVS_PAGE_SIZE = 4096
NVS_ENTRY_OFFSET = 64
NVS_ENTRY_SIZE = 32
NVS_ENTRY_COUNT = 126
TYPE_U8 = 0x01
TYPE_STRING = 0x21


def find_partition(flash: bytes, label: str) -> tuple[int, int]:
    table = flash[PARTITION_TABLE_OFFSET : PARTITION_TABLE_OFFSET + 0x1000]
    for position in range(0, len(table), PARTITION_ENTRY_SIZE):
        entry = table[position : position + PARTITION_ENTRY_SIZE]
        if len(entry) != PARTITION_ENTRY_SIZE:
            break
        magic, _, _, offset, size, raw_label, _ = struct.unpack("<HBBLL16sL", entry)
        decoded_label = raw_label.split(b"\0", 1)[0].decode("ascii", "replace")
        if magic == 0x50AA and decoded_label == label:
            return offset, size
    raise ValueError(f"partition not found: {label}")


def parse_nvs_strings(partition: bytes) -> dict[tuple[str, str], str]:
    namespaces: dict[int, str] = {}
    raw_entries: list[tuple[int, int, int, str, bytes]] = []

    for page_start in range(0, len(partition), NVS_PAGE_SIZE):
        page = partition[page_start : page_start + NVS_PAGE_SIZE]
        slot = 0
        while slot < NVS_ENTRY_COUNT:
            position = NVS_ENTRY_OFFSET + slot * NVS_ENTRY_SIZE
            entry = page[position : position + NVS_ENTRY_SIZE]
            if len(entry) != NVS_ENTRY_SIZE or entry == b"\xff" * NVS_ENTRY_SIZE:
                slot += 1
                continue
            namespace_id, value_type, span, _, _, raw_key, data = struct.unpack(
                "<BBBBI16s8s", entry
            )
            key = raw_key.split(b"\0", 1)[0].decode("utf-8", "replace")
            if not key or not 1 <= span <= NVS_ENTRY_COUNT - slot:
                slot += 1
                continue
            if namespace_id == 0 and value_type == TYPE_U8:
                namespaces[data[0]] = key
            elif value_type == TYPE_STRING:
                data_size = struct.unpack_from("<H", data)[0]
                payload_start = position + NVS_ENTRY_SIZE
                payload_capacity = (span - 1) * NVS_ENTRY_SIZE
                payload = page[payload_start : payload_start + payload_capacity][:data_size]
                value = payload.rstrip(b"\0").decode("utf-8")
                raw_entries.append((namespace_id, value_type, span, key, value.encode()))
            slot += span

    values: dict[tuple[str, str], str] = {}
    for namespace_id, _, _, key, encoded_value in raw_entries:
        namespace = namespaces.get(namespace_id)
        if namespace:
            values[(namespace, key)] = encoded_value.decode("utf-8")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flash", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    flash = args.flash.read_bytes()
    nvs_offset, nvs_size = find_partition(flash, "nvs")
    values = parse_nvs_strings(flash[nvs_offset : nvs_offset + nvs_size])
    ssid = values.get(("wifi", "ssid"))
    password = values.get(("wifi", "password"))
    if not ssid or password is None:
        raise SystemExit("Factory Wi-Fi values were not found; no secret file was written.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"ssid": ssid, "password": password}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(args.output, 0o600)
    print(
        "Factory Wi-Fi preserved without displaying secrets: "
        f"ssid_bytes={len(ssid.encode())}, password_bytes={len(password.encode())}"
    )


if __name__ == "__main__":
    main()
