import os, re, csv, sqlite3, hashlib, time
from pathlib import Path
from datetime import datetime

# ---------------- PATHS ----------------
Q_ROOT = Path(r"D:\ShadowOps_Main_House_D\_QUALIFIED_C")
C_ROOT = Path(r"C:\ShadowOps_Main_House_C")

DB_PATH = Path(r"C:\_FORENSIC_SQLITE\qualified_hash_index.sqlite")
OUT_DIR = Path(r"C:\_FORENSIC_SQLITE\REPORTS")
OUT_DIR.mkdir(parents=True, exist_ok=True)

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
FILE_REPORT = OUT_DIR / f"C_REDUNDANCY_FILE_REPORT__{stamp}.csv"
GLOBAL_SUMMARY = OUT_DIR / f"C_REDUNDANCY_GLOBAL_SUMMARY__{stamp}.csv"

# ---------------- SPEED / SAFETY TUNING ----------------
BATCH_INSERT = 50_000        # insert batch size
COMMIT_EVERY = 2_000_000     # commit checkpoint (qualifying lines hashed)

HB_Q_LINES   = 5_000_000     # heartbeat while building index
HB_C_FILES   = 200           # heartbeat while comparing C

# ---------------- QUALIFICATION GATE (your rule) ----------------
# must have:
# - name token (2+ letters)
# - address pattern: number + street word
# - and either ZIP(5) OR city-like alpha token (3+)
re_name = re.compile(r"\\b[A-Za-z]{2,}\\b")
re_addr = re.compile(r"\\b\\d{1,6}\\s+[A-Za-z][A-Za-z\\.\\-]{1,}\\b")
re_zip  = re.compile(r"\\b\\d{5}\\b")
re_city = re.compile(r"\\b[A-Za-z]{3,}\\b")

def qualifies(line: str) -> bool:
    if not re_name.search(line): return False
    if not re_addr.search(line): return False
    if re_zip.search(line): return True
    if re_city.search(line): return True
    return False

def sha1hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8","ignore")).hexdigest()

def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    # WAL for speed + resilience
    con.execute("PRAGMA journal_mode=WAL;")
    # performance (safe enough for rebuildable index)
    con.execute("PRAGMA synchronous=OFF;")
    con.execute("PRAGMA temp_store=MEMORY;")
    con.execute("PRAGMA cache_size=-200000;")   # ~200MB cache
    con.execute("PRAGMA mmap_size=268435456;")  # 256MB mmap
    con.execute("PRAGMA locking_mode=NORMAL;")
    con.execute("PRAGMA busy_timeout=60000;")

    con.execute("CREATE TABLE IF NOT EXISTS qhash (h TEXT PRIMARY KEY);")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);")
    con.commit()
    return con

def meta_get(con, k):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None

def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", (k,v))
    con.commit()

def iter_files(root: Path):
    # Only txt/csv
    for p in root.rglob("*"):
        if not p.is_file(): 
            continue
        suf = p.suffix.lower()
        if suf in (".txt",".csv"):
            yield p

def build_index(con: sqlite3.Connection):
    # If you want to force rebuild, delete DB file.
    built = meta_get(con, "q_index_built")
    if built == "1":
        print("[INDEX] Existing index detected (meta=q_index_built=1). Skipping rebuild.")
        return

    print("[INDEX] Building SQLite hash index from _QUALIFIED_C (chunked commits)...")
    cur = con.cursor()

    total_qual = 0
    batch = []
    last_commit_at = 0
    last_hb_at = 0

    # helpful: track approximate new inserts via total_changes delta
    inserted_new = 0
    prev_changes = con.total_changes

    t0 = time.time()

    q_files = list(iter_files(Q_ROOT))
    print(f"[INDEX] Files in Q: {len(q_files)}")

    for fi, f in enumerate(q_files, start=1):
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as r:
                for line in r:
                    line = line.rstrip("\\n")
                    if not qualifies(line):
                        continue
                    h = sha1hex(line)
                    batch.append((h,))
                    total_qual += 1

                    if len(batch) >= BATCH_INSERT:
                        cur.executemany("INSERT OR IGNORE INTO qhash(h) VALUES (?)", batch)
                        # update inserted estimate
                        ch = con.total_changes
                        inserted_new += (ch - prev_changes)
                        prev_changes = ch
                        batch.clear()

                    if total_qual - last_commit_at >= COMMIT_EVERY:
                        con.commit()
                        last_commit_at = total_qual
                        dt = (time.time() - t0)/60
                        print(f"[INDEX-COMMIT] qualifying_hashed={total_qual:,} approx_new_inserts={inserted_new:,} elapsed={dt:.1f}m")

                    if total_qual - last_hb_at >= HB_Q_LINES:
                        last_hb_at = total_qual
                        dt = (time.time() - t0)/60
                        print(f"[INDEX-HB] qualifying_hashed={total_qual:,} approx_new_inserts={inserted_new:,} elapsed={dt:.1f}m")

        except Exception as e:
            print(f"[WARN] Q read failed: {f} :: {e}")

    if batch:
        cur.executemany("INSERT OR IGNORE INTO qhash(h) VALUES (?)", batch)
        ch = con.total_changes
        inserted_new += (ch - prev_changes)
        prev_changes = ch
        batch.clear()

    con.commit()
    dt = (time.time() - t0)/60
    print(f"[INDEX-DONE] qualifying_hashed={total_qual:,} approx_new_inserts={inserted_new:,} elapsed={dt:.1f}m")

    # mark built (so future runs skip index build)
    meta_set(con, "q_index_built", "1")

