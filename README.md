# PyChain

PyChain is a Python-first **EVM contract compiler**. Developers write contract logic in a typed, Python-like subset; PyChain validates that subset, lowers it to a chain-oriented intermediate representation, generates Solidity, and compiles the result with a pinned Solidity compiler.

It does not execute arbitrary Python or translate ordinary Web2/Python applications wholesale. The supported syntax is a small smart-contract language with Python ergonomics and explicit EVM semantics.

## Upload a Python file

Use **Upload .py** or drop a local Python file into the editor (up to 100 KB). The browser reads the file into the editor and sends its source to the compiler; files are not persisted by PyChain. Click **Check compatibility** for a source-level preview, then **Compile contract** to generate Solidity and, when local `solc` is installed, an ABI and bytecode artifact. Editing the source invalidates the previous artifact so it cannot be mistaken for the latest code.

Any `.py` file can be selected, but only code in PyChain's supported smart-contract subset can be converted. Regular Python features such as imports, filesystem access, HTTP clients, classes without `@contract`, or dynamic execution have no equivalent safe EVM translation and return source diagnostics.

The artifact JSON bundle includes the uploaded source name and contents, compiler version/settings, generated Solidity, PyChain IR, generated Hardhat test starter, ABI, and bytecode. It is an export bundle, not a deployed contract or a security audit.

## Beginner templates

Choose a starter in **Beginner templates** to see what it does, a suggested experiment, and its limitations before loading it into the editor. Replacing edited source asks for confirmation. The included examples are:

- **Simple voting** — one vote per wallet address, with readable totals. It has no voter identity, eligibility, or election-period controls.
- **Simple ETH escrow** — a buyer funds an escrow and can release the ETH to a chosen seller. It has no refund, timeout, or dispute path.
- **Owner-managed points registry** — the deployer can award points and anyone can read balances. Points are not transferable tokens.

These examples teach contract state and caller checks; they are not audited or suitable for real funds or high-stakes voting. Compile and inspect the generated Solidity, then experiment on a local EVM network first.

## Deploy on a local network or Sepolia

After compiling successfully with local `solc`, use **Deployment assistant** to connect an injected EVM wallet, enter constructor arguments (including initial ETH for payable constructors), review the active account/network and estimated gas fee, then choose **Sign & deploy in wallet**. The network switch control asks the wallet to switch; the wallet may reject it or require a network to be configured first. The wallet presents the transaction for approval; PyChain does not receive private keys or seed phrases. After confirmation, PyChain checks that bytecode exists at the reported address and offers a downloadable deployment record.

The assistant permits only Sepolia (chain ID `11155111`) or a local EVM network (chain ID `31337`). Sepolia is Ethereum's recommended default testnet for application development; users need test ETH for fees. Local mode requires the user to run a compatible node at `http://127.0.0.1:8545` and configure their wallet for it. PyChain does not run the node, add networks to the wallet, provide test ETH, or deploy to mainnet. A matching chain ID is a guardrail, not proof that a custom RPC endpoint is trustworthy—check your wallet's active network and RPC configuration. [Ethereum testnet guidance and Sepolia resources](https://ethereum.org/developers/docs/networks).

Gas estimates are best-effort and can change before the transaction is included. The wallet's confirmation screen is authoritative for the transaction it signs. Successful compilation and deployment do not constitute a security audit.

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

There is currently one implemented backend: Solidity/EVM. The Rust selector is intentionally marked as unimplemented. General function gas estimation is not implemented; deployment gas estimates are available only as a best-effort preflight. Wallet-connected deployment is limited to Sepolia and a local EVM network, with no mainnet path. Security diagnostics are conservative heuristics and can have false positives or miss vulnerabilities.

## Architecture

```text
Python-like source → AST validation → typed PyChain IR → Solidity backend
                                                   └──→ compatibility/security diagnostics
Solidity backend → pinned solc → Solidity + ABI + bytecode
```

See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the product rationale and staged roadmap.
