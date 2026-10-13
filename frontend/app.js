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
const analyzeButton = document.querySelector("#analyze");
const status = document.querySelector("#compile-status");
const fileInput = document.querySelector("#python-file");
const sourceFile = document.querySelector("#source-file");
const sourcePane = document.querySelector("#source-pane");
const templateSelect = document.querySelector("#template-select");
const loadTemplateButton = document.querySelector("#load-template");
const templateTitle = document.querySelector("#template-title");
const templateSummary = document.querySelector("#template-summary");
const templateBehavior = document.querySelector("#template-behavior");
const templateTry = document.querySelector("#template-try");
const templateCaution = document.querySelector("#template-caution");
const walletConnectButton = document.querySelector("#wallet-connect");
const switchNetworkButton = document.querySelector("#switch-network");
const walletStatus = document.querySelector("#wallet-status");
const deploymentNetwork = document.querySelector("#deploy-network");
const constructorFields = document.querySelector("#constructor-fields");
const reviewDeploymentButton = document.querySelector("#review-deployment");
const deploymentReview = document.querySelector("#deployment-review");
const deploymentReviewText = document.querySelector("#deployment-review-text");
const confirmDeploymentButton = document.querySelector("#confirm-deployment");
const deploymentResult = document.querySelector("#deployment-result");
const deploymentResultText = document.querySelector("#deployment-result-text");
const downloadDeploymentButton = document.querySelector("#download-deployment");
let lastResult = null;
let currentWallet = null;
let pendingDeployment = null;
let lastDeploymentRecord = null;
let deploymentBusy = false;
let sourceName = "vault.py";
let editorBaseline = example;
let sourceRevision = 0;
let beginnerTemplates = [];
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
  analyzeButton.disabled = true;
  const submittedRevision = sourceRevision;
  const submittedSource = source.value;
  const submittedSourceName = sourceName;
  status.textContent = "Validating source and generating Solidity…";
  try {
    const response = await fetch("/api/compile", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source: submittedSource,
        target: document.querySelector("#target").value,
        source_name: submittedSourceName
      })
    });
    const data = await response.json();
    if (submittedRevision !== sourceRevision) {
      throw {diagnostics: [{severity: "info", code: "SOURCE_CHANGED", message: "Source changed while compiling. Run the compiler again for the latest version."}]};
    }
    if (!response.ok) throw data.detail || {};
    lastResult = data;
    output.textContent = data.solidity;
    ir.textContent = JSON.stringify(data.ir, null, 2);
    tests.textContent = data.tests;
    renderConstructorFields();
    clearDeploymentReview();
    deploymentResult.hidden = true;
    lastDeploymentRecord = null;
    updateDeploymentControls();

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
    renderConstructorFields();
    clearDeploymentReview();
    updateDeploymentControls();
    output.textContent = "// No output generated.";
    ir.textContent = "No IR generated.";
    tests.textContent = "No test starter generated.";
    renderDiagnostics(error.diagnostics || [{severity: "error", message: "The compiler could not process this contract."}]);
    status.textContent = "Compilation stopped — resolve the reported diagnostics";
  } finally {
    button.disabled = false;
    analyzeButton.disabled = false;
  }
}

async function analyzeCompatibility() {
  analyzeButton.disabled = true;
  button.disabled = true;
  const submittedRevision = sourceRevision;
  status.textContent = "Checking source against the supported contract language…";
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source: source.value,
        target: document.querySelector("#target").value,
        source_name: sourceName
      })
    });
    const data = await response.json();
    if (submittedRevision !== sourceRevision) {
      status.textContent = "Source changed during the check — check compatibility again";
      return;
    }
    if (!response.ok) throw data.detail || {};
    const warningCount = (data.diagnostics || []).filter(item => item.severity === "warning").length;
    renderDiagnostics(data.diagnostics || []);
    const contract = data.contract;
    status.textContent = `${data.source_name}: compatible contract "${contract.name}" · ${contract.state_fields} state fields · ${contract.functions} functions · ${contract.events} events${warningCount ? ` · ${warningCount} warning(s) to review` : ""}`;
  } catch (error) {
    lastResult = null;
    clearDeploymentReview();
    updateDeploymentControls();
    output.textContent = "// No current artifact. Compile the compatible source first.";
    ir.textContent = "No current IR.";
    tests.textContent = "No current test starter.";
    renderDiagnostics(error.diagnostics || [{severity: "error", message: "The compatibility check could not process this source."}]);
    status.textContent = "Not compatible yet — review the source diagnostics";
  } finally {
    analyzeButton.disabled = false;
    button.disabled = false;
  }
}

