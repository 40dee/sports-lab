"""Experimental v2 replay. No provider calls, writes, or live model replacement."""
from __future__ import annotations
import io
import hashlib
import json
import math
from collections import Counter
import numpy as np
import pandas as pd

ENGINE_VERSION = "minutes-eb-vegas-v2-replay-2"
_env_ref = {}

def install(env, workflow_cls=None, workflow_obj=None):
    """Bind app helpers for offline replay only; leave live calculations unchanged."""
    global _env_ref
    _env_ref = env

def estimate_robust_minutes(sample, game_context=None, team_name=None):
    values = pd.to_numeric(sample.head(12)["MIN"], errors="coerce").dropna().to_numpy(float)
    if not len(values):
        raise ValueError("Missing valid minutes")
    if len(values) >= 5:
        med = float(np.median(values))
        band = max(4., 2. * 1.4826 * float(np.median(abs(values-med))))
        values = np.clip(values, max(1., med-band), min(46., med+band))
    weights = .85 ** np.arange(len(values))
    minutes = float(np.average(values, weights=weights))
    factor = 1.
    if isinstance(game_context, dict) and game_context.get("available"):
        try:
            spread = abs(float(game_context["home_spread"]))
            if math.isfinite(spread) and spread >= 8 and minutes >= 26:
                factor = max(.92, 1-.008*min(10., spread-7.5))
        except (KeyError, TypeError, ValueError):
            pass
    return max(1., min(44., minutes*factor)), factor

def shrunk_per_minute_rate(history, current_sample, stat, season, cutoff, frozen_result=None):
    frame = current_sample.copy()
    frame[stat] = pd.to_numeric(frame[stat], errors="coerce")
    frame = frame[frame[stat].ge(0) & frame[stat].notna()]
    recent = frame.head(15)
    if len(recent) < 3:
        raise ValueError("Fewer than three usable appearances")
    w = .90 ** np.arange(len(recent))
    recent_mins = float(recent.MIN.sum())
    rate = float(np.sum(w*recent[stat].to_numpy(float))/np.sum(w*recent.MIN.to_numpy(float)))
    # Exclude the recent window from the season anchor rather than counting it twice.
    outside = frame.iloc[len(recent):]
    anchor_minutes = float(outside.MIN.sum())
    anchor_total = float(outside[stat].sum())
    from season_anchors import summary_stat
    saved=frozen_result or {}
    if not anchor_minutes:
        totals=summary_stat(saved.get('current_season_totals'),season,stat,cutoff,recent)
        if totals:
            anchor_minutes=max(0.,totals['minutes']-float(recent.MIN.sum()))
            anchor_total=max(0.,totals['total']-float(recent[stat].sum()))
    year=int(str(season).split('-')[0]);prior_season=f"{year-1}-{str(year)[-2:]}"
    prior = history[(history.GAME_DATE < cutoff) & history.SEASON.eq(prior_season)].copy()
    prior[stat] = pd.to_numeric(prior[stat], errors="coerce")
    prior = prior[prior[stat].ge(0) & prior[stat].notna()]
    prior_mins = float(prior.MIN.sum())
    prior_total = float(prior[stat].sum())
    if not prior_mins:
        totals=summary_stat(saved.get('prior_season_totals'),prior_season,stat,cutoff)
        if totals:prior_mins,prior_total=totals['minutes'],totals['total']
    strength = {"PTS":220., "REB":260., "AST":260., "TOV":280.,
                "FG3M":420., "STL":420., "BLK":420.}.get(stat,280.)
    if prior_mins >= 60:
        anchor_total += strength * float(prior_total/prior_mins)
        anchor_minutes += strength
    if anchor_minutes:
        k = min(350., anchor_minutes)
        rate = (recent_mins*rate+k*anchor_total/anchor_minutes)/(recent_mins+k)
    if not math.isfinite(rate):
        raise ValueError("Invalid rate")
    return max(0., rate), len(recent)

