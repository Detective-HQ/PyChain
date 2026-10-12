from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .compiler import CompileFailure, compile_source
from .solidity import validate_solidity

ROOT = Path(__file__).resolve().parents[1]
ETHERS_BUNDLE = ROOT / "node_modules" / "ethers" / "dist" / "ethers.umd.min.js"
app = FastAPI(title="PyChain Compiler", version="0.1.0")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")


class CompileRequest(BaseModel):
    source: str = Field(min_length=1, max_length=100_000)
    target: str = "solidity"
    source_name: str = Field(default="contract.py", min_length=1, max_length=255)


@app.get("/")
def workspace():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/vendor/ethers.js")
def ethers_bundle():
    if not ETHERS_BUNDLE.is_file():
        raise HTTPException(status_code=503, detail="Run `npm ci` to enable wallet deployment support.")
    return FileResponse(ETHERS_BUNDLE, media_type="text/javascript")


@app.get("/health")
def health():
    return {"status": "ok", "service": "pychain"}


def validate_source_name(source_name: str) -> None:
    if source_name.lower().endswith(".py"):
        return
    raise HTTPException(
        status_code=422,
        detail={"diagnostics": [{
            "line": 1,
            "column": 1,
            "severity": "error",
            "code": "PY101",
            "message": "PyChain accepts Python source files ending in `.py`.",
        }]},
    )


@app.post("/api/analyze")
def analyze_source(request: CompileRequest):
    """Check whether a Python source file fits PyChain's contract language."""
    validate_source_name(request.source_name)
    try:
        result = compile_source(request.source, request.target)
        ir = result["ir"]
        return {
            "compatible": True,
            "source_name": request.source_name,
            "contract": {
                "name": ir["name"],
                "state_fields": len(ir["state"]),
                "functions": len(ir["functions"]),
                "events": len(ir["events"]),
                "features": ir["features"],
            },
            "diagnostics": result["diagnostics"],
            "message": "This source fits the supported PyChain subset. Review warnings before generating an artifact.",
        }
    except CompileFailure as exc:
        raise HTTPException(status_code=422, detail={"diagnostics": exc.diagnostics}) from exc


@app.post("/api/compile")
def compile_contract(request: CompileRequest):
    validate_source_name(request.source_name)
    try:
        result = compile_source(request.source, request.target)
        result["source_name"] = request.source_name
        result["solidity_validation"] = validate_solidity(result["solidity"])
        return result
    except CompileFailure as exc:
        raise HTTPException(status_code=422, detail={"diagnostics": exc.diagnostics}) from exc
