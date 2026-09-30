# workspace/task_live_13013/solution.py

from typing import Tuple, Dict, Any

TARGET_SERIES = (2, 3)

def check_version_compatibility(current_version: Tuple[int, ...]) -> bool:
    """Validates runtime compatibility with 2.3.x release series."""
    if len(current_version) < 2:
        return False
    return current_version[:2] == TARGET_SERIES

def get_branch_metadata() -> Dict[str, Any]:
    """Returns branch manifest for 2.3.x maintenance stream."""
    return {
        "series": "2.3.x",
        "status": "maintenance",
        "python_requires": ">=3.8",
        "supported": True
    }