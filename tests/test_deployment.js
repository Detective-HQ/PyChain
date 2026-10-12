const assert = require("node:assert/strict");
const solc = require("solc");
const {ContractFactory, parseEther} = require("ethers");

const compilerInput = {
  language: "Solidity",
  sources: {
    "PayableConstructor.sol": {
      content: `
        pragma solidity ^0.8.24;
        contract PayableConstructor {
          uint256 public initialValue;
          constructor(uint256 value) payable {
            initialValue = value;
          }
        }
      `
    }
  },
  settings: {
    outputSelection: {"*": {"*": ["abi", "evm.bytecode.object"]}}
  }
};

const compilerOutput = JSON.parse(solc.compile(JSON.stringify(compilerInput)));
const compilerErrors = (compilerOutput.errors || []).filter(item => item.severity === "error");
assert.deepEqual(compilerErrors, [], JSON.stringify(compilerErrors, null, 2));

const compiled = compilerOutput.contracts["PayableConstructor.sol"].PayableConstructor;
const factory = new ContractFactory(compiled.abi, `0x${compiled.evm.bytecode.object}`);
const initialValue = 42n;
const deploymentValue = parseEther("0.25");

factory.getDeployTransaction(initialValue, {value: deploymentValue}).then(transaction => {
  assert.equal(transaction.value, deploymentValue);
  const encodedConstructor = factory.interface.encodeDeploy([initialValue]);
  assert.ok(transaction.data.endsWith(encodedConstructor.slice(2)));
  console.log("Payable constructor arguments and ETH value encode correctly.");
}).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
