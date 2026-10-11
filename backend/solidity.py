"""Optional invocation of a locally installed, pinned Solidity compiler."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOLCJS = ROOT / "node_modules" / "solc" / "solc.js"


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout + result.stderr
    match = re.search(r"(?:Version:\s*)?(0\.\d+\.\d+(?:\+[^\s]+)?)", text)
    return match.group(1) if match else None


def validate_solidity(source: str) -> dict[str, Any]:
    """Compile output and return ABI/bytecode when the local solc is available."""
    node = shutil.which("node")
    native_solc = shutil.which("solc")
    if SOLCJS.exists() and node:
        command = [node, str(SOLCJS)]
    elif native_solc:
        command = [native_solc]
    else:
        return {
            "status": "not_configured",
            "compiler": None,
            "message": "Install project dependencies (`npm ci`) to compile and validate generated Solidity.",
            "errors": [],
            "warnings": [],
            "artifacts": None,
        }

    version = _version(command)
    standard_input = {
        "language": "Solidity",
        "sources": {"PyChain.sol": {"content": source}},
        "settings": {
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    try:
        result = subprocess.run(
            command + ["--standard-json"],
            input=json.dumps(standard_input),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "compiler": version,
            "message": "Solidity compilation timed out.",
            "errors": [{"severity": "error", "message": "The target compiler exceeded its 40-second limit."}],
            "warnings": [],
            "artifacts": None,
        }
    except OSError as exc:
        return {
            "status": "failed",
            "compiler": version,
            "message": "The Solidity compiler could not be started.",
            "errors": [{"severity": "error", "message": str(exc)}],
            "warnings": [],
            "artifacts": None,
        }

    output_text = result.stdout
    first_json = output_text.find("{")
    try:
        output = json.loads(output_text[first_json:]) if first_json >= 0 else {}
    except json.JSONDecodeError:
        output = {}
    raw_diagnostics = output.get("errors", [])
    errors = [
        {"severity": "error", "message": item.get("formattedMessage", item.get("message", "Solidity compiler error"))}
        for item in raw_diagnostics if item.get("severity") == "error"
    ]
    warnings = [
        {"severity": "warning", "message": item.get("formattedMessage", item.get("message", "Solidity compiler warning"))}
        for item in raw_diagnostics if item.get("severity") == "warning"
    ]
    contracts = output.get("contracts", {}).get("PyChain.sol", {})
    artifacts = None
    if not errors and contracts:
        contract_name, contract_output = next(iter(contracts.items()))
        artifacts = {
            "contractName": contract_name,
            "abi": contract_output.get("abi", []),
            "bytecode": "0x" + contract_output.get("evm", {}).get("bytecode", {}).get("object", ""),
        }
    status = "passed" if not errors and artifacts else "failed"
    message = "Generated Solidity compiled successfully." if status == "passed" else "Generated Solidity did not compile."
    if result.returncode != 0 and not errors:
        errors.append({"severity": "error", "message": result.stderr.strip() or "Solidity compiler exited unsuccessfully."})
        status = "failed"
    return {
        "status": status,
        "compiler": version,
        "message": message,
        "errors": errors,
        "warnings": warnings,
        "artifacts": artifacts,
    }
