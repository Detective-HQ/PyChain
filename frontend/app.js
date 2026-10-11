const example = `from pychain import contract, event, external, payable, view, require, emit, send_eth, mapping, msg

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
`;

const source = document.querySelector("#source");
const output = document.querySelector("#output");
const diagnostics = document.querySelector("#diagnostics");
const ir = document.querySelector("#ir");
const tests = document.querySelector("#tests");
const button = document.querySelector("#compile");
const status = document.querySelector("#compile-status");
const fileInput = document.querySelector("#python-file");
const sourceFile = document.querySelector("#source-file");
let lastResult = null;
let sourceName = "vault.py";
source.value = example;

function renderDiagnostics(items) {
  diagnostics.replaceChildren();
  if (!items.length) {
    diagnostics.className = "empty";
    diagnostics.textContent = "No language or security diagnostics. Compilation is not an audit.";
    return;
  }
  diagnostics.className = "";
  for (const item of items) {
    const card = document.createElement("div");
    card.className = `diagnostic ${item.severity || ""}`;
    const title = document.createElement("b");
    const line = item.line ? ` · line ${item.line}${item.column ? `:${item.column}` : ""}` : "";
    title.textContent = `${item.severity || "info"}${line}${item.code ? ` · ${item.code}` : ""}`;
    const message = document.createElement("div");
    message.textContent = item.message;
    card.append(title, message);
    diagnostics.append(card);
  }
}

async function compile() {
  button.disabled = true;
  status.textContent = "Validating source and generating Solidity…";
  try {
    const response = await fetch("/api/compile", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source: source.value,
        target: document.querySelector("#target").value,
        source_name: sourceName
      })
    });
    const data = await response.json();
    if (!response.ok) throw data.detail || {};
    lastResult = data;
    output.textContent = data.solidity;
    ir.textContent = JSON.stringify(data.ir, null, 2);
    tests.textContent = data.tests;

    const items = [...(data.diagnostics || [])];
    const validation = data.solidity_validation;
    if (validation?.status === "failed") {
      items.push(...(validation.errors || []));
    } else if (validation?.status === "not_configured") {
      items.push({severity: "info", code: "SOLC_NOT_CONFIGURED", message: validation.message});
    } else if (validation?.status === "passed") {
      items.push(...(validation.warnings || []));
    }
    renderDiagnostics(items);

    if (validation?.status === "passed") {
      status.textContent = `Solidity compiled successfully · ${validation.compiler || "solc"}`;
    } else if (validation?.status === "failed") {
      status.textContent = "PyChain generated Solidity, but target compilation failed";
    } else {
      status.textContent = "PyChain generated Solidity · install dependencies for target compilation";
    }
  } catch (error) {
    lastResult = null;
    output.textContent = "// No output generated.";
    ir.textContent = "No IR generated.";
    tests.textContent = "No test starter generated.";
    renderDiagnostics(error.diagnostics || [{severity: "error", message: "The compiler could not process this contract."}]);
    status.textContent = "Compilation stopped — resolve the reported diagnostics";
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", compile);
document.querySelector("#load-example").addEventListener("click", () => {
  source.value = example;
  sourceName = "vault.py";
  sourceFile.textContent = sourceName;
  fileInput.value = "";
  status.textContent = "Vault example loaded";
});
fileInput.addEventListener("change", async () => {
  const [file] = fileInput.files;
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".py")) {
    fileInput.value = "";
    status.textContent = "Choose a Python source file ending in .py";
    return;
  }
  if (file.size > 100_000) {
    fileInput.value = "";
    status.textContent = "This file is too large; the limit is 100 KB";
    return;
  }
  try {
    source.value = await file.text();
    sourceName = file.name;
    sourceFile.textContent = sourceName;
    status.textContent = `${sourceName} loaded — compile to generate Solidity`;
  } catch {
    fileInput.value = "";
    status.textContent = "The selected Python file could not be read";
  }
});
document.querySelector("#copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText(output.textContent);
  status.textContent = "Solidity copied to clipboard";
});
document.querySelector("#download").addEventListener("click", () => {
  if (!lastResult) return;
  const blob = new Blob([output.textContent], {type: "text/plain;charset=utf-8"});
  downloadBlob(blob, `${lastResult.ir.name}.sol`);
});
document.querySelector("#download-artifact").addEventListener("click", () => {
  const artifacts = lastResult?.solidity_validation?.artifacts;
  if (!artifacts) {
    status.textContent = "Compile with local solc before downloading ABI/bytecode";
    return;
  }
  const bundle = {
    contractName: artifacts.contractName,
    compiler: lastResult.solidity_validation.compiler,
    source: source.value,
    solidity: lastResult.solidity,
    abi: artifacts.abi,
    bytecode: artifacts.bytecode
  };
  downloadBlob(new Blob([JSON.stringify(bundle, null, 2)], {type: "application/json"}), `${artifacts.contractName}.artifact.json`);
});

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