def vegas_environment_factor(team_name, stat, game_context, advanced_frame, team_key_fn):
    if not isinstance(game_context,dict) or not game_context.get("available") or advanced_frame is None:
        return 1., False
    try:
        total, spread = float(game_context["total"]), float(game_context["home_spread"])
        own = team_key_fn(team_name)
        if own == team_key_fn(game_context["home"]):
            implied = (total-spread)/2
        elif own == team_key_fn(game_context["away"]):
            implied = (total+spread)/2
        else:
            return 1., False
        frame = advanced_frame.copy()
        frame["_key"] = frame.TEAM_NAME.map(team_key_fn)
        own_rows = frame[frame._key.eq(own)]
        if len(own_rows) != 1:
            return 1., False
        row = own_rows.iloc[0]
        ppg = float(row["OFF_RATING"])*float(row["PACE"])/100.
        if not all(math.isfinite(v) for v in (implied,ppg)) or ppg <= 50 or implied <= 0:
            return 1., False
        sensitivity = {"PTS":.45,"AST":.40,"FG3M":.35,"TOV":.25,"REB":.15,"STL":.15,"BLK":.10}.get(stat,.25)
        return max(.92,min(1.08,1+sensitivity*(implied/ppg-1))), True
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 1., False

def _discrete_tail_probability(mean, variance, line, direction):
    mu = max(.000001,float(mean))
    var = max(mu,float(variance))
    integer = abs(line-round(line)) < 1e-9
    boundary = int(round(line)) if integer else math.floor(line)
    if boundary > 1000:
        raise ValueError("Unsupported count threshold")
    if var > mu*1.05:
        r = mu*mu/(var-mu)
        p = r/(r+mu)
        def pmf(k):
            return math.exp(math.lgamma(k+r)-math.lgamma(r)-math.lgamma(k+1)+r*math.log(p)+k*math.log1p(-p))
    else:
        def pmf(k):
            return math.exp(k*math.log(mu)-mu-math.lgamma(k+1))
    less = sum(pmf(k) for k in range(max(0,boundary if integer else boundary+1)))
    tie = pmf(boundary) if integer and boundary >= 0 else 0.
    more = max(0.,1-less-tie)
    denominator = more+less
    return None if denominator < 1e-12 else max(.01,min(.99,(more if direction=="MORE" else less)/denominator))

def calibrated_hit_probability(all_values, forecast, line, direction, columns, is_fantasy=False, model_error_variance=0.):
    """Uncalibrated distributional estimate, conditional on playing and no tie."""
    values = pd.to_numeric(pd.Series(all_values),errors="coerce").dropna()
    n = len(values)
    if n < 3 or direction not in ("MORE","LESS"):
        return None, None
    variance = float(values.var(ddof=1)) + max(0.,float(model_error_variance))
    if not is_fantasy and len(columns)==1 and columns[0] in ("FG3M","STL","BLK"):
        return _discrete_tail_probability(forecast,variance,line,direction), "experimental-negbin-poisson"
    floor = max(1., .20*max(forecast,line,1.))
    sd = max(.5,math.sqrt((n*variance+5*floor*floor)/(n+5))*math.sqrt((max(6,n)+2)/max(6,n)))
    # Continuity correction excludes equality at integer count/combo lines.
    integer_count = not is_fantasy and abs(line-round(line))<1e-9
    cdf = lambda x:.5*(1+math.erf((x-forecast)/(sd*math.sqrt(2))))
    less = cdf(line-.5 if integer_count else line)
    more = 1-cdf(line+.5 if integer_count else line)
    p = (more if direction=="MORE" else less)/max(1e-12,more+less)
    return max(.01,min(.99,p)), "experimental-shrunk-normal"

