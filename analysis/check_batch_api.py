"""batch API가 clip 정보를 돌려주는지 HTTP로 확인한다."""
import json
import random
import urllib.request
from pathlib import Path

path = Path("public/dashboard/100m/11620_관악구.geojson")
with path.open(encoding="utf-8") as f:
    feats = [ft for ft in json.load(f)["features"] if ft["properties"].get("grid_id")]

random.seed(20260803)
ids = [str(ft["properties"]["grid_id"]) for ft in random.sample(feats, 120)]

body = json.dumps({
    "grid_ids": ids,
    "changes": {"impervious_ratio": -0.05},
    "couple_land_cover": True,
}).encode()

req = urllib.request.Request(
    "http://localhost:8000/api/simulate/batch",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=180) as resp:
    data = json.load(resp)

print("count                 ", data.get("count"))
print("valid_count           ", data.get("valid_count"))
print("clipped_count         ", data.get("clipped_count"))
print("mean_delta_c          ", data.get("mean_delta_c"))
print("mean_delta_c_unclipped", data.get("mean_delta_c_unclipped"))
m, u = data.get("mean_delta_c"), data.get("mean_delta_c_unclipped")
if m is not None and u is not None:
    print(f"차이                   {m - u:+.3f}℃  (동률밴드 0.132)")
