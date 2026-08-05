import os
import re
import sys

BASE = r"C:\ShadowOps_CANONICAL\03_STATE_BUILD"
INPUT = os.path.join(BASE, "UNKNOWN_REMAINING.txt")
OUTPUT_UNKNOWN = os.path.join(BASE, "UNKNOWN_FINAL.txt")

STATES = {
    "CA","TX","FL","AZ","NV","OR","UT","ID","VA","WA","NY","NJ","PA",
    "IL","OH","GA","NC","SC","CO"
}

state_files = {}
for st in STATES:
    state_files[st] = open(os.path.join(BASE, f"{st}.txt"), "a", encoding="utf-8", errors="ignore")

unknown_out = open(OUTPUT_UNKNOWN, "w", encoding="utf-8", errors="ignore")

state_regex = {
    st: re.compile(rf'(?i)(^|[^A-Z]){st}([^A-Z]|$)')
    for st in STATES
}

processed = 0
routed = 0

print("=== RESUME STATE ROUTER STARTED ===", flush=True)
print(f"Input: {INPUT}", flush=True)

with open(INPUT, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        processed += 1
        matched = False
        for st, rx in state_regex.items():
            if rx.search(line):
                state_files[st].write(line)
                routed += 1
                matched = True
                break
        if not matched:
            unknown_out.write(line)

        if processed % 1_000_000 == 0:
            print(f"Processed {processed:,} | Routed {routed:,}", flush=True)

for fh in state_files.values():
    fh.close()
unknown_out.close()

print("=== RESUME ROUTE COMPLETE ===", flush=True)
print(f"Processed total: {processed:,}", flush=True)
print(f"Routed total: {routed:,}", flush=True)
print(f"Remaining UNKNOWN written to: {OUTPUT_UNKNOWN}", flush=True)
