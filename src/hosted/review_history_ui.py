"""Read-only Review history projections over already-authorized hosted reads."""
from copy import deepcopy
import json


def snapshot_payload(record):
    result = deepcopy(record.get('snapshot') or record)
    # Retain original bytes in canonical storage/export, not every reader reply.
    result.pop('native_source', None)
    return result


def round_order(record):
    return (int(record.get('round_number') or 0), str(record.get('created_at') or ''), str(record.get('id') or ''))


def enrich_review_summaries(pid, reviews, list_rows):
    """Show imported native rounds without hydrating every novel at bootstrap.

    Import-time summaries are derived from immutable frozen rounds. Overlay the
    current lifecycle pointer without rewriting that historical source content.
    The callback is the caller's normal project-authorized list operation.
    """
    result = []
    for source in reviews:
        review = dict(source)
        native = review.pop('native_rounds', None)
        if isinstance(native, list):
            closed = {str(value) for value in review.get('closed_snapshot_ids') or []}
            active = str(review.get('active_snapshot_id') or '')
            rounds = deepcopy(native)
            for row in rounds:
                if str(row['id']) in closed:
                    row['status'] = 'closed'
                elif str(row['id']) == active and review.get('status') in ('active', 'open', 'paused'):
                    row['status'] = 'paused' if review['status'] == 'paused' else 'open'
            review['rounds'] = sorted(rounds, key=round_order, reverse=True)
            review['snapshot'] = next((row for row in rounds if str(row['id']) == active),
                                      review['rounds'][0] if rounds else None)
            parents = {'project_id': pid, 'review_id': str(review['id'])}
            events = list_rows('review_event', parents)
            history = []
            allowed = {'REVIEW_ROUND_STARTED', 'REVIEW_PAUSED', 'REVIEW_RESUMED', 'REVIEW_ROUND_CLOSED',
                       'REVIEW_ROUND_REOPENED', 'REVIEW_ARCHIVED', 'REVIEW_UNARCHIVED', 'REVIEW_CHECKPOINT_UPDATED'}
            for event in events:
                if event.get('event_type') not in allowed:
                    continue
                row = dict(event)
                try:
                    row['payload'] = json.loads(row.pop('payload_json', '{}'))
                except (TypeError, ValueError):
                    row['payload'] = {}
                row['created_at'] = row.get('native_created_at') or row.get('created_at')
                row.pop('native_payload_json', None)
                history.append(row)
            review['history'] = sorted(history, key=lambda row: (str(row.get('created_at') or ''), str(row['id'])), reverse=True)[:100]
        result.append(review)
    return result


def feedback_rows(pid, rid, list_rows, participant_id=None, snapshot_id=None, snapshot_records=None):
    """Join frozen anchors/questions within this Review's authorized subtree."""
    parents = {'project_id': pid, 'review_id': rid}
    snapshots = list_rows('review_snapshot', parents) if snapshot_records is None else snapshot_records
    if snapshot_id is not None:
        snapshots = [row for row in snapshots if str(row['id']) == str(snapshot_id)]
    snapshot_by_id = {str(row['id']): snapshot_payload(row) for row in snapshots}
    anchors = {}
    for sid in snapshot_by_id:
        for row in list_rows('review_anchor', {**parents, 'snapshot_id': sid}):
            anchors[str(row['id'])] = {**row, 'snapshot_id': sid}
    participants = {str(row['id']): row for row in list_rows('review_participant', parents)}

    def rows(kind):
        return [dict(row) for row in list_rows(kind, parents)
                if participant_id is None or str(row.get('participant_id') or '') == participant_id]

    result = {}
    for key, kind in (('highlights', 'review_highlight'), ('comments', 'review_comment'), ('suggestions', 'review_suggestion')):
        joined = []
        for row in rows(kind):
            anchor = anchors.get(str(row.get('anchor_id') or ''))
            if row.get('anchor_id') and anchor is None:
                continue
            if anchor:
                for field in ('snapshot_id', 'start_block_id', 'end_block_id', 'start_offset', 'end_offset',
                              'selected_text', 'prefix_context', 'suffix_context', 'context_hash'):
                    if field in anchor:
                        row[field] = anchor[field]
            sid = str(row.get('snapshot_id') or '')
            if snapshot_id is not None and sid != str(snapshot_id):
                continue
            snap = snapshot_by_id.get(sid, {})
            row['round_number'] = snap.get('round_number')
            row['participant_name'] = participants.get(str(row.get('participant_id')), {}).get('display_name', '')
            if key == 'highlights':
                marker = next((m for m in snap.get('markers', []) if str(m.get('name') or '').casefold() == str(row.get('tag') or '').casefold()), {})
                row['marker_name'] = marker.get('name') or row.get('tag') or ''
                row['marker_color'] = marker.get('color') or '#f2c94c'
            joined.append(row)
        result[key] = joined

    forms = {}
    def form(row):
        sid, participant = str(row.get('snapshot_id') or ''), str(row.get('participant_id') or '')
        if snapshot_id is not None and sid != str(snapshot_id):
            return None
        return forms.setdefault((sid, participant), {
            'snapshot_id': sid, 'participant_id': participant,
            'round_number': snapshot_by_id.get(sid, {}).get('round_number'),
            'participant_name': participants.get(participant, {}).get('display_name', ''),
            'answers': [], 'general_feedback': '',
        })
    for row in rows('review_question_answer'):
        target = form(row)
        if target is None:
            continue
        questions = snapshot_by_id.get(str(row.get('snapshot_id')), {}).get('questions', [])
        question = next((q for q in questions if str(q['id']) == str(row.get('question_id'))), {})
        target['answers'].append({**row, 'prompt': question.get('prompt', ''),
                                  'questionnaire_name': question.get('questionnaire_name', '')})
    generals = rows('review_general_feedback')
    for row in generals:
        target = form(row)
        if target is not None:
            target.update(general_feedback=str(row.get('body') or ''), general_feedback_id=row['id'])
    result['feedback_forms'] = sorted(forms.values(), key=lambda row: (int(row.get('round_number') or 0), row['participant_id']))
    result['feedback'] = [row for row in generals if snapshot_id is None or str(row.get('snapshot_id')) == str(snapshot_id)]
    return result
