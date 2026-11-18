"""Replay saved evidence only; never run app navigation or provider imports."""
import ast,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import projection_v2
source=Path("app.py").read_text()
nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ""):
        break
    nodes.append(node)
env={"__name__":"v2_offline_replay","__file__":"app.py"}
exec(compile(ast.Module(body=nodes,type_ignores=[]),"app.py","exec"),env)
if not env["_database_online"]:
    raise SystemExit("Permanent storage unavailable")
projection_v2.install(env)
projection_v2.print_cli_backtest(env["workflow_store"],max_samples=None)

# Bounded 600-candidate Standard replay; batched saved-evidence reads.

# Report stat, direction, gap/probability cutoffs and exact replay cohort IDs.


# Full saved settled Standard cohort, disjoint edge tiers and frozen sportsbook agreement.

# Chronological uncertainty, PRA ablations and saved ESPN health inventory.

# Recovered historical DraftKings probability buckets and model alignment (no API calls).