def compare_all_C(con: sqlite3.Connection):
    print("[COMPARE] Comparing ALL C (.txt/.csv) against indexed _QUALIFIED_C...")
    cur = con.cursor()
    stmt = "SELECT 1 FROM qhash WHERE h=? LIMIT 1"

    c_files = list(iter_files(C_ROOT))
    print(f"[COMPARE] Files in C: {len(c_files)}")

    # Global totals
    g_files = 0
    g_files_with_qual = 0
    g_files_all_redundant = 0
    g_files_with_unique = 0
    g_files_no_valid = 0

    g_qual_lines = 0
    g_matched = 0
    g_unmatched = 0

    t0 = time.time()

    with open(FILE_REPORT, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["FullPath","QualifyingLines","MatchedInQualifiedC","UnmatchedQualifying","Verdict"])

        for idx, f in enumerate(c_files, start=1):
            g_files += 1
            qcnt = 0
            mcnt = 0

            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as r:
                    for line in r:
                        line = line.rstrip("\\n")
                        if not qualifies(line):
                            continue
                        qcnt += 1
                        h = sha1hex(line)
                        if cur.execute(stmt, (h,)).fetchone():
                            mcnt += 1

                unmatched = qcnt - mcnt

                if qcnt == 0:
                    verdict = "NO_VALID_DATA"
                    g_files_no_valid += 1
                elif unmatched == 0:
                    verdict = "REDUNDANT"
                    g_files_with_qual += 1
                    g_files_all_redundant += 1
                else:
                    verdict = "UNIQUE_PRESENT"
                    g_files_with_qual += 1
                    g_files_with_unique += 1

                w.writerow([str(f), qcnt, mcnt, unmatched, verdict])

                # update global line totals (only qualifying lines)
                g_qual_lines += qcnt
                g_matched += mcnt
                g_unmatched += unmatched

            except Exception as e:
                # safety: treat unreadable files as unique (do not delete)
                w.writerow([str(f), 0, 0, 0, "READ_ERROR__KEEP"])
                print(f"[WARN] C read failed: {f} :: {e}")

            if idx % HB_C_FILES == 0:
                dt = (time.time() - t0)/60
                print(f"[COMPARE-HB] files_done={idx}/{len(c_files)} qualifying_lines={g_qual_lines:,} matched={g_matched:,} unmatched={g_unmatched:,} elapsed={dt:.1f}m")

    # Write global summary
    redundancy_pct = (100.0 * g_matched / g_qual_lines) if g_qual_lines else 0.0
    unique_pct = (100.0 * g_unmatched / g_qual_lines) if g_qual_lines else 0.0

    with open(GLOBAL_SUMMARY, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["Metric","Value"])
        w.writerow(["MasterReferenceFolder", str(Q_ROOT)])
        w.writerow(["CompareUniverseFolder", str(C_ROOT)])
        w.writerow(["C_Files_Total", g_files])
        w.writerow(["C_Files_With_Qualifying_Lines", g_files_with_qual])
        w.writerow(["C_Files_All_Redundant", g_files_all_redundant])
        w.writerow(["C_Files_With_Unique_Present", g_files_with_unique])
        w.writerow(["C_Files_No_Valid_Data", g_files_no_valid])
        w.writerow(["C_Total_Qualifying_Lines", g_qual_lines])
        w.writerow(["C_Total_Matched_Lines", g_matched])
        w.writerow(["C_Total_Unmatched_Lines", g_unmatched])
        w.writerow(["RedundancyPercent_MatchedOfQualifying", f"{redundancy_pct:.4f}"])
        w.writerow(["UniquePercent_UnmatchedOfQualifying", f"{unique_pct:.4f}"])
        w.writerow(["FileReportPath", str(FILE_REPORT)])
        w.writerow(["GlobalSummaryPath", str(GLOBAL_SUMMARY)])

    print("[DONE] File report:", FILE_REPORT)
    print("[DONE] Global summary:", GLOBAL_SUMMARY)

def main():
    if not Q_ROOT.exists():
        raise SystemExit(f"[ERROR] Master reference not found: {Q_ROOT}")
    if not C_ROOT.exists():
        raise SystemExit(f"[ERROR] Compare universe not found: {C_ROOT}")

    con = connect(DB_PATH)
    build_index(con)
    compare_all_C(con)
    con.close()

if __name__ == "__main__":
    main()
