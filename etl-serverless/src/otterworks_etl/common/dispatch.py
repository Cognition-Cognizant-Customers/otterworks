"""Task dispatcher shared by all pipeline Lambda entrypoints.

Each pipeline ships as a single Lambda function; Step Functions states invoke
it with a payload of the form ``{"task": "<name>", ...}`` and the dispatcher
routes to the registered task callable. Every task receives the full event and
returns a JSON-serializable dict that becomes the state output.
"""

from collections.abc import Callable
from typing import Any

from otterworks_etl.common.logging import get_logger

logger = get_logger(__name__)

TaskFn = Callable[[dict], dict]


def make_handler(pipeline: str, tasks: dict[str, TaskFn]) -> Callable[[dict, Any], dict]:
    def handler(event: dict, context: Any) -> dict:
        task_name = event.get("task")
        if task_name not in tasks:
            raise ValueError(
                f"Unknown task '{task_name}' for pipeline '{pipeline}'; "
                f"expected one of {sorted(tasks)}"
            )
        logger.info(
            "task starting",
            extra={"context": {"pipeline": pipeline, "task": task_name,
                               "execution_id": event.get("execution_id")}},
        )
        result = tasks[task_name](event)
        logger.info(
            "task completed",
            extra={"context": {"pipeline": pipeline, "task": task_name,
                               "execution_id": event.get("execution_id")}},
        )
        return result

    return handler
