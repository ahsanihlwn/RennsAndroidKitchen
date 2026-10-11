# SPDX-License-Identifier: MPL-2.0
import os
import struct
from core.payload_extract import extract_partitions_from_payload, init_payload_info

print("[CORE STAGE] PAYLOAD")

def detect_image_type(path):
    with open(path, "rb") as f:
        header = f.read(4)
        if header == b'\x3a\xff\x26\xed':
            return "ANDROID SPARSE"
        if header[:2] == b'\x53\xef':
            return "EXT4"
        if header == b'\xe2\xe0\x45\x52':
            return "EROFS"
        return "UNKNOWN"

def list_partitions(payload_path):
    with open(payload_path, "rb") as f:
        manifest = init_payload_info(f)
        print("\nDetected partitions:\n")
        for p in manifest.partitions:
            print(" -", p.partition_name)
        return [p.partition_name for p in manifest.partitions]

def extract_and_check(payload_path, out_dir="out"):
    parts = list_partitions(payload_path)
    print("\nExtracting...\n")

    with open(payload_path, "rb") as f:
        extract_partitions_from_payload(f, parts, out_dir)

    print("\nRESULT:\n")
    for p in parts:
        img = os.path.join(out_dir, p + ".img")
        if os.path.exists(img):
            fs = detect_image_type(img)
            print(f"{p:<16} | {fs}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="payload toolbox")
    parser.add_argument("-i","--input", required=True, help="payload.bin / OTA.zip")
    parser.add_argument("--list", action="store_true", help="list partitions only")
    parser.add_argument("--extract", action="store_true", help="extract and show type")
    parser.add_argument("-o","--out", default="out", help="output dir")
    args = parser.parse_args()

    if args.list:
        list_partitions(args.input)
    elif args.extract:
        extract_and_check(args.input, args.out)
    else:
        print("Use --list or --extract, tolol.")
