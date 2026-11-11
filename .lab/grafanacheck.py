#!/usr/bin/env python3
import base64, json, os, subprocess, sys, time, urllib.error, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
RG = "rg-grafana"
GRAFANA_RESOURCE = "ce34e7e5-485f-4d76-964f-b3d2b16d1e4f"
passed = failed = 0


def check(desc, ok, hint=""):
    global passed, failed
    if ok:
        passed += 1; print("PASS  " + desc, flush=True)
    else:
        failed += 1; print("FAIL  " + desc + (("   -> " + hint) if hint else ""), flush=True)


def az(*a):
    r = subprocess.run(("az",) + a, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout, r.stderr


def azj(*a):
    rc, out, err = az(*a, "-o", "json")
    try:
        return json.loads(out) if rc == 0 and out.strip() else None
    except Exception:
        return None


def finish():
    print()
    print(("ALL CHECKS PASSED (%d/%d)" % (passed, passed)) if failed == 0 else ("%d check(s) failed, %d passed" % (failed, passed)))
    sys.exit(1 if failed else 0)


def api(base, tok, method, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


sub = azj("account", "show") or {}
sid = sub.get("id", "")
print("Subscription %s" % sub.get("name"))
check("resource group %s exists (run ./setup.sh)" % RG, bool(azj("group", "show", "-n", RG)))
sas = azj("storage", "account", "list", "-g", RG) or []
sa = next((s for s in sas if s["name"].startswith("stgraf")), None)
check("the storage account to show exists", bool(sa))
if not sa:
    finish()

print("== The workspace ==")
gl = azj("resource", "list", "-g", RG, "--resource-type", "Microsoft.Dashboard/grafana") or []
check("an Azure Managed Grafana workspace exists in %s" % RG, len(gl) >= 1, "az grafana create")
if not gl:
    finish()
g = azj("grafana", "show", "-g", RG, "-n", gl[0]["name"]) or {}
props = g.get("properties", {})
check("it is provisioned and reachable over the public endpoint", props.get("provisioningState") == "Succeeded" and props.get("publicNetworkAccess", "Enabled") == "Enabled", str(props.get("provisioningState")))
ident = g.get("identity") or {}
pid = ident.get("principalId")
check("it has a system-assigned managed identity", ident.get("type", "").startswith("SystemAssigned") and bool(pid), "the identity is what Grafana uses to read Azure Monitor")

print("== Who may do what ==")
ras = azj("role", "assignment", "list", "--all", "--assignee", pid or "none") if pid else None
mr = [r for r in (ras or []) if r.get("roleDefinitionName") in ("Monitoring Reader", "Reader", "Contributor", "Owner", "Monitoring Contributor")]
mr = [r for r in mr if (RG in r["scope"].lower().replace("resourcegroups/" + RG, RG) or r["scope"].lower() == "/subscriptions/" + sid.lower() or r["scope"].lower() == sa["id"].lower() or ("/resourcegroups/" + RG) in r["scope"].lower())]
check("the workspace identity can read monitoring data of the storage account (Monitoring Reader on the resource group or the subscription)", bool(mr), "az role assignment create --role 'Monitoring Reader'")
if mr:
    check("it uses the least-privilege role Monitoring Reader (not Contributor or Owner)", any(r["roleDefinitionName"] == "Monitoring Reader" for r in mr), ", ".join(r["roleDefinitionName"] for r in mr))
tok = None
rc, out, err = az("account", "get-access-token", "--resource", GRAFANA_RESOURCE, "--query", "accessToken", "-o", "tsv")
if rc == 0:
    tok = out.strip()
oid = ""
try:
    p = tok.split(".")[1]
    oid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("oid", "")
except Exception:
    pass
mine = azj("role", "assignment", "list", "--all", "--assignee", oid) if oid else None
check("you hold the role Grafana Admin on the workspace", any(r.get("roleDefinitionName") == "Grafana Admin" and g.get("id", "x").lower().startswith(r["scope"].lower()) for r in (mine or [])), "az role assignment create --role 'Grafana Admin' ... (it can take a few minutes to take effect)")
base = props.get("endpoint", "")
check("Grafana accepts your sign-in (not 'No Grafana Role Assigned')", bool(base and tok) and api(base, tok, "GET", "/api/user")[0] == 200, "roles for new workspaces can take a few minutes; wait and run again")
if failed:
    finish()

print("== The dashboard ==")
code, res = api(base, tok, "GET", "/api/search?type=dash-db")
cand = None
for d in res or []:
    c, dj = api(base, tok, "GET", "/api/dashboards/uid/" + d["uid"])
    if c != 200:
        continue
    body = json.dumps(dj)
    if "grafana-azure-monitor-datasource" in body and "Transactions" in body and sa["name"] in body:
        cand = dj["dashboard"]
        break
check("a dashboard exists that charts the metric Transactions of YOUR storage account with the Azure Monitor data source", bool(cand), "build a panel: data source Azure Monitor, resource = the storage account, metric Transactions")
if cand:
    panels = [p for p in cand.get("panels", []) if p.get("type") != "row"]
    check("the dashboard has at least 2 panels and a title", len(panels) >= 2 and bool(cand.get("title")), "%d panels" % len(panels))
    tg = next((t for p in panels for t in p.get("targets", []) if "Transactions" in json.dumps(t)), {})
    ds = (tg.get("datasource") or {}).get("uid") or next((p.get("datasource", {}).get("uid") for p in panels if p.get("datasource")), None)
    print("== Does it show data? ==")
    now = int(time.time() * 1000)
    qbody = {"queries": [tg], "from": str(now - 3600 * 1000), "to": str(now)}
    if tg and ds:
        tg = dict(tg); tg["datasource"] = {"type": "grafana-azure-monitor-datasource", "uid": ds}
        qbody["queries"] = [tg]
        for i in range(8):
            c, r = api(base, tok, "POST", "/api/ds/query", qbody)
            frames = (((r or {}).get("results") or {}).get("A") or {})
            if c == 200 and not frames.get("error"):
                break
            time.sleep(20)
        err = frames.get("error", "") if r else "no answer"
        check("Grafana can query the metric through its own identity (no authorization error)", c == 200 and not err, (err or "HTTP %s" % c)[:200])
    else:
        check("the panel has a data source and a query", False)
finish()
