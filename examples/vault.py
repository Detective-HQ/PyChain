from pychain import contract, event, external, payable, view, require, emit, send_eth, mapping, msg


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
