from api.annotations.marker_colors import recommend_marker_color,contrast,rgb
import math


def test_recommended_colors_are_readable_unique_and_distinct():
    used=[]
    for _ in range(25):
        candidate=recommend_marker_color(used)
        assert candidate not in used
        assert contrast(candidate,'#ffffff')>=1.32
        assert contrast(candidate,'#222322')>=1.32
        assert max(contrast(candidate,'#202020'),contrast(candidate,'#ececea'))>=4.5
        assert all(math.dist(rgb(candidate),rgb(color))>=48 for color in used)
        used.append(candidate)


def test_recommendation_is_deterministic_and_case_insensitive():
    assert recommend_marker_color(['#aabbcc','#112233'])==recommend_marker_color(['#AABBCC','#112233'])
    assert recommend_marker_color(['#112233','#aabbcc'])==recommend_marker_color(['#aabbcc','#112233'])
