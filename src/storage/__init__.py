"""Breadmaker canonical persistence package.

Runtime behavior lives in focused owner modules such as ``storage.context``,
``database``, ``mutations``, ``history``, ``undo``, ``assets``, ``invariants``,
``integrity``, and ``recovery``. Import the owner that defines the behavior you need.
"""

from .schema import SCHEMA_VERSION, SCHEMA_SQL

__all__=['SCHEMA_VERSION','SCHEMA_SQL']
