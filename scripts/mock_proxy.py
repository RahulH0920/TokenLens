"""FastAPI Logging Proxy and Query API for LLM FinOps (TRD 2.5)."""

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Dict, Any, Optional

from fastapi import FastAPI, Header, Query, HTTPException
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.models import RequestRecord
from core.attribution import AttributionParser
from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.reconciliation import ReconciliationEngine

app = FastAPI(title="LLM FinOps Proxy & Query API", version="1.0")

DATA_DIR = BASE_DIR / "data"
importer = DataImporter()
pricing_records = importer.load_pricing(DATA_DIR / "model_pricing.csv")
requests_records, rejects, stats = importer.load_requests(DATA_DIR / "sample_requests.csv")

engine = CostEngine()
engine.load_pricing_records(pricing_records)
priced = engine.process_requests(requests_records)
df = engine.get_priced_dataframe(priced)
attr_parser = AttributionParser()


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list
    max_tokens: Optional[int] = 500


@app.post("/v1/proxy/{provider}")
def proxy_completion(
    provider: str,
    body: ChatCompletionRequest,
    x_team: Optional[str] = Header(None),
    x_feature: Optional[str] = Header(None),
    x_user: Optional[str] = Header(None),
    x_env: Optional[str] = Header("production")
):
    """Forward LLM request and asynchronously attribute and log usage metadata."""
    parsed_attr = attr_parser.parse_attribution({
        "x-team": x_team,
        "x-feature": x_feature,
        "x-user": x_user,
        "x-env": x_env
    })

    # Mock response token usage
    in_tok = sum(len(str(m.get("content", ""))) // 4 for m in body.messages) or 120
    out_tok = min(body.max_tokens or 200, 150)

    req = RequestRecord(
        request_id=f"proxy_req_{int(datetime.now(timezone.utc).timestamp()*1000)}",
        timestamp_utc=datetime.now(timezone.utc),
        team=parsed_attr["team"],
        feature=parsed_attr["feature"],
        user_id=parsed_attr["user_id"],
        provider=provider,
        model=body.model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        status="success",
        env=parsed_attr["env"],
        source="proxy"
    )

    priced_req = engine.calculate_request_cost(req)

    return {
        "id": req.request_id,
        "object": "chat.completion",
        "model": body.model,
        "choices": [{"message": {"role": "assistant", "content": "Sample LLM Response"}}],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok
        },
        "finops_attribution": {
            "team": priced_req.team,
            "feature": priced_req.feature,
            "user_id": priced_req.user_id,
            "cost_usd": str(priced_req.total_cost_usd),
            "missing_price": priced_req.missing_price
        }
    }


@app.get("/summary")
def get_summary():
    """KPI strip totals."""
    return {
        "total_spend_usd": round(float(df["total_cost_usd"].sum()), 4),
        "total_requests": len(df),
        "total_input_tokens": int(df["input_tokens"].sum()),
        "total_output_tokens": int(df["output_tokens"].sum()),
        "unattributed_spend_usd": round(float(df[df["is_unattributed"]]["total_cost_usd"].sum()), 4)
    }


@app.get("/breakdown")
def get_breakdown(by: str = Query("team", regex="^(team|feature|model|user_id)$")):
    """Grouped cost and tokens breakdown."""
    grouped = df.groupby(by).agg(
        cost_usd=("total_cost_usd", "sum"),
        request_count=("request_id", "count"),
        input_tokens=("input_tokens", "sum"),
        output_tokens=("output_tokens", "sum")
    ).reset_index()
    return grouped.to_dict(orient="records")


@app.get("/validate")
def get_validation():
    """Run reconciliation checks and return validation report."""
    import json
    golden_file = DATA_DIR / "golden_validation.json"
    with open(golden_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    reconciler = ReconciliationEngine()
    checks = reconciler.run_reconciliation(df, manifest, stats, rejects)
    return [c.model_dump() for c in checks]


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