function invalidateGeneratedResult() {
  sourceRevision += 1;
  lastResult = null;
  clearDeploymentReview();
  deploymentResult.hidden = true;
  lastDeploymentRecord = null;
  renderConstructorFields();
  updateDeploymentControls();
  output.textContent = "// Source changed. Compile this version to generate Solidity.";
  ir.textContent = "No current IR. Compile the updated source.";
  tests.textContent = "No current test starter. Compile the updated source.";
}

async function loadPythonFile(file) {
  if (deploymentBusy) {
    fileInput.value = "";
    status.textContent = "Wait for the deployment transaction to finish before editing the source.";
    return false;
  }
  if (!file.name.toLowerCase().endsWith(".py")) {
    fileInput.value = "";
    status.textContent = "Choose a Python source file ending in .py";
    return false;
  }
  if (file.size > 100_000) {
    fileInput.value = "";
    status.textContent = "This file is too large; the limit is 100 KB";
    return false;
  }
  try {
    const contents = await file.text();
    if (deploymentBusy) {
      fileInput.value = "";
      status.textContent = "Wait for the deployment transaction to finish before editing the source.";
      return false;
    }
    source.value = contents;
    editorBaseline = contents;
    sourceName = file.name;
    sourceFile.textContent = sourceName;
    templateSelect.value = "";
    renderTemplateDetails(null);
    fileInput.value = "";
    invalidateGeneratedResult();
    status.textContent = `${sourceName} loaded — check compatibility or compile`;
    return true;
  } catch {
    fileInput.value = "";
    status.textContent = "The selected Python file could not be read";
    return false;
  }
}

function renderTemplateDetails(template) {
  if (!template) {
    templateTitle.textContent = "Pick a template to see what it teaches";
    templateSummary.textContent = "Each example includes a behavior walkthrough, a suggested experiment, and clear limitations.";
    templateBehavior.replaceChildren();
    const prompt = document.createElement("li");
    prompt.textContent = "Select a starter contract above.";
    templateBehavior.append(prompt);
    templateTry.textContent = "";
    templateCaution.textContent = "Educational examples only—review and test contracts locally before considering any public testnet deployment.";
    loadTemplateButton.disabled = true;
    return;
  }

  templateTitle.textContent = template.name;
  templateSummary.textContent = template.summary;
  templateBehavior.replaceChildren();
  for (const behavior of template.behavior) {
    const item = document.createElement("li");
    item.textContent = behavior;
    templateBehavior.append(item);
  }
  templateTry.textContent = `Try it: ${template.try}`;
  templateCaution.textContent = template.caution;
  loadTemplateButton.disabled = deploymentBusy;
}

async function loadBeginnerTemplates() {
  try {
    const response = await fetch("/assets/templates.json");
    if (!response.ok) throw new Error("Template catalog request failed.");
    const catalog = await response.json();
    if (!Array.isArray(catalog) || !catalog.length) throw new Error("Template catalog is empty.");
    beginnerTemplates = catalog;
    for (const template of beginnerTemplates) {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = template.name;
      templateSelect.append(option);
    }
  } catch {
    templateTitle.textContent = "Starter templates are unavailable";
    templateSummary.textContent = "The rest of the editor is still available. Check that the PyChain frontend files are installed correctly.";
    templateBehavior.replaceChildren();
    templateTry.textContent = "";
    templateCaution.textContent = "You can continue with the built-in Vault example or upload your own supported .py contract.";
  }
}

templateSelect.addEventListener("change", () => {
  const template = beginnerTemplates.find(item => item.id === templateSelect.value);
  renderTemplateDetails(template);
});

loadTemplateButton.addEventListener("click", () => {
  const template = beginnerTemplates.find(item => item.id === templateSelect.value);
  if (!template || deploymentBusy) return;
  if (source.value !== editorBaseline
    && source.value.trim()
    && !window.confirm("Replace your current editor contents with this starter template?")) {
    return;
  }
  source.value = template.source;
  editorBaseline = template.source;
  sourceName = template.fileName;
  sourceFile.textContent = sourceName;
  fileInput.value = "";
  invalidateGeneratedResult();
  status.textContent = `${template.name} loaded — review the explanation, then check compatibility or compile`;
});

