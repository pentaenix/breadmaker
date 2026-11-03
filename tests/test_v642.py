from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
import json
from pathlib import Path
import re
import shutil
import subprocess


def test_v642_collapsed_map_lines_are_active_map_color_swatches():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,4,2)
    # Evaluate the actual read-model expression instead of freezing its spelling.
    # Desktop collapse preferences must not make an open phone drawer compact.
    visibility = re.search(r'\bconst\s+showLines\s*=\s*([^;]+);', js)
    assert visibility is not None
    node = shutil.which('node')
    assert node, 'Node is required by the existing release gate'
    result = subprocess.run([node, '-e', '''
      const assert=require('node:assert/strict'),vm=require('node:vm');
      const expression=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
      for(const phone of [false,true])for(const expanded of [false,true])
      for(const collapsed of [false,true])for(const active of [false,true]){
        const context={phoneRailMedia:{matches:phone},state:{mapRailExpanded:expanded,
          currentMap:{id:active?1:2}},map:{id:1},collapsed};
        const actual=vm.runInNewContext(expression,context,{timeout:100});
        const expected=phone?!collapsed:(expanded?!collapsed:active);
        assert.equal(actual,expected,JSON.stringify({phone,expanded,collapsed,active}));
      }
    '''], input=json.dumps(visibility.group(1)), text=True, capture_output=True, timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '.maps-sidebar.collapsed .map-tree-line-short{display:none}' in css
    assert '.maps-sidebar.collapsed .map-tree-line-dot{display:block;width:26px;height:8px;border-radius:999px}' in css
