from __future__ import annotations

"""Explicit schema coverage contracts for History and generation tracking."""

import sqlite3

_HISTORY_MANAGED_TABLES = frozenset({
    'document_tabs','chapters','scenes','bookmarks','markup_labels','icon_markers',
    'manuscript_blocks','comment_threads','annotations','comment_messages','tasks',
    'task_milestones','canvases','canvas_nodes','canvas_edges','canvas_versions','canvas_version_items','story_maps','map_lines',
    'map_items','map_junctions','references_map','reviews','review_participants','review_scope_chapters','review_scope_scenes','review_marker_labels','review_markers','review_snapshots','review_snapshot_checkpoints','review_snapshot_markers','review_snapshot_chapters','review_snapshot_scenes','review_snapshot_blocks','review_anchors','review_highlights','review_comments','review_comment_replies','review_suggestions','questionnaires','questionnaire_questions','review_questionnaires','review_snapshot_questions','review_question_answers','review_general_feedback','review_events','review_imports',
})


_HISTORY_EXCLUDED_TABLES = frozenset({
    'projects','ui_state','checkpoints','cuts','checkpoint_assets','checkpoint_asset_refs','storage_meta',
})


_GENERATION_CANONICAL_TABLES = frozenset({
    'projects','document_tabs','chapters','scenes','bookmarks','markup_labels',
    'icon_markers','manuscript_blocks','comment_threads','annotations',
    'comment_messages','tasks','task_milestones','canvases','canvas_nodes',
    'canvas_edges','canvas_versions','canvas_version_items','story_maps','map_lines',
    'map_items','map_junctions','references_map','reviews','review_participants','review_scope_chapters','review_scope_scenes','review_marker_labels','review_markers','review_snapshots','review_snapshot_checkpoints','review_snapshot_markers','review_snapshot_chapters','review_snapshot_scenes','review_snapshot_blocks','review_anchors','review_highlights','review_comments','review_comment_replies','review_suggestions','questionnaires','questionnaire_questions','review_questionnaires','review_snapshot_questions','review_question_answers','review_general_feedback','review_events','review_imports','cuts','checkpoints',
})


_GENERATION_EXCLUDED_TABLES = frozenset({
    'ui_state','checkpoint_assets','checkpoint_asset_refs','storage_meta',
})


def history_schema_classification(con: sqlite3.Connection) -> dict:
    tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    classified=set(_HISTORY_MANAGED_TABLES)|set(_HISTORY_EXCLUDED_TABLES)
    return {
        'managed':sorted(tables & set(_HISTORY_MANAGED_TABLES)),
        'excluded':sorted(tables & set(_HISTORY_EXCLUDED_TABLES)),
        'unclassified':sorted(tables-classified),
        'missing':sorted(classified-tables),
    }


def generation_schema_classification(con: sqlite3.Connection) -> dict:
    tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    classified=set(_GENERATION_CANONICAL_TABLES)|set(_GENERATION_EXCLUDED_TABLES)
    return {
        'canonical':sorted(tables & set(_GENERATION_CANONICAL_TABLES)),
        'excluded':sorted(tables & set(_GENERATION_EXCLUDED_TABLES)),
        'unclassified':sorted(tables-classified),
        'missing':sorted(classified-tables),
    }