button.addEventListener("click", compile);
analyzeButton.addEventListener("click", analyzeCompatibility);
source.addEventListener("input", invalidateGeneratedResult);
document.querySelector("#load-example").addEventListener("click", () => {
  source.value = example;
  editorBaseline = example;
  sourceName = "vault.py";
  sourceFile.textContent = sourceName;
  templateSelect.value = "";
  renderTemplateDetails(null);
  fileInput.value = "";
  invalidateGeneratedResult();
  status.textContent = "Vault example loaded";
});
fileInput.addEventListener("change", async () => {
  const [file] = fileInput.files;
  if (!file) return;
  await loadPythonFile(file);
});
for (const eventName of ["dragenter", "dragover"]) {
  sourcePane.addEventListener(eventName, event => {
    event.preventDefault();
    sourcePane.classList.add("drag-over");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  sourcePane.addEventListener(eventName, event => {
    event.preventDefault();
    if (eventName === "drop") {
      const [file] = event.dataTransfer.files;
      if (file && deploymentBusy) {
        status.textContent = "Wait for the deployment transaction to finish before editing the source.";
      } else if (file) {
        loadPythonFile(file);
      }
    }
    sourcePane.classList.remove("drag-over");
  });
}
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
    schemaVersion: 1,
    contractName: artifacts.contractName,
    sourceName: lastResult.source_name,
    compiler: lastResult.solidity_validation.compiler,
    compilerSettings: lastResult.solidity_validation.settings,
    target: "ethereum-evm",
    source: source.value,
    solidity: lastResult.solidity,
    ir: lastResult.ir,
    generatedTests: lastResult.tests,
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

const deploymentNetworks = {
  sepolia: {name: "Sepolia testnet", chainId: 11155111n},
  local: {name: "Local EVM development network", chainId: 31337n}
};

function selectedDeploymentNetwork() {
  return deploymentNetworks[deploymentNetwork.value];
}

function clearDeploymentReview() {
  pendingDeployment = null;
  deploymentReview.hidden = true;
  deploymentReviewText.textContent = "";
  confirmDeploymentButton.disabled = true;
}

function renderConstructorFields() {
  constructorFields.replaceChildren();
  const artifacts = lastResult?.solidity_validation?.artifacts;
  if (!artifacts) {
    constructorFields.textContent = "Compile with local solc to prepare constructor inputs.";
    return;
  }
  const constructor = artifacts.abi.find(item => item.type === "constructor");
  const inputs = constructor?.inputs || [];
  if (!inputs.length) {
    constructorFields.textContent = "This contract has no constructor arguments.";
  } else {
    const heading = document.createElement("h3");
    heading.textContent = "Constructor arguments";
    constructorFields.append(heading);
  }
  for (const [index, parameter] of inputs.entries()) {
    const label = document.createElement("label");
    label.className = "constructor-field";
    const caption = document.createElement("span");
    caption.textContent = `${parameter.name || `Argument ${index + 1}`} · ${parameter.type}`;
    let input;
    if (parameter.type === "bool") {
      input = document.createElement("select");
      for (const value of ["false", "true"]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        input.append(option);
      }
    } else {
      input = document.createElement("input");
      input.type = "text";
      input.placeholder = parameter.type;
      input.autocomplete = "off";
    }
    input.dataset.constructorIndex = String(index);
    input.setAttribute("aria-label", caption.textContent);
    input.addEventListener("input", clearDeploymentReview);
    label.append(caption, input);
    constructorFields.append(label);
  }
  if (constructor?.stateMutability === "payable") {
    const label = document.createElement("label");
    label.className = "constructor-field";
    const caption = document.createElement("span");
    caption.textContent = "Initial ETH value · payable constructor";
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.value = "0";
    input.dataset.constructorValue = "true";
    input.setAttribute("aria-label", caption.textContent);
    input.addEventListener("input", clearDeploymentReview);
    label.append(caption, input);
    constructorFields.append(label);
  }
}

function updateDeploymentControls() {
  const artifacts = lastResult?.solidity_validation?.artifacts;
  const hasArtifact = lastResult?.solidity_validation?.status === "passed"
    && Boolean(artifacts?.abi)
    && Boolean(artifacts?.bytecode && artifacts.bytecode !== "0x");
  const network = selectedDeploymentNetwork();
  const matchingNetwork = Boolean(currentWallet && currentWallet.chainId === network.chainId);
  reviewDeploymentButton.disabled = deploymentBusy || !hasArtifact || !matchingNetwork;
  switchNetworkButton.disabled = deploymentBusy || !window.ethereum || matchingNetwork;
  confirmDeploymentButton.disabled = deploymentBusy || !pendingDeployment;

  if (deploymentBusy) return;

  if (!hasArtifact) {
    walletStatus.textContent = "Compile with local solc to enable deployment.";
  } else if (!currentWallet) {
    walletStatus.textContent = "Connect an injected EVM wallet. PyChain never asks for private keys.";
  } else if (!matchingNetwork) {
    walletStatus.textContent = `Connected ${shortAddress(currentWallet.address)} on chain ${currentWallet.chainId}. Switch your wallet to ${network.name} (chain ID ${network.chainId}).`;
  } else {
    walletStatus.textContent = `Connected ${shortAddress(currentWallet.address)} · ${network.name} · ready for review`;
  }
}

function shortAddress(address) {
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

async function activeWalletState(provider) {
  const [chainIdHex, accounts] = await Promise.all([
    provider.send("eth_chainId", []),
    provider.send("eth_accounts", [])
  ]);
  return {
    chainId: BigInt(chainIdHex),
    address: accounts.length ? ethers.getAddress(accounts[0]) : null
  };
}

async function connectWallet(requestAccess = true) {
  if (!window.ethers) {
    throw new Error("Wallet support is unavailable. Run `npm ci` and reload PyChain.");
  }
  if (!window.ethereum) {
    throw new Error("No injected EVM wallet was found. Install or enable a browser wallet, then try again.");
  }
  const provider = new ethers.BrowserProvider(window.ethereum);
  const accounts = await provider.send(requestAccess ? "eth_requestAccounts" : "eth_accounts", []);
  if (!accounts.length) {
    currentWallet = null;
    updateDeploymentControls();
    return;
  }
  const address = ethers.getAddress(accounts[0]);
  const signer = await provider.getSigner(address);
  const chainId = BigInt(await provider.send("eth_chainId", []));
  currentWallet = {provider, signer, address, chainId};
  updateDeploymentControls();
}

function readConstructorArguments() {
  const artifacts = lastResult?.solidity_validation?.artifacts;
  const constructor = artifacts?.abi.find(item => item.type === "constructor");
  const inputs = constructor?.inputs || [];
  const values = [];
  const displayValues = [];
  for (const [index, parameter] of inputs.entries()) {
    const field = constructorFields.querySelector(`[data-constructor-index="${index}"]`);
    const raw = field?.value ?? "";
    const label = parameter.name || `argument${index + 1}`;
    if (parameter.type === "bool") {
      values.push(raw === "true");
      displayValues.push({name: label, value: raw === "true"});
    } else if (parameter.type === "address") {
      if (!ethers.isAddress(raw)) throw new Error(`${label} must be a valid EVM address.`);
      const address = ethers.getAddress(raw);
      values.push(address);
      displayValues.push({name: label, value: address});
    } else if (/^u?int([0-9]*)$/.test(parameter.type)) {
      if (!/^-?\d+$/.test(raw)) throw new Error(`${label} must be a whole-number value.`);
      if (parameter.type.startsWith("uint") && raw.startsWith("-")) {
        throw new Error(`${label} cannot be negative.`);
      }
      values.push(raw);
      displayValues.push({name: label, value: raw});
    } else {
      values.push(raw);
      displayValues.push({name: label, value: raw});
    }
  }
  let valueWei = 0n;
  let valueEth = "0";
  if (constructor?.stateMutability === "payable") {
    const valueInput = constructorFields.querySelector("[data-constructor-value='true']");
    valueEth = valueInput?.value.trim() || "0";
    if (!/^\d+(\.\d{1,18})?$/.test(valueEth)) {
      throw new Error("Initial ETH value must be a non-negative decimal with at most 18 decimal places.");
    }
    valueWei = ethers.parseEther(valueEth);
    displayValues.push({name: "initial ETH value", value: `${valueEth} ETH`});
  }
  return {values, displayValues, valueWei, valueEth};
}

function formatEstimatedFee(wei) {
  const [whole, fraction = ""] = ethers.formatEther(wei).split(".");
  return `${whole}.${fraction.slice(0, 6).padEnd(6, "0")} ETH (approx.)`;
}

async function reviewDeployment() {
  clearDeploymentReview();
  reviewDeploymentButton.disabled = true;
  const submittedRevision = sourceRevision;
  const submittedResult = lastResult;
  let operationMessage = "";
  try {
    const artifacts = submittedResult?.solidity_validation?.artifacts;
    if (!artifacts || submittedResult.solidity_validation.status !== "passed") {
      throw new Error("Compile and validate the current Solidity artifact first.");
    }
    if (!currentWallet) throw new Error("Connect your wallet first.");
    const reviewingWallet = currentWallet;
    const network = selectedDeploymentNetwork();
    const currentState = await activeWalletState(reviewingWallet.provider);
    if (currentState.chainId !== network.chainId) {
      reviewingWallet.chainId = currentState.chainId;
      updateDeploymentControls();
      throw new Error(`Switch your wallet to ${network.name} (chain ID ${network.chainId}) before reviewing.`);
    }
    if (currentState.address !== reviewingWallet.address) {
      throw new Error("Wallet account changed. Reconnect before reviewing the deployment.");
    }

    const {values, displayValues, valueWei, valueEth} = readConstructorArguments();
    const factory = new ethers.ContractFactory(artifacts.abi, artifacts.bytecode, reviewingWallet.signer);
    const constructor = artifacts.abi.find(item => item.type === "constructor");
    const payableConstructor = constructor?.stateMutability === "payable";
    const overrides = payableConstructor ? [{value: valueWei}] : [];
    const deployTransaction = await factory.getDeployTransaction(...values, ...overrides);
    const gasEstimate = await reviewingWallet.provider.estimateGas({
      ...deployTransaction,
      from: reviewingWallet.address
    });
    const feeData = await reviewingWallet.provider.getFeeData();
    const feePerGas = feeData.maxFeePerGas ?? feeData.gasPrice;
    const estimatedFee = feePerGas ? formatEstimatedFee(gasEstimate * feePerGas) : "Unavailable from this wallet/network";
    if (submittedRevision !== sourceRevision || lastResult !== submittedResult) {
      throw new Error("Source or compiled artifact changed during the estimate. Compile and review it again.");
    }
    const latestState = await activeWalletState(reviewingWallet.provider);
    if (currentWallet !== reviewingWallet
      || latestState.chainId !== network.chainId
      || latestState.address !== reviewingWallet.address) {
      throw new Error("Wallet account or network changed during the estimate. Reconnect and review again.");
    }

    pendingDeployment = {
      args: values,
      displayArgs: displayValues,
      payableConstructor,
      valueWei: valueWei.toString(),
      valueEth,
      sourceRevision: submittedRevision,
      compiledResult: submittedResult,
      chainId: network.chainId,
      networkName: network.name,
      deployer: reviewingWallet.address,
      contractName: artifacts.contractName,
      compiler: submittedResult.solidity_validation.compiler,
      gasEstimate: gasEstimate.toString(),
      estimatedFee
    };
    deploymentReviewText.textContent = [
      `Contract: ${artifacts.contractName}`,
      `Network: ${network.name} (${network.chainId})`,
      `Wallet: ${reviewingWallet.address}`,
      `Estimated gas: ${gasEstimate.toString()}`,
      `Estimated fee: ${estimatedFee}`,
      `Initial ETH value: ${valueEth} ETH`,
      `Constructor arguments: ${JSON.stringify(displayValues)}`
    ].join("\n");
    deploymentReview.hidden = false;
    confirmDeploymentButton.disabled = false;
    operationMessage = "Review the details, then use your wallet to approve or reject the transaction.";
  } catch (error) {
    operationMessage = deploymentErrorMessage(error);
  } finally {
    updateDeploymentControls();
    if (operationMessage) walletStatus.textContent = operationMessage;
  }
}

function deploymentErrorMessage(error) {
  if (error?.code === 4001 || error?.code === "ACTION_REJECTED") {
    return "Wallet request rejected.";
  }
  return error?.shortMessage || error?.reason || error?.message || "Deployment could not be prepared.";
}

async function confirmDeployment() {
  const review = pendingDeployment;
  if (!review) return;
  confirmDeploymentButton.disabled = true;
  reviewDeploymentButton.disabled = true;
  let operationMessage = "";
  let submittedTransaction = null;
  let submittedReceipt = null;
  let submittedAddress = null;
  try {
    if (review.sourceRevision !== sourceRevision) {
      throw new Error("Source changed after review. Compile and review the current artifact again.");
    }
    const artifacts = review.compiledResult?.solidity_validation?.artifacts;
    if (!artifacts || artifacts.contractName !== review.contractName || lastResult !== review.compiledResult) {
      throw new Error("The compiled artifact changed after review. Review it again.");
    }
    if (!currentWallet) throw new Error("Wallet disconnected. Reconnect and review again.");
    const walletState = await activeWalletState(currentWallet.provider);
    if (walletState.chainId !== review.chainId) {
      throw new Error("Wallet network changed after review. Select the target network and review again.");
    }
    if (walletState.address !== currentWallet.address) {
      throw new Error("Wallet account changed after review. Review again with the active account.");
    }
    if (review.sourceRevision !== sourceRevision || lastResult !== review.compiledResult) {
      throw new Error("Source or compiled artifact changed after review. Review it again.");
    }

    const deploymentWallet = currentWallet;
    operationMessage = "Confirm the deployment transaction in your wallet…";
    setDeploymentBusy(true);
    walletStatus.textContent = operationMessage;
    const factory = new ethers.ContractFactory(artifacts.abi, artifacts.bytecode, deploymentWallet.signer);
    const overrides = review.payableConstructor ? [{value: BigInt(review.valueWei)}] : [];
    const contract = await factory.deploy(...review.args, ...overrides);
    const transaction = contract.deploymentTransaction();
    if (!transaction) throw new Error("The wallet did not return a deployment transaction.");
    submittedTransaction = transaction;
    submittedAddress = await contract.getAddress();
    pendingDeployment = null;
    deploymentReview.hidden = true;
    lastDeploymentRecord = null;
    deploymentResult.hidden = true;
    walletStatus.textContent = `Transaction sent: ${transaction.hash} · waiting for confirmation…`;
    submittedReceipt = await transaction.wait();
    if (!submittedReceipt || submittedReceipt.status !== 1) {
      throw new Error("The deployment transaction failed on-chain.");
    }
    const address = submittedAddress;
    let bytecodeVerified = false;
    let verificationNote = "Bytecode could not be verified because the wallet changed networks after confirmation.";
    try {
      const confirmedState = await activeWalletState(deploymentWallet.provider);
      if (confirmedState.chainId === review.chainId) {
        bytecodeVerified = (await deploymentWallet.provider.getCode(address)) !== "0x";
        verificationNote = bytecodeVerified
          ? "Deployed bytecode verified at the reported address."
          : "The transaction was confirmed, but the RPC returned no bytecode at the reported address.";
      }
    } catch {
      verificationNote = "The transaction was confirmed, but bytecode verification failed. Check the address on the selected network.";
    }

    lastDeploymentRecord = {
      schemaVersion: 1,
      contractName: review.contractName,
      address,
      network: review.networkName,
      chainId: review.chainId.toString(),
      deployer: review.deployer,
      transactionHash: transaction.hash,
      blockNumber: submittedReceipt.blockNumber,
      gasUsed: submittedReceipt.gasUsed.toString(),
      bytecodeVerified,
      verificationNote,
      constructorValueWei: review.valueWei,
      constructorValueEth: review.valueEth,
      compiler: review.compiler,
      constructorArguments: review.displayArgs,
      sourceName: review.compiledResult.source_name,
      deployedAt: new Date().toISOString()
    };
    deploymentResultText.textContent = JSON.stringify(lastDeploymentRecord, null, 2);
    deploymentResult.hidden = false;
    operationMessage = `Deployment confirmed at ${address}. ${verificationNote} Save the deployment record for your project.`;
  } catch (error) {
    if (submittedTransaction) {
      const mined = Boolean(submittedReceipt);
      const confirmedSuccessfully = submittedReceipt?.status === 1;
      const trackingNote = confirmedSuccessfully
        ? "The transaction was confirmed, but PyChain could not finish processing its deployment details."
        : mined
          ? "The deployment transaction was mined and failed."
          : "The transaction was submitted, but confirmation could not be verified. Track it using the transaction hash.";
      lastDeploymentRecord = {
        schemaVersion: 1,
        status: confirmedSuccessfully ? "confirmed" : mined ? "failed" : "submitted",
        contractName: review.contractName,
        address: submittedAddress,
        network: review.networkName,
        chainId: review.chainId.toString(),
        deployer: review.deployer,
        transactionHash: submittedTransaction.hash,
        blockNumber: submittedReceipt?.blockNumber ?? null,
        gasUsed: submittedReceipt?.gasUsed?.toString() ?? null,
        bytecodeVerified: false,
        verificationNote: trackingNote,
        constructorValueWei: review.valueWei,
        constructorValueEth: review.valueEth,
        compiler: review.compiler,
        constructorArguments: review.displayArgs,
        sourceName: review.compiledResult.source_name,
        deployedAt: new Date().toISOString()
      };
      deploymentResultText.textContent = JSON.stringify(lastDeploymentRecord, null, 2);
      deploymentResult.hidden = false;
      operationMessage = `${trackingNote} Transaction: ${submittedTransaction.hash}`;
    } else {
      operationMessage = deploymentErrorMessage(error);
    }
  } finally {
    setDeploymentBusy(false);
    confirmDeploymentButton.disabled = !pendingDeployment;
    updateDeploymentControls();
    if (operationMessage) walletStatus.textContent = operationMessage;
  }
}

function setDeploymentBusy(isBusy) {
  deploymentBusy = isBusy;
  source.disabled = isBusy;
  fileInput.disabled = isBusy;
  button.disabled = isBusy;
  analyzeButton.disabled = isBusy;
  document.querySelector("#load-example").disabled = isBusy;
  document.querySelector("#target").disabled = isBusy;
  templateSelect.disabled = isBusy;
  loadTemplateButton.disabled = isBusy || !templateSelect.value;
  deploymentNetwork.disabled = isBusy;
  walletConnectButton.disabled = isBusy;
  updateDeploymentControls();
}

walletConnectButton.addEventListener("click", async () => {
  walletConnectButton.disabled = true;
  try {
    await connectWallet(true);
  } catch (error) {
    currentWallet = null;
    updateDeploymentControls();
    walletStatus.textContent = deploymentErrorMessage(error);
  } finally {
    walletConnectButton.disabled = false;
  }
});

switchNetworkButton.addEventListener("click", async () => {
  const network = selectedDeploymentNetwork();
  switchNetworkButton.disabled = true;
  clearDeploymentReview();
  let operationMessage = "";
  try {
    if (!window.ethereum?.request) {
      throw new Error("This wallet does not support network switching. Switch networks in the wallet instead.");
    }
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{chainId: ethers.toBeHex(network.chainId)}]
    });
    await connectWallet(false);
  } catch (error) {
    if (error?.code === 4902) {
      operationMessage = `${network.name} is not configured in your wallet. Add and verify it in the wallet, then reconnect.`;
    } else {
      operationMessage = deploymentErrorMessage(error);
    }
  } finally {
    updateDeploymentControls();
    if (operationMessage) walletStatus.textContent = operationMessage;
  }
});

