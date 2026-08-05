import os, re, csv, sqlite3, hashlib, time
from pathlib import Path

Q_ROOT = Path(r"D:\ShadowOps_Main_House_D\_QUALIFIED_C")
C_ROOT = Path(r"C:\ShadowOps_Main_House_C")
SECTION = os.environ.get("SECTION_OVERRIDE")
DB_PATH = Path(r"C:\_FORENSIC_SQLITE\qualified_hash_index.sqlite")
OUT_DIR = Path(r"C:\_FORENSIC_SQLITE\REPORTS")

re_name = re.compile(r"\b[A-Za-z]{2,}\b")
re_addr = re.compile(r"\b\d{1,6}\s+[A-Za-z][A-Za-z\.\-]{1,}\b")
re_zip  = re.compile(r"\b\d{5}\b")
re_city = re.compile(r"\b[A-Za-z]{3,}\b")

def qualifies(line):
    if not re_name.search(line): return False
    if not re_addr.search(line): return False
    if re_zip.search(line): return True
    if re_city.search(line): return True
    return False

def sha1hex(s):
    return hashlib.sha1(s.encode("utf-8","ignore")).hexdigest()

def connect():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=OFF;")
    con.execute("PRAGMA temp_store=MEMORY;")
    con.execute("PRAGMA cache_size=-200000;")
    con.execute("CREATE TABLE IF NOT EXISTS qhash (h TEXT PRIMARY KEY);")
    return con

def build_index(con):
    print("[INDEX BUILD START]")
    cur = con.cursor()
    total = 0
    batch = []
    BATCH = 50000
    COMMIT = 2000000
    last_commit = 0

    for f in list(Q_ROOT.rglob("*.txt")) + list(Q_ROOT.rglob("*.csv")):
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as r:
                for line in r:
                    line=line.rstrip("\n")
                    if not qualifies(line): continue
                    batch.append((sha1hex(line),))
                    total+=1
                    if len(batch)>=BATCH:
                        cur.executemany("INSERT OR IGNORE INTO qhash(h) VALUES (?)", batch)
                        batch=[]
                    if total-last_commit>=COMMIT:
                        con.commit()
                        last_commit=total
                        print(f"[INDEX HB] {total:,}")
        except:
            pass

    if batch:
        cur.executemany("INSERT OR IGNORE INTO qhash(h) VALUES (?)", batch)
    con.commit()
    print("[INDEX COMPLETE]")

def compare_section(con):
    section_path = C_ROOT / SECTION
    print(f"[COMPARE] {SECTION}")
    report_path = OUT_DIR / f"C_REDUNDANCY_REPORT__{SECTION.replace(' ','_')}.csv"

    cur = con.cursor()
    stmt = "SELECT 1 FROM qhash WHERE h=? LIMIT 1"

    with open(report_path,"w",newline="",encoding="utf-8") as out:
        w=csv.writer(out)
        w.writerow(["FullPath","QualifyingLines","Matched","Unmatched","SafeToDelete"])

        for f in list(section_path.rglob("*.txt")) + list(section_path.rglob("*.csv")):
            q=m=0
            try:
                with open(f,"r",encoding="utf-8",errors="ignore") as r:
                    for line in r:
                        line=line.rstrip("\n")
                        if not qualifies(line): continue
                        q+=1
                        if cur.execute(stmt,(sha1hex(line),)).fetchone():
                            m+=1
                u=q-m
                safe="YES" if q>0 and u==0 else "NO"
                w.writerow([str(f),q,m,u,safe])
            except:
                w.writerow([str(f),0,0,0,"ERROR"])

    print("[REPORT GENERATED]",report_path)

def main():
    con=connect()
    build_index(con)
    compare_section(con)
    con.close()

if __name__=="__main__":
    main()
