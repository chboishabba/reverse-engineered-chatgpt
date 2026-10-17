import json
import subprocess

cmd = ["gh", "pr", "list", "--state", "all", "--limit", "80", "--repo", "chboishabba/dashi_agda", "--json", "number,title,headRefName,state,createdAt,mergedAt"]
res = subprocess.run(cmd, capture_output=True, text=True, check=True)
prs = json.loads(res.stdout)

recent_prs = [p for p in prs if p.get("createdAt", "") >= "2026-10-01"]

domains = {
    "Yang-Mills / Agda Residuals": [1231, 1230, 1229, 1228, 1217, 1164, 1165],
    "xPollenation / Bio / UniProt": [1227, 1226, 1159],
    "Max Cut / ROM / CFFI": [1225, 1091, 1098],
    "IPFS / DA51 / Atlas": [1223, 1219, 1208],
    "SensibLaw / PNF": [1222, 1191],
    "Governance / Regulatory": [1220, 1195, 1161, 1150],
    "Torque / Mechanics Trials": [1224, 1221, 1218, 1215, 1213, 1199],
    "Goldbach / Math Corpi / RH": [1216, 1214, 1212, 1211],
    "Sync / CI Commits": [1210, 1209, 1206, 1205, 1204, 1203, 1202, 1201, 1200, 1198, 1196, 1193, 1190, 1189, 1188, 1187, 1184, 1183, 1182],
}

matched = set()
for dom, nums in domains.items():
    print(f"\n### {dom}")
    for p in recent_prs:
        p_num = p["number"]
        if p_num in nums:
            matched.add(p_num)
            is_merged = bool(p.get("mergedAt"))
            st = "🟣 MERGED" if is_merged else ("🟢 OPEN" if p["state"] == "OPEN" else "⚪ " + p["state"])
            print(f"  - #{p_num}: {p['title'][:55]} [{st}] ({p['headRefName'][:25]})")

unmatched = [p for p in recent_prs if p["number"] not in matched]
print(f"\n### Other PRs ({len(unmatched)}):")
for p in unmatched[:10]:
    p_num = p["number"]
    is_merged = bool(p.get("mergedAt"))
    st = "🟣 MERGED" if is_merged else ("🟢 OPEN" if p["state"] == "OPEN" else "⚪ " + p["state"])
    print(f"  - #{p_num}: {p['title'][:55]} [{st}]")
