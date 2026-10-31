from __future__ import annotations

"""Annotations namespace: pure matching imports must not boot native SQL.

Native API composition imports ``annotations.routes`` explicitly. Implementation
and payload contracts stay in their focused owners; no lazy facade is installed.
"""