def _batch_fetch(store, namespace, keys):
    """Bounded SELECT batches; never read unrelated full evidence."""
    keys=list(dict.fromkeys(str(k) for k in keys if k))
    found={}
    for offset in range(0,len(keys),50):
        chunk=keys[offset:offset+50]
        sql="SELECT key,payload FROM sports_records WHERE namespace=? AND key IN ("+",".join("?" for _ in chunk)+")"
        args=(namespace,*chunk)
        if callable(getattr(store,"query",None)):
            rows=store.query(sql,args,True)
        elif callable(getattr(store,"connection",None)):
            with store.connection() as con:
                if store.remote:sql=sql.replace("?","%s")
                rows=con.execute(sql,args).fetchall()
        else:
            for key in chunk:
                value=store.get(namespace,key)
                if value is not None:found[key]=value
            continue
        found.update((str(key),json.loads(payload)) for key,payload in rows)
    return found

def _replay_candidates(store, env, grades, max_samples, scope):
    reader=getattr(store,"research_forecast_items",None)
    light=reader() if callable(reader) else store.items("forecast")
    records=env["unique_forecasts"](light)
    records.sort(key=lambda item:(str(item[1].get("created_at","")),str(item[0])))
    records.sort(key=lambda item:str((item[1].get("card") or {}).get("Start (UTC)","")),reverse=True)
    selected=[];seen=set()
    for key,frozen in records:
        grade=grades.get(key) or {}
        card=frozen.get("card") or {}
        if grade.get("status")!="FINAL" or grade.get("outcome") not in ("WIN","LOSS"):continue
        platform=str(card.get("Platform","")).lower()
        tier=str(card.get("Line Type") or card.get("Line type") or "").lower()
        if scope=="standard_one_per_stat" and (platform!="prizepicks" or tier not in ("standard","regular")):continue
        if scope=="standard_one_per_stat":
            signature=(str(frozen.get("event_id")),str(card.get("Player","")).lower(),
                       tuple(sorted(env["market_columns"](card) or [])),str(card.get("Period","")).lower(),platform)
            if signature in seen:continue
            seen.add(signature)
        selected.append((key,frozen))
        if max_samples is not None and len(selected)>=max_samples:break
    return selected

def _saved_replays(store, candidates):
    """Bound memory to 50 forecasts and their saved inputs, including all-history runs."""
    for offset in range(0,len(candidates),50):
        batch=candidates[offset:offset+50]
        full=_batch_fetch(store,"forecast",[key for key,_ in batch])
        refs=set()
        for frozen in full.values():
            for field in ("games","advanced"):
                value=(frozen.get("result") or {}).get(field)
                if isinstance(value,dict) and value.get("source_ref"):
                    refs.add(value["source_ref"])
        cache={key:value.get("data") for key,value in _batch_fetch(store,"source",refs).items()}
        for key,_ in batch:
            yield key,full.get(key),cache

def saved_book_direction(market):
    """Use frozen exact-match evidence only, never today's odds for an old game."""
    if not isinstance(market, dict) or market.get('book') not in ('draftkings','fanduel','betmgm','bovada','pinnacle','consensus'):
        return None
    if market.get('match_method') != 'teams-time-player-stat-period-line':
        return None
    try:
        more, less = float(market['more']), float(market['less'])
        if not all(math.isfinite(p) and 0 < p < 1 for p in (more, less)):
            return None
        if abs(more + less - 1) > 1e-6:
            return None
        return 'NEUTRAL' if abs(more-less) < 1e-9 else 'MORE' if more > less else 'LESS'
    except (KeyError, TypeError, ValueError):
        return None


