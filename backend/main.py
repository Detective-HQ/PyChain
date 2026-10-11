from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .compiler import CompileFailure, compile_source
from .solidity import validate_solidity

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="PyChain Compiler", version="0.1.0")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")


class CompileRequest(BaseModel):
    source: str = Field(min_length=1, max_length=100_000)
    target: str = "solidity"
    source_name: str = Field(default="contract.py", min_length=1, max_length=255)


@app.get("/")
def workspace():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": "pychain"}


@app.post("/api/compile")
def compile_contract(request: CompileRequest):
    if not request.source_name.lower().endswith(".py"):
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
    try:
        result = compile_source(request.source, request.target)
        result["source_name"] = request.source_name
        result["solidity_validation"] = validate_solidity(result["solidity"])
        return result
    except CompileFailure as exc:
        raise HTTPException(status_code=422, detail={"diagnostics": exc.diagnostics}) from exc
