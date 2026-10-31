"""Closed diagnostics shared by workflow services and the storage adapter."""


class Unavailable(Exception):
    def __init__(self):
        super().__init__('Workflow unavailable')


class Conflict(Exception):
    pass


class InvalidWorkflow(ValueError):
    def __init__(self):
        super().__init__('Invalid workflow')


class OutputLimit(Exception):
    pass
