import json, os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

def main():
    day = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    parsed = urlsplit(os.getenv("TURSO_DATABASE_URL", "").replace("libsql://", "https://", 1))
    token = os.getenv("TURSO_AUTH_TOKEN", "")
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".turso.io") or not token:
        print(json.dumps({"state": "Database connection not configured for this read-only check"}))
        return 1
    sql = """SELECT namespace,key,payload,updated FROM sports_records
        WHERE (namespace='preparation' AND key='last')
        OR (namespace IN ('board_run','board_download','coverage','automation','daily_board_capture') AND key=?)
        OR (namespace='board' AND key='board')
        OR (namespace='source' AND key IN ('pp_health','pp_fantasy_daily_v1'))
        LIMIT 12"""
    body = json.dumps({"requests": [
        {"type":"execute","stmt":{"sql":sql,"args":[{"type":"text","value":day}],"want_rows":True}},
        {"type":"close"}]}).encode()
    data = None
    for attempt in range(2):
        try:
            request = Request("https://" + parsed.netloc + "/v2/pipeline", data=body,
                headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
            with urlopen(request, timeout=20) as response:
                reply = json.load(response)
            result = reply["results"][0]
            if result.get("type") != "ok":
                print(json.dumps({"state":"Database read rejected; no records changed"}))
                return 1
            data = result["response"]["result"].get("rows", [])
            break
        except Exception:
            if attempt:
                print(json.dumps({"state":"Database read unavailable after two attempts; no records changed"}))
                return 1
    records = {}
    source_details = {}
    times = {}
    for row in data:
        namespace, key, payload, updated = [cell.get("value") for cell in row]
        records[namespace] = json.loads(payload)
        if namespace=='source':
            value=json.loads(payload)
            source_details[key]={k:value.get(k) for k in ('state','remaining','http_status','pages_downloaded','pagination_complete','partial','next_cursor','updated_at','fetched_at')}
            source_details[key]['saved_items']=len(value.get('items') or [])
        times[namespace] = updated
    prep = records.get("preparation", {})
    run = records.get("board_run", {})
    download = records.get("board_download", {})
    ledger = records.get("automation", {})
    coverage = records.get("coverage", {})
    report = {
        "read_at": datetime.now(timezone.utc).isoformat(), "slate_day": day,
        "worker_stage": run.get("stage"), "worker_started": run.get("started_at"),
        "import_complete": run.get("import_complete"),
        "download": {key:download.get(key) for key in ("games_done","games_total","lines","state")},
        "preparation": {key:prep.get(key) for key in ("run_at","ready","total","pending","unavailable","attempted","state","current","finished_at","elapsed_seconds","newly_ready")},
        "progress_last_saved": times.get("preparation"),
        "coverage": {key:coverage.get(key) for key in ("ready","imported","upcoming_pending","upcoming_unavailable","missed_start","checked_at")},
        "last_completed_pass": {key:(ledger.get("preparation") or {}).get(key) for key in ("ready","total","pending","unavailable","finished_at")},
        "provider_capture_complete": (ledger.get("fallback") or {}).get("complete")
    }
    report["source_details"]=source_details
    report["capture"]=records.get("daily_board_capture")
    games = {}
    for card in (records.get("board", {}).get("v4_rows") or []):
        game = str(card.get("Game", "Unknown"))
        item = games.setdefault(game, {"lines": 0, "fantasy": 0, "latest_quote": "", "platforms": {}})
        platform = item["platforms"].setdefault(str(card.get("Platform")), {"lines": 0, "fantasy": 0, "latest_quote": ""})
        platform["lines"] += 1
        platform["fantasy"] += int("fantasy" in str(card.get("Stat", "")).lower() or "fantasy" in str(card.get("Stat Key", "")).lower())
        platform["latest_quote"] = max(platform["latest_quote"], str(card.get("Odds checked (UTC)") or ""))
        item["lines"] += 1
        item["fantasy"] += int("fantasy" in str(card.get("Stat", "")).lower() or "fantasy" in str(card.get("Stat Key", "")).lower())
        item["latest_quote"] = max(item["latest_quote"], str(card.get("Odds checked (UTC)") or ""))
    report["games"] = games
    stmt={"sql":"SELECT namespace,key,payload FROM sports_records WHERE namespace IN ('model','combo_model','pick_reliability_model','pick_reliability_evaluation') AND (namespace IN ('model','combo_model') OR key='latest')","want_rows":True}
    request=Request("https://"+parsed.netloc+"/v2/pipeline",
        data=json.dumps({"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]}).encode(),
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urlopen(request,timeout=30) as response:
        model_rows=json.load(response)["results"][0]["response"]["result"].get("rows",[])
    report["learning"]={}
    for row in model_rows:
        ns,key,payload=[cell["value"] for cell in row];v=json.loads(payload)
        report["learning"].setdefault(ns,{})[key]={k:v.get(k) for k in ("version","alpha","policy","status","activation","trained_through","evaluated_at","last_graded_date","graded_dates","examples","ranking_examples")}
    stmt={"sql":"SELECT namespace,key,payload FROM sports_records WHERE namespace IN ('combo_cohort','combo_grade','combo_played','combo_actual')","want_rows":True}
    request=Request("https://"+parsed.netloc+"/v2/pipeline",
        data=json.dumps({"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]}).encode(),
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urlopen(request,timeout=30) as response:
        result=json.load(response)["results"][0]
    if result.get("type")!="ok":raise RuntimeError("Combo read failed")
    combo_data={}
    for row in result["response"]["result"].get("rows",[]):
        ns,key,payload=[cell["value"] for cell in row]
        combo_data.setdefault(ns,{})[key]=json.loads(payload)
    sizes={str(n):{"hits":0,"misses":0,"pending":0,"review":0,"days":set()} for n in range(2,7)}
    for cohort in combo_data.get("combo_cohort",{}).values():
        row=sizes.get(str(cohort.get("size")))
        if row is None:continue
        row["days"].add(cohort.get("day"))
        for key in (cohort.get("policies") or {}).get("balanced-v1",[]):
            state=(combo_data.get("combo_grade",{}).get(key) or {}).get("state","PENDING")
            row[{"ALL_HIT":"hits","MISSED":"misses","VOID_REVIEW":"review"}.get(state,"pending")]+=1
    for row in sizes.values():
        row["days"]=len(row["days"])
        row["hit_rate"]=row["hits"]/(row["hits"]+row["misses"]) if row["hits"]+row["misses"] else None
    report["combo_hit_rates_balanced"]=sizes
    played={v["combo_id"] for v in combo_data.get("combo_played",{}).values() if v.get("active")}
    played.update(v["combo_id"] for v in combo_data.get("combo_actual",{}).values())
    report["unique_played_combos"]=len(played)
    for saved_row in data:
        ns,key,payload,_=[cell.get("value") for cell in saved_row]
        if ns=="source" and key=="pp_fantasy_daily_v1":
            report["fantasy_offer_examples"]=[item for item in (json.loads(payload).get("items") or [])
                if any(name in str(item.get("player","")).lower() for name in ("mikal bridges","jalen brunson"))]
    fields=("Player","Line","Line Type","Direction","Available Sides","Tier Fetched At","Tier Observed At","Tier Source","Tier Projection ID","Start (UTC)")
    report["fantasy_offer_cards"]=[{k:c.get(k) for k in fields} for c in (records.get("board",{}).get("v4_rows") or [])
        if "fantasy" in str(c.get("Stat","")).lower() and any(name in str(c.get("Player","")).lower() for name in ("mikal bridges","jalen brunson"))]
    stmt={"sql":"SELECT namespace,key,payload FROM sports_records WHERE namespace='combo_ranking' OR (namespace IN ('combo','combo_suggestions') AND json_extract(payload,'$.day')=?)","args":[{"type":"text","value":day}],"want_rows":True}
    request=Request("https://"+parsed.netloc+"/v2/pipeline",
        data=json.dumps({"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]}).encode(),
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urlopen(request,timeout=30) as response:
        rows=json.load(response)["results"][0]["response"]["result"].get("rows",[])
    current_combos={};suggestions={};ranking={}
    for row in rows:
        ns,key,payload=[cell["value"] for cell in row]
        {"combo":current_combos,"combo_suggestions":suggestions,"combo_ranking":ranking}[ns][key]=json.loads(payload)
    report["combo_ranking_setting"]=ranking
    report["current_suggestions"]={}
    selected_ids=set()
    for key,value in suggestions.items():
        report["current_suggestions"][key]={k:value.get(k) for k in ("created_at","eligible_players","tier_mode","quote_mode","sizes","excluded")}
        for ids in (value.get("sizes") or {}).values():selected_ids.update(ids)
    report["selected_combo_examples"]=[value for key,value in current_combos.items() if key in selected_ids]
    selected_forecasts={leg["forecast_id"] for combo in report["selected_combo_examples"] for leg in combo["legs"]}
    if selected_forecasts:
        stmt={"sql":"SELECT key,payload FROM sports_records WHERE namespace='forecast' AND key IN ("+",".join("?" for _ in selected_forecasts)+")","args":[{"type":"text","value":key} for key in selected_forecasts],"want_rows":True}
        request=Request("https://"+parsed.netloc+"/v2/pipeline",
            data=json.dumps({"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]}).encode(),
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        with urlopen(request,timeout=30) as response:
            rows=json.load(response)["results"][0]["response"]["result"].get("rows",[])
        report["selected_forecast_features"]={}
        for row in rows:
            key,payload=[cell["value"] for cell in row];frozen=json.loads(payload)
            report["selected_forecast_features"][key]={k:(frozen.get("result") or {}).get(k) for k in ("minutes","baseline","projection","ranking_games","standardized_gap","stat_group","components")}
    for row in model_rows:
        ns,key,payload=[cell["value"] for cell in row];v=json.loads(payload)
        if ns=="pick_reliability_evaluation":
            report["minutes_patterns"]=[p for p in v.get("patterns",[]) if p.get("feature")=="minutes"]
            report["ranking_evaluation"]=v.get("folds")
    print("BOARD_PROGRESS_JSON " + json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