reviewDeploymentButton.addEventListener("click", reviewDeployment);
confirmDeploymentButton.addEventListener("click", confirmDeployment);
deploymentNetwork.addEventListener("change", () => {
  clearDeploymentReview();
  updateDeploymentControls();
});

document.querySelector("#target").addEventListener("change", () => {
  invalidateGeneratedResult();
  status.textContent = "Target changed — compile the source for the selected backend.";
});

downloadDeploymentButton.addEventListener("click", () => {
  if (!lastDeploymentRecord) return;
  const fileName = `${lastDeploymentRecord.contractName}.${lastDeploymentRecord.chainId}.deployment.json`;
  downloadBlob(new Blob([JSON.stringify(lastDeploymentRecord, null, 2)], {type: "application/json"}), fileName);
});

if (window.ethereum?.on) {
  const refreshWalletState = async () => {
    clearDeploymentReview();
    try {
      await connectWallet(false);
    } catch (error) {
      currentWallet = null;
      updateDeploymentControls();
      walletStatus.textContent = deploymentErrorMessage(error);
    }
  };
  window.ethereum.on("accountsChanged", refreshWalletState);
  window.ethereum.on("chainChanged", refreshWalletState);
  connectWallet(false).catch(() => updateDeploymentControls());
} else {
  updateDeploymentControls();
}

loadBeginnerTemplates();
