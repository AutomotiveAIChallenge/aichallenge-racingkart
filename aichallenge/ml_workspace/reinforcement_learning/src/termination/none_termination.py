from context.context_types import StepContext
from termination.interfaces import TerminationFunction


class NoneTermination(TerminationFunction):
    def __init__(self) -> None:
        return None

    def reset(self) -> None:
        return None

    def is_terminated(self, context: StepContext) -> tuple[bool, StepContext]:
        return False, context