def run_backtest(store, env=None, max_samples=600, scope='standard_one_per_stat', diagnostics=False, include_rows=False):
    env = env or _env_ref
    raw = getattr(store,"_store",store)
    grades = dict(raw.items("grade"))
    if max_samples not in (None,300,600,1200):raise ValueError("Choose all history, 300, 600 or 1200 samples")
    if scope not in ("standard_one_per_stat","all_selectable"):raise ValueError("Unsupported replay scope")
    candidates=_replay_candidates(raw,env,grades,max_samples,scope)
    rows, excluded = [], Counter()
    # Per original line/market, never replace a frozen outcome with a later forecast.
    for key,frozen,cache in _saved_replays(raw,candidates):
        def resolve(value):
            if isinstance(value,dict) and value.get("source_ref"):
                return cache.get(value["source_ref"])
            return value
        if not frozen:
            excluded["missing_full_forecast"]+=1
            continue
        grade = grades.get(key) or {}
        if grade.get("status")!="FINAL" or grade.get("outcome") not in ("WIN","LOSS"):
            excluded["not_final_decided"]+=1
            continue
        card, result = frozen.get("card") or {}, frozen.get("result") or {}
        try:
            start = pd.to_datetime(card["Start (UTC)"],utc=True,errors="coerce")
            created = pd.to_datetime(frozen["created_at"],utc=True,errors="coerce")
            if pd.isna(start) or pd.isna(created) or created>=start:
                raise ValueError("late_or_missing_timestamp")
            if result.get("selection_allowed") is False or result.get("tie"):
                raise ValueError("original_not_selectable")
            if not env["pick_is_selectable"](card,result):
                raise ValueError("original_side_not_offered")
            actual,line,v1 = float(grade["actual"]),float(card["Line"]),float(result["projection"])
            if not all(math.isfinite(v) for v in (actual,line,v1)) or line<=0 or actual==line:
                raise ValueError("invalid_numbers_or_push")
            direction = result.get("direction")
            if direction not in ("MORE","LESS") or (grade["outcome"]=="WIN") != ((actual>line) if direction=="MORE" else (actual<line)):
                raise ValueError("inconsistent_original_grade")
            history_json = resolve(result.get("games"))
            if not isinstance(history_json,str):
                raise ValueError("missing_saved_logs")
            hist = pd.read_json(io.StringIO(history_json),orient="records")
            required = {"MIN","GAME_DATE","GAME_ID","SEASON"}
            if not required.issubset(hist.columns):
                raise ValueError("missing_history_columns")
            hist["GAME_DATE"]=pd.to_datetime(hist.GAME_DATE,utc=True,errors="coerce")
            hist["MIN"]=pd.to_numeric(hist.MIN,errors="coerce")
            hist=hist[(hist.GAME_DATE<created)&hist.MIN.between(.01,70)].drop_duplicates("GAME_ID").sort_values("GAME_DATE",ascending=False)
            hist=hist[hist.GAME_ID.astype(str).ne(str(frozen.get("event_id")))]
            season = frozen.get("season")
            if not season:
                raise ValueError("missing_season")
            sample=hist[hist.SEASON.eq(season)]
            if len(sample)<3:
                raise ValueError("insufficient_pregame_history")
            cols = env["market_columns"](card)
            if not cols or not set(cols).issubset(hist.columns):
                raise ValueError("unsupported_market")
            weights=env["fantasy_weights"](card) or {}
            from season_anchors import summary_stat
            year=int(str(season).split('-')[0]);prior_season=f"{year-1}-{str(year)[-2:]}"
            prior_summary_available=any(summary_stat(result.get('prior_season_totals'),prior_season,stat,created) for stat in cols)
            ctx=result.get("game_context") or {}
            # Only frozen context is used. Never fetch a current spread for an old game.
            if ctx.get("event_id") and str(ctx["event_id"])!=str(frozen.get("event_id")):
                ctx={}
            for field in ("fetched_at","checked_at"):
                if ctx.get(field):
                    stamp=pd.to_datetime(ctx[field],utc=True,errors="coerce")
                    if pd.isna(stamp) or stamp>created:
                        ctx={}
                        break
            adv=resolve(result.get("advanced"))
            adv_df=pd.DataFrame(adv["rows"]) if isinstance(adv,dict) and adv.get("rows") else None
            if isinstance(adv,dict) and adv.get("fetched_at"):
                stamp=pd.to_datetime(adv["fetched_at"],utc=True,errors="coerce")
                if pd.isna(stamp) or stamp>created:adv_df=None
            minutes,blowout_factor=estimate_robust_minutes(sample,ctx,result.get("team"))
            unadjusted_minutes,_=estimate_robust_minutes(sample,None,result.get("team"))
            comps={c["Stat"]:c for c in result.get("components",[]) if c.get("Stat")}
            projection=0.
            no_vegas_projection=0.
            for stat in cols:
                if stat not in comps:raise ValueError("missing_frozen_component")
                rate,_=shrunk_per_minute_rate(hist,sample,stat,season,created,result)
                pace=float(comps[stat].get("Pace factor",1))
                position=float(comps[stat].get("Position factor",1))
                vegas,has_vegas=vegas_environment_factor(result.get("team"),stat,ctx,adv_df,env["engine_team_key"])
                effective_pace=1+.4*(pace-1) if has_vegas else pace
                factor=max(.88,min(1.12,effective_pace*vegas))
                projection+=minutes*rate*factor*position*weights.get(stat,1.)
                no_vegas_projection+=minutes*rate*max(.88,min(1.12,pace))*position*weights.get(stat,1.)
            if not math.isfinite(projection):raise ValueError("invalid_candidate")
            if abs(projection-line)<1e-9:raise ValueError("candidate_no_direction")
            v2_dir="MORE" if projection>line else "LESS"
            if not env["pick_is_selectable"](card,dict(result,direction=v2_dir,projection=projection,tie=False)):
                raise ValueError("candidate_side_not_offered")
            values=env["score_frame"](hist.head(40),card).dropna()
            probability,_=calibrated_hit_probability(values,projection,line,v2_dir,cols,bool(weights))
            old_probability=result.get("estimated_hit_probability")
            old_probability=float(old_probability) if old_probability is not None else None
            if old_probability is not None and (not math.isfinite(old_probability) or not 0<=old_probability<=1):old_probability=None
            dispersion=env["score_frame"](sample.head(15),card).dropna().std(ddof=1)
            v2_gap=abs(projection-line)/dispersion if pd.notna(dispersion) and dispersion>1e-9 else None
            v1_gap=result.get("standardized_gap")
            try:
                v1_gap=float(v1_gap)
                if not math.isfinite(v1_gap):v1_gap=None
            except (TypeError,ValueError):v1_gap=None
            rows.append(dict(id=key,card=card,created=created.isoformat(),graded_at=grade.get("graded_at"),line=line,
                columns=cols,is_fantasy=bool(weights),history_values=values.tolist(),game_variance=float(values.var(ddof=1)),
                no_vegas_proj=no_vegas_projection,no_blowout_proj=projection*unadjusted_minutes/minutes,neither_proj=no_vegas_projection*unadjusted_minutes/minutes,
                v1_dir=direction,v2_dir=v2_dir,v1_gap=v1_gap,v2_gap=v2_gap,day=start.tz_convert("America/Los_Angeles").date().isoformat(),event=str(frozen.get("event_id")),
                player=str(result.get("player_id")),stat=result.get("stat_group",card.get("Stat")),
                platform=str(card.get("Platform","")).lower(),tier=str(card.get("Line Type","")).lower(),
                actual=actual,v1_proj=v1,v2_proj=projection,v1_win=int(grade["outcome"]=="WIN"),
                v2_win=int(actual>line if v2_dir=="MORE" else actual<line),
                v1_prob=old_probability,v2_prob=probability,book_direction=saved_book_direction(result.get("market")),prior_available=bool(hist.SEASON.ne(season).any()) or prior_summary_available,
                vegas_available=has_vegas))
        except (KeyError,TypeError,ValueError,ZeroDivisionError,OverflowError) as error:
            excluded[str(error)[:100]]+=1
    if not rows:
        return {"picks":0,"excluded":dict(excluded),"status":"no_replayable_records","version":ENGINE_VERSION}
    frame=pd.DataFrame(rows)
    # Alternate thresholds share one player-game/market of weight.
    frame["weight"]=1/frame.groupby(["day","event","player","stat","platform","tier"]).actual.transform("count")
    def summary(group):
        w=group.weight
        cal=group.dropna(subset=["v1_prob","v2_prob"])
        out={"picks":len(group),"player_game_markets":float(w.sum())}
        for version in ("v1","v2"):
            out[version+"_hit"]=float(np.average(group[version+"_win"],weights=w))
            out[version+"_mae"]=float(np.average(abs(group[version+"_proj"]-group.actual),weights=w))
            out[version+"_brier"]=float(np.average((cal[version+"_prob"]-cal[version+"_win"])**2,weights=cal.weight)) if len(cal) else None
        out["calibration_n"]=len(cal)
        return out
    result=summary(frame)
    by_direction=[]
    cutoffs=[]
    for version in ("v1","v2"):
        for direction,group in frame.groupby(version+"_dir"):
            by_direction.append(dict(version=version,direction=direction,picks=len(group),
                hit_rate=float(np.average(group[version+"_win"],weights=group.weight))))
        for field,thresholds in (("gap",(.25,.40,.60)),("prob",(.55,.58))):
            for threshold in thresholds:
                numeric=pd.to_numeric(frame[version+"_"+field],errors="coerce")
                group=frame[numeric.ge(threshold)]
                cutoffs.append(dict(version=version,metric=field,cutoff=threshold,picks=len(group),
                    hit_rate=float(np.average(group[version+"_win"],weights=group.weight)) if len(group) else None))
    edge_tiers=[]
    agreement=[]
    for version in ("v1","v2"):
        gap=pd.to_numeric(frame[version+"_gap"],errors="coerce")
        for label,mask in (("<0.25",gap.lt(.25)),("0.25–<0.50",gap.ge(.25)&gap.lt(.5)),
                           ("0.50+",gap.ge(.5)),("Unavailable",gap.isna())):
            group=frame[mask]
            edge_tiers.append(dict(version=version,tier=label,picks=len(group),
                hit_rate=float(np.average(group[version+"_win"],weights=group.weight)) if len(group) else None))
        for label,mask in (("Agrees",frame.book_direction.eq(frame[version+"_dir"])),
                           ("Disagrees",frame.book_direction.isin(["MORE","LESS"])&frame.book_direction.ne(frame[version+"_dir"])),
                           ("Neutral",frame.book_direction.eq("NEUTRAL")),
                           ("Unavailable",frame.book_direction.isna())):
            group=frame[mask]
            agreement.append(dict(version=version,agreement=label,picks=len(group),
                hit_rate=float(np.average(group[version+"_win"],weights=group.weight)) if len(group) else None))
    result["by_edge_tier"]=edge_tiers
    result["by_sportsbook_agreement"]=agreement
    result["sportsbook_note"]="Only frozen semantically verified sportsbook pairs or consensus are used. Missing or legacy unverified quotes remain unavailable; no current odds are fetched."
    result["by_direction"]=by_direction
    result["by_confidence_cutoff"]=cutoffs
    result["by_original_direction"]=[dict(direction=direction,**summary(group)) for direction,group in frame.groupby("v1_dir")]
    result["cohort_ids"]=sorted(frame.id.tolist())
    result["cohort_digest"]=hashlib.sha256(json.dumps(result["cohort_ids"]).encode()).hexdigest()
    result.update(version=ENGINE_VERSION,status="retrospective_diagnostic",excluded=dict(excluded),
        scope=scope,max_samples=max_samples,selected_candidates=len(candidates),
        by_platform_tier=[dict(platform=platform,tier=tier,**summary(group)) for (platform,tier),group in frame.groupby(["platform","tier"])],
        days=sorted(frame.day.unique()),prior_history_rows=int(frame.prior_available.sum()),
        vegas_rows=int(frame.vegas_available.sum()),by_stat=[dict(stat=stat,**summary(group)) for stat,group in frame.groupby("stat")],
        by_day=[dict(day=day,**summary(group)) for day,group in frame.groupby("day")],
        limitation="Uses only stored pregame logs and frozen matchup factors. Historical logs may contain only L15, preventing prior-season shrinkage. Fixed heuristics were not selected on an untouched holdout. Not a production promotion or proof of calibrated probabilities.")
    if include_rows:
        # Optional compact per-pick diagnostics for saved day/cohort attribution.
        result['replay_rows']=[{k:row[k] for k in ('id','v1_win','v2_win','v1_proj','v2_proj','v1_prob','v2_prob','v1_dir','v2_dir','actual')} for row in rows]
    if diagnostics:
        from replay_diagnostics import uncertainty_report,pra_ablation_report,health_inventory,sportsbook_inventory
        result["uncertainty_diagnostic"]=uncertainty_report(frame,calibrated_hit_probability)
        candidate=frame.copy()
        candidate['v2_proj']=candidate.v1_proj
        candidate['v2_dir']=candidate.v1_dir
        candidate['v2_win']=candidate.v1_win
        candidate['v2_prob']=[calibrated_hit_probability(row.history_values,row.v1_proj,row.line,row.v1_dir,row.columns,row.is_fantasy)[0] for _,row in candidate.iterrows()]
        result['v1_probability_candidate']=uncertainty_report(candidate,calibrated_hit_probability)
        result["pra_ablation"]=pra_ablation_report(frame)
        result["health_inventory"]=health_inventory(raw)
        result["sportsbook_inventory"]=sportsbook_inventory(raw)
        from recovered_market_backtest import recovered_market_report
        result["recovered_draftkings"]=recovered_market_report(raw,frame,env)
    return result

