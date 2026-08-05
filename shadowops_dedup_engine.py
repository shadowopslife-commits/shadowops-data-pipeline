import os, sys, csv, re, hashlib, sqlite3, time

WORK = sys.argv[1]
MANIFEST = sys.argv[2]
UNIQUE_OUT = sys.argv[3]
COUNTS_BY_FILE = sys.argv[4]
COUNTS_BY_TAG  = sys.argv[5]
DEDUP_SUMMARY  = sys.argv[6]

DB = os.path.join(WORK, "dedup_index.sqlite")

# --- normalization: ignore formatting/order noise inside the line itself ---
# We do NOT reorder fields; we only normalize characters so commas/quotes/spacing don't block matches.
_space = re.compile(r"\s+")
_non_alnum = re.compile(r"[^0-9a-z]+", re.I)

def normalize_line(s: str) -> str:
    s = s.strip().lower()
    # collapse whitespace
    s = _space.sub(" ", s)
    # normalize common CSV quoting noise
    s = s.replace('\ufeff','').replace('\u200b','')
    # strip surrounding quotes
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
    # canonicalize punctuation differences
    s = _non_alnum.sub(" ", s)
    s = _space.sub(" ", s).strip()
    return s

def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def hb(msg):
    print(msg, flush=True)

def main():
    t0 = time.time()

    # SQLite: one row per unique normalized record hash
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("CREATE TABLE IF NOT EXISTS seen (h TEXT PRIMARY KEY, tags TEXT, src TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS dup (h TEXT, tags TEXT, src TEXT)")
    con.commit()

    # read manifest
    files = []
    with open(MANIFEST, "r", encoding="utf-8", errors="ignore") as f:
        r = csv.DictReader(f)
        for row in r:
            files.append(row)

    hb(f"[INFO] Files in manifest: {len(files)}")
    # outputs
    with open(COUNTS_BY_FILE, "w", newline="", encoding="utf-8") as fcf, \
         open(COUNTS_BY_TAG,  "w", newline="", encoding="utf-8") as fct, \
         open(UNIQUE_OUT,     "w", newline="", encoding="utf-8") as fou:

        w_file = csv.writer(fcf)
        w_file.writerow(["House","FullPath","Tags","LinesTotal","UniqueAdded","DupSkipped"])

        # tag aggregation
        tag_counts = {}

        total_lines = 0
        total_unique = 0
        total_dup = 0

        for idx, row in enumerate(files, start=1):
            path = row["FullPath"]
            tags = row.get("Tags","")
            house = row.get("House","?")

            if not os.path.exists(path):
                hb(f"[WARN] Missing file on disk: {path}")
                continue

            lines_total = 0
            uniq_added = 0
            dup_skipped = 0

            # Update tag file-count stat
            for t in tags.split("|"):
                if not t: 
                    continue
                tag_counts.setdefault(t, {"files":0, "lines":0, "unique":0, "dup":0})
            for t in tags.split("|"):
                if t:
                    tag_counts[t]["files"] += 0  # will bump once per file below

            # bump files once per tag per file
            for t in set([x for x in tags.split("|") if x]):
                tag_counts[t]["files"] += 1

            hb(f"[FILE {idx}/{len(files)}] {house} {os.path.basename(path)}")

            # detect delimiter-ish lines: we still treat each line as one record candidate
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    lines_total += 1
                    total_lines += 1

                    n = normalize_line(line)
                    if not n:
                        continue

                    h = sha1_hex(n)

                    # try insert into seen
                    try:
                        cur.execute("INSERT INTO seen(h,tags,src) VALUES(?,?,?)", (h, tags, path))
                        uniq_added += 1
                        total_unique += 1

                        # write unique record line with tags + source
                        # format: hash,tags,source,normalized_record
                        fou.write(f"{h},{tags.replace(',','|')},{path.replace(',',';')},{n}\n")

                        for t in tags.split("|"):
                            if t:
                                tag_counts[t]["unique"] += 1
                    except sqlite3.IntegrityError:
                        dup_skipped += 1
                        total_dup += 1
                        cur.execute("INSERT INTO dup(h,tags,src) VALUES(?,?,?)", (h, tags, path))
                        for t in tags.split("|"):
                            if t:
                                tag_counts[t]["dup"] += 1

                    if lines_total % 250000 == 0:
                        con.commit()
                        hb(f"[HB] {os.path.basename(path)} lines={lines_total:,} unique+={uniq_added:,} dup={dup_skipped:,}")

            con.commit()

            # line counts by tag
            for t in tags.split("|"):
                if t:
                    tag_counts[t]["lines"] += lines_total

            w_file.writerow([house, path, tags, lines_total, uniq_added, dup_skipped])
            hb(f"[DONE] lines={lines_total:,} unique+={uniq_added:,} dup={dup_skipped:,}")

        # write tag summary
        w_tag = csv.writer(fct)
        w_tag.writerow(["Tag","Files","Lines","Unique","Dup"])
        for tag, v in sorted(tag_counts.items(), key=lambda kv: (-kv[1]["lines"], kv[0])):
            w_tag.writerow([tag, v["files"], v["lines"], v["unique"], v["dup"]])

        # write dedup summary
        with open(DEDUP_SUMMARY, "w", newline="", encoding="utf-8") as fs:
            w = csv.writer(fs)
            w.writerow(["TotalLinesSeen","TotalUniqueRecords","TotalDuplicateRecords","DBPath","UniqueOut"])
            w.writerow([total_lines, total_unique, total_dup, DB, UNIQUE_OUT])

    con.close()
    hb(f"[✓] COMPLETE in {time.time()-t0:.1f}s")
    hb(f"Outputs:\n  {COUNTS_BY_FILE}\n  {COUNTS_BY_TAG}\n  {DEDUP_SUMMARY}\n  {UNIQUE_OUT}\n  {DB}")

if __name__ == "__main__":
    main()
