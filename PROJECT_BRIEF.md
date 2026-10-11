# PyChain — product concept and implementation status

## Product

PyChain is a Python-first **smart-contract language and compiler**. Developers express contract state and transaction logic in familiar Python syntax; PyChain validates the supported language, translates it through a typed intermediate representation, and generates target-specific code.

The promise is not “turn any Python/Web2 application into a contract.” Python programs can use files, networking, databases, threads, dynamic imports, and libraries that have no on-chain equivalent. PyChain instead bridges the authoring gap by defining explicit contract primitives for persistent state, callers, transaction value, events, reverts, and value transfers.

## Practical initial target

The first supported chain family is EVM-compatible networks, through Solidity. Rust-based chains are a separate future backend with their own semantics and feature capability matrix; Rust generation is not implemented yet.

Current end-to-end workflow:

1. Write a Python-like contract in the web editor.
2. Parse and validate the source without executing user Python.
3. Lower supported source constructs into typed PyChain IR.
4. Generate Solidity and source-line annotations.
5. Compile with pinned Solidity 0.8.24 when project dependencies are installed.
6. Return compiler errors or compiled ABI/bytecode, static heuristic warnings, and a Hardhat deployment-test starter.

## Current supported contract features

- Typed state and one-dimensional mappings.
- Typed external methods, view/pure methods, constructors, and payable methods.
- `msg.sender`, `msg.value`, `block.timestamp`, and contract ETH balance.
- Events, `require`, `assert`, typed locals, conditions, arithmetic, and mapping operations.
- Checked ETH transfers through `send_eth`; the compiler adds a reentrancy lock around state-changing external methods for contracts that use this helper.
- Solidity compiler validation and ABI/bytecode generation.

The exact grammar and limits live in `backend/compiler.py` and are described in `README.md`. Unsupported code fails closed with source diagnostics.

## Architecture

```text
Python-like source
        │
        ▼
AST parser + subset/type checks ──► source diagnostics
        │
        ▼
Typed chain-neutral PyChain IR
        │
        ├──► Solidity/EVM backend ──► pinned solc ──► ABI + bytecode
        └──► Future backends, each with an explicit capability matrix
```

“Chain-neutral” does not mean every feature has identical semantics across chains. A backend must reject any IR construct it cannot implement consistently.

## Important safety boundary

Generated code is not automatically secure just because it compiles. PyChain currently has heuristic checks, not a complete analyzer; warnings may be false positives, and vulnerabilities may be missed. Generated tests currently provide a deployment smoke-test starter rather than semantic/property-based verification. Gas estimation, wallet integration, and deployment are not implemented.

## Roadmap

| Phase | Goal | Evidence required |
|---|---|---|
| 0 — working slice | Editor → AST/IR → Solidity → pinned solc | Included Vault compiles and returns ABI/bytecode; unsupported syntax fails with diagnostics |
| 1 | Behavioral tests | Foundry/Hardhat tests exercise state transitions and revert conditions, not just deployment |
| 2 | Contract primitives | Explicit access-control, ownership, token/event patterns, data structures, and capability docs |
| 3 | Security and resource feedback | Slither integration, property tests, limits/cost analysis, false-positive tracking |
| 4 | Reproducible projects | Versioned project files, compiler metadata, source maps, lockfiles, deterministic artifacts |
| 5 | Additional backend | A Rust-based target with independent semantics and conformance tests |
| 6 | Deployment workflow | User-approved network selection, wallet signing, deployment records, and artifact verification |

## Success criteria

- Each accepted program compiles deterministically with a pinned backend toolchain.
- Unsupported constructs fail closed and identify the source location.
- Tests validate intended state transitions and failure behavior.
- Cross-target support is claimed only for features with semantic conformance tests.
- Security language distinguishes compiler errors, heuristic findings, test results, and formal/audit guarantees.
