import json
from pathlib import Path
import unittest

from backend.compiler import CompileFailure, compile_source
from backend.main import CompileRequest, analyze_source, compile_contract, ethers_bundle
from backend.solidity import validate_solidity
from fastapi import HTTPException


VAULT_SOURCE = '''from pychain import contract, event, external, payable, view, require, emit, send_eth, mapping, msg

@contract
class Vault:
    balances: mapping[address, uint] = {}
    total_deposited: uint = 0

    @event
    def Deposited(account: address, amount: uint):
        ...

    @event
    def Withdrawn(account: address, amount: uint):
        ...

    @payable
    @external
    def deposit(self):
        require(msg.value > 0, "deposit must be positive")
        self.balances[msg.sender] += msg.value
        self.total_deposited += msg.value
        emit(Deposited, msg.sender, msg.value)

    @external
    def withdraw(self, amount: uint):
        require(amount > 0, "withdrawal must be positive")
        require(self.balances[msg.sender] >= amount, "insufficient balance")
        self.balances[msg.sender] -= amount
        self.total_deposited -= amount
        send_eth(msg.sender, amount)
        emit(Withdrawn, msg.sender, amount)

    @view
    def balance_of(self, account: address) -> uint:
        return self.balances[account]
'''

TEMPLATE_CATALOG = Path(__file__).resolve().parents[1] / "frontend" / "templates.json"