def print_cli_backtest(store, max_samples=600):
    print("V2_BACKTEST_JSON "+json.dumps(run_backtest(store,max_samples=max_samples,diagnostics=True),allow_nan=False),flush=True)

def render_backtest_panel(store, st):
    with st.expander("v2 Projection Backtest (offline · no API calls)"):
        st.caption("Experimental saved-data replay. Current live model stays active; no forecasts or grades are overwritten.")
        scope_label=st.selectbox("Backtest scope",["PrizePicks Standard: one line per player/stat/game","All selectable: separate platform and tier reports"],key="v2_bt_scope")
        scope="standard_one_per_stat" if scope_label.startswith("PrizePicks") else "all_selectable"
        limit=st.selectbox("Max recent candidates",[300,600,1200,None],index=1,format_func=lambda value: "All saved settled history" if value is None else str(value),key="v2_bt_limit")
        if st.button("Run v2 Backtest",key="run_v2_backtest_btn"):
            try:
                with st.spinner("Replaying saved pregame evidence..."):
                    st.session_state["_v2_backtest_result"]=run_backtest(store,max_samples=limit,scope=scope)
            except Exception as error:
                st.error("Replay failed: "+type(error).__name__+". Live projections remain unchanged.")
        result=st.session_state.get("_v2_backtest_result")
        if result:
            if not result["picks"]:
                st.info("No settled records with replayable pregame evidence.")
            else:
                st.write({k:result[k] for k in ("picks","player_game_markets","v1_hit","v2_hit","v1_mae","v2_mae","v1_brier","v2_brier")})
                st.dataframe(pd.DataFrame(result["by_platform_tier"]),hide_index=True,width="stretch")
                st.dataframe(pd.DataFrame(result["by_stat"]),hide_index=True,width="stretch")
                st.caption("Direction and cutoff rows use each version's own direction and scores; denominators can differ. Gap is separation, not a calibrated confidence or profit estimate.")
                st.dataframe(pd.DataFrame(result["by_direction"]),hide_index=True,width="stretch")
                st.dataframe(pd.DataFrame(result["by_confidence_cutoff"]),hide_index=True,width="stretch")
                st.dataframe(pd.DataFrame(result.get("by_edge_tier",[])),hide_index=True,width="stretch")
                st.dataframe(pd.DataFrame(result.get("by_sportsbook_agreement",[])),hide_index=True,width="stretch")
                st.caption(result.get("sportsbook_note",""))
                st.dataframe(pd.DataFrame(result["by_day"]),hide_index=True,width="stretch")
                st.caption(result["limitation"])
                st.caption(f'Prior-season evidence: {result["prior_history_rows"]} rows · Usable Vegas context: {result["vegas_rows"]} rows')
            st.write({"excluded":result["excluded"]})

