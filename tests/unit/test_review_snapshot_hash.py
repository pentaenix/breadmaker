"""Scene numeric affinity must not change a frozen Review's canonical digest."""
from copy import deepcopy
import json

import pytest

from storage.values import review_snapshot_content_hash, sha


def _scene(position):
    return {'source_scene_id':1,'source_tab_id':1,'ordinal':0,'position':position,
            'title':'Frozen scene','description':'Synthetic prose'}


def _hash(scene):
    return review_snapshot_content_hash('review_test',3,'manuscript',{},[],[scene],[],[],[])


@pytest.mark.parametrize('position',[0,1,7])
def test_scene_whole_number_hash_matches_existing_persisted_integer_hash(position):
    scene=_scene(position)
    canonical={'review_id':'review_test','source_generation':3,'scope_type':'manuscript',
               'permissions':{field:False for field in ('permission_read','permission_comment','permission_suggest','permission_feedback')},
               'chapters':[],'scenes':[scene],'blocks':[],'questions':[],'markers':[]}
    expected=sha(json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    assert _hash(scene)==expected
    source=_scene(float(position));before=deepcopy(source)
    assert _hash(source)==expected
    assert source==before and isinstance(source['position'],float)


@pytest.mark.parametrize('position',[0.25,1.5,7.75])
def test_scene_hash_keeps_fractional_position_significant(position):
    assert _hash(_scene(position))!=_hash(_scene(int(position)))