class CompilerTests(unittest.TestCase):
    def test_compiles_common_vault_logic_to_solidity(self):
        result = compile_source(VAULT_SOURCE)
        solidity = result["solidity"]
        self.assertIn("mapping(address => uint256) public balances;", solidity)
        self.assertIn("function deposit() external payable _pychainNonReentrant", solidity)
        self.assertIn("emit Deposited(msg.sender, msg.value);", solidity)
        self.assertIn("balances[msg.sender] -= amount;", solidity)
        self.assertIn("function balance_of(address account) external view returns (uint256)", solidity)
        self.assertIn("payable(recipient).call{value: amount}", solidity)
        self.assertEqual(result["ir"]["name"], "Vault")
        self.assertEqual(len(result["ir"]["events"]), 2)

    def test_solidity_backend_compiles_generated_artifact(self):
        result = validate_solidity(compile_source(VAULT_SOURCE)["solidity"])
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["artifacts"]["abi"])
        self.assertTrue(result["artifacts"]["bytecode"].startswith("0x"))

    def test_rejects_unbounded_loops(self):
        source = "@contract\nclass Bad:\n    @external\n    def run(self):\n        while True:\n            pass\n"
        with self.assertRaises(CompileFailure) as raised:
            compile_source(source)
        self.assertIn("unbounded gas", raised.exception.diagnostics[0]["message"])

    def test_methods_must_be_explicit_entry_points(self):
        source = "@contract\nclass Bad:\n    def hidden_by_accident(self):\n        pass\n"
        with self.assertRaises(CompileFailure) as raised:
            compile_source(source)
        self.assertTrue(any(item["code"] == "PY103" for item in raised.exception.diagnostics))

    def test_rejects_arbitrary_imports_and_calls(self):
        cases = [
            "import os\n@contract\nclass Bad:\n    pass\n",
            "@contract\nclass Bad:\n    @external\n    def run(self):\n        print(1)\n",
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(CompileFailure):
                compile_source(source)

    def test_rejects_msg_value_on_nonpayable_method(self):
        source = '''@contract
class Bad:
    @external
    def read(self) -> uint:
        return msg.value
'''
        with self.assertRaises(CompileFailure) as raised:
            compile_source(source)
        self.assertTrue(any(item["code"] == "PY040" for item in raised.exception.diagnostics))

    def test_view_and_pure_mutations_are_rejected(self):
        cases = [
            "@contract\nclass Bad:\n    value: uint = 0\n    @view\n    def mutate(self):\n        self.value += 1\n",
            "@contract\nclass Bad:\n    value: uint = 0\n    @pure\n    def read(self) -> uint:\n        return self.value\n",
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(CompileFailure):
                compile_source(source)

    def test_typed_locals_and_branching_compile(self):
        source = '''@contract
class MathBox:
    @pure
    def larger(self, left: uint, right: uint) -> uint:
        result: uint = left
        if right > left:
            result = right
        return result
'''
        solidity = compile_source(source)["solidity"]
        self.assertIn("uint256 result = left;", solidity)
        self.assertIn("if ((right > left))", solidity)

    def test_constructor_and_explicit_address_cast_compile(self):
        source = '''@contract
class Ownable:
    owner: address
    def __init__(self, initial_owner: address):
        require(initial_owner != address(0), "zero owner")
        self.owner = initial_owner
'''
        result = compile_source(source)
        validation = validate_solidity(result["solidity"])
        self.assertEqual(validation["status"], "passed", validation)
        self.assertIn("constructor(address initial_owner)", result["solidity"])

    def test_rejects_negative_values_in_unsigned_storage(self):
        source = "@contract\nclass Bad:\n    amount: uint = -1\n"
        with self.assertRaises(CompileFailure) as raised:
            compile_source(source)
        codes = {item["code"] for item in raised.exception.diagnostics}
        self.assertIn("PY100", codes)

    def test_returns_are_type_checked(self):
        source = '''@contract
class Bad:
    @pure
    def answer(self) -> bool:
        return 42
'''
        with self.assertRaises(CompileFailure):
            compile_source(source)

    def test_non_evm_target_fails_explicitly(self):
        with self.assertRaises(CompileFailure) as raised:
            compile_source("@contract\nclass C:\n    pass\n", "rust")
        self.assertEqual(raised.exception.diagnostics[0]["code"], "PY100")

    def test_http_compile_returns_target_validation(self):
        body = compile_contract(CompileRequest(source=VAULT_SOURCE))
        self.assertEqual(body["solidity_validation"]["status"], "passed")
        self.assertIn("bytecode", body["solidity_validation"]["artifacts"])

    def test_http_compile_returns_source_diagnostics(self):
        request = CompileRequest(source="@contract\nclass Bad:\n    @external\n    def loop(self):\n        while True:\n            pass\n")
        with self.assertRaises(HTTPException) as raised:
            compile_contract(request)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["diagnostics"][0]["code"], "PY052")

    def test_http_compile_preserves_uploaded_python_filename(self):
        body = compile_contract(CompileRequest(source=VAULT_SOURCE, source_name="uploaded_vault.py"))
        self.assertEqual(body["source_name"], "uploaded_vault.py")

    def test_http_compile_rejects_non_python_upload_name(self):
        with self.assertRaises(HTTPException) as raised:
            compile_contract(CompileRequest(source=VAULT_SOURCE, source_name="uploaded_vault.txt"))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["diagnostics"][0]["code"], "PY101")

    def test_http_analyze_reports_compatible_contract_summary(self):
        result = analyze_source(CompileRequest(source=VAULT_SOURCE, source_name="uploaded_vault.py"))
        self.assertTrue(result["compatible"])
        self.assertEqual(result["source_name"], "uploaded_vault.py")
        self.assertEqual(result["contract"]["name"], "Vault")
        self.assertEqual(result["contract"]["state_fields"], 2)
        self.assertEqual(result["contract"]["functions"], 3)
        self.assertEqual(result["contract"]["events"], 2)

    def test_http_analyze_returns_diagnostics_for_unsupported_python(self):
        request = CompileRequest(source="import os\nprint('not on chain')\n", source_name="ordinary.py")
        with self.assertRaises(HTTPException) as raised:
            analyze_source(request)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertTrue(raised.exception.detail["diagnostics"])

    def test_solidity_artifact_includes_compiler_settings(self):
        result = validate_solidity(compile_source(VAULT_SOURCE)["solidity"])
        self.assertEqual(result["status"], "passed", result)
        self.assertIn("outputSelection", result["settings"])

    def test_wallet_deployment_library_is_served_from_pinned_local_dependency(self):
        response = ethers_bundle()
        self.assertTrue(str(response.path).endswith("ethers.umd.min.js"))
        self.assertEqual(response.media_type, "text/javascript")

    def test_beginner_templates_compile_to_valid_solidity(self):
        templates = json.loads(TEMPLATE_CATALOG.read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in templates}, {"voting", "escrow", "points"})
        for template in templates:
            with self.subTest(template=template["id"]):
                self.assertTrue(template["summary"])
                self.assertTrue(template["behavior"])
                self.assertTrue(template["try"])
                self.assertTrue(template["caution"])
                compiled = compile_source(template["source"])
                validation = validate_solidity(compiled["solidity"])
                self.assertEqual(validation["status"], "passed", validation)


if __name__ == "__main__":
    unittest.main()
