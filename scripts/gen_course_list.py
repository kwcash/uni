import sqlite3, csv

cx = sqlite3.connect("../out/fstem.db")
cx.row_factory = sqlite3.Row

courses = {}
for r in cx.execute(
    "SELECT fstem_id, code, title, rung, "
    "COALESCE(verticals_override, verticals_scored) AS vertical, "
    "substrate_status, authored_by FROM assets "
    "WHERE fstem_id IS NOT NULL AND fstem_id != '' ORDER BY fstem_id"):
    fid = r["fstem_id"]
    courses[fid] = dict(r)

for fid in courses:
    count = cx.execute(
        "SELECT COUNT(*) FROM modules WHERE parent_fstem_id=?", (fid,)).fetchone()[0]
    courses[fid]["module_count"] = count

with open("../COURSE_LIST.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["fstem_id", "mit_code", "mit_title", "rung", "vertical", "substrate_status", "authored_by", "module_count"])
    w.writeheader()
    for fid in sorted(courses.keys()):
        c = courses[fid]
        w.writerow({"fstem_id": fid, "mit_code": c["code"], "mit_title": c["title"], "rung": c["rung"], "vertical": c["vertical"], "substrate_status": c["substrate_status"], "authored_by": c["authored_by"], "module_count": c["module_count"]})

print("wrote ../COURSE_LIST.csv")
cx.close()
