# PyChain

PyChain is a Python-first **EVM contract compiler**. Developers write contract logic in a typed, Python-like subset; PyChain validates that subset, lowers it to a chain-oriented intermediate representation, generates Solidity, and compiles the result with a pinned Solidity compiler.

It does not execute arbitrary Python or translate ordinary Web2/Python applications wholesale. The supported syntax is a small smart-contract language with Python ergonomics and explicit EVM semantics.

## Upload a Python file

Use **Upload .py** above the editor to load a local Python file (up to 100 KB). The browser reads the file into the editor and sends its source to the same compiler endpoint used by the editor; files are not persisted by PyChain. Click **Compile contract** to generate Solidity and, when local `solc` is installed, an ABI and bytecode artifact.

Any `.py` file can be selected, but only code in PyChain's supported smart-contract subset can be converted. Regular Python features such as imports, filesystem access, HTTP clients, classes without `@contract`, or dynamic execution have no equivalent safe EVM translation and return source diagnostics.

## Run locally

Requirements: Python 3.11+ and Node.js/npm.

```powershell
cd "D:\My Code\PyChain"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm ci
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000. `npm ci` installs the exact Solidity compiler version used for validation. If it is not installed, PyChain still generates Solidity but clearly marks target compilation as not configured.

Run compiler and Solidity integration tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

## First usable contract language slice

See the embedded Vault example in the editor. The current compiler supports:

- `@contract` classes with typed state (`uint`, `int`, sized integers, `bool`, `address`, `bytesN`, `string`, `bytes`)
- one-dimensional state mappings, such as `balances: mapping[address, uint] = {}`
- `@event` declarations and `emit(EventName, ...)`
- explicitly exposed typed methods (`@external`, `@view`, or `@pure`) plus `@payable`
- EVM context through `msg.sender`, `msg.value`, `block.timestamp`, and `balance()`
- `require(condition, "message")` and Python `assert` as Solidity reverts
- local typed variables, assignments, integer arithmetic, comparisons, `and`/`or`/`not`, and `if`/`else`
- `send_eth(recipient, amount)`, emitted as a checked low-level ETH call; contracts using it receive an automatically generated reentrancy lock on their state-changing external methods
- generated Solidity, a serialized IR, source-line comments, security heuristics, a Hardhat deployment smoke-test starter, ABI, and bytecode when `solc` is available

The Solidity target compiler is authoritative for Solidity validity. PyChain diagnostics and the generated smoke test are not a security audit or proof that application logic is correct.

State fields currently generate public Solidity getters; do not put secrets in contract state. Solidity's checked arithmetic can revert on underflow/overflow, which is not Python's arbitrary-precision integer behavior.

## Example

```python
from pychain import contract, event, external, payable, view, require, emit, send_eth, mapping, msg

@contract
class Vault:
    balances: mapping[address, uint] = {}

    @event
    def Deposited(account: address, amount: uint):
        ...

    @payable
    @external
    def deposit(self):
        require(msg.value > 0, "deposit must be positive")
        self.balances[msg.sender] += msg.value
        emit(Deposited, msg.sender, msg.value)

    @external
    def withdraw(self, amount: uint):
        require(self.balances[msg.sender] >= amount, "insufficient balance")
        self.balances[msg.sender] -= amount
        send_eth(msg.sender, amount)

    @view
    def balance_of(self, account: address) -> uint:
        return self.balances[account]
```

## Deliberate limitations

PyChain rejects arbitrary imports/calls, loops, inheritance, user-defined helpers, external contract calls, dynamic Python behavior, nested mappings, untyped local variables, and unsupported AST constructs. Loop support is excluded because unbounded iteration can consume unbounded gas. `send_eth` permits an external call, so contract invariants and effects ordering still need careful review even though PyChain adds a reentrancy lock.

There is currently one implemented backend: Solidity/EVM. The Rust selector is intentionally marked as unimplemented. Gas estimation and deployment are not implemented. Security diagnostics are conservative heuristics and can have false positives or miss vulnerabilities.

## Architecture

```text
Python-like source → AST validation → typed PyChain IR → Solidity backend
                                                   └──→ compatibility/security diagnostics
Solidity backend → pinned solc → Solidity + ABI + bytecode
```

See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the product rationale and staged roadmap.
