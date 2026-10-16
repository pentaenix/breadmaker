# Breadmaker Project JSON 1.0

Breadmaker Project JSON is a public, human-readable project description used by **Import JSON…** and **JSON Project** export in Breadmaker 6.1+. It describes project content, not Breadmaker's SQLite schema or UI/session state.

A Project JSON import always creates a **new project**. Breadmaker validates the complete file before creating the project and rolls the new project back if import fails.

## File identity

Use the extension `.breadmaker.json`.

Every file starts with:

```json
{
  "format": "breadmaker-project",
  "schema_version": "1.0",
  "name": "Required project name"
}
```

`name` is required. Breadmaker never silently overwrites an existing project; if necessary it creates a distinct name such as `My Novel (2)`.

The machine-readable structural schema is `docs/breadmaker-project.schema.json`. Reference existence, Canvas cycles, Developmental Structure rules, and same-Canvas connector rules are semantic checks performed by Breadmaker in addition to JSON Schema validation.

## Public IDs and references

Project JSON never contains Breadmaker database IDs. Give addressable objects readable IDs such as:

- `marker:character`
- `tab:manuscript`
- `chapter:opening`
- `scene:arrival`
- `canvas:characters`
- `card:cinna-want`
- `map:relationships`
- `map-line:cinna`
- `map-item:eugene`
- `task:rewrite-opening`
- `subtask:read-aloud`

IDs must contain a namespace, a colon, and a readable token. They are unique across the entire file. Breadmaker resolves references case-insensitively but preserves the supplied ID spelling.

Recommended convention: lowercase namespace and kebab-case names.

## Inline manuscript markup

Manuscript `content`, Chapter `content`, and Scene `content` are plain text. Newlines become manuscript paragraphs.

Supported inline markup:

| Markup | Meaning |
| --- | --- |
| `**bold**` | Bold |
| `*italic*` | Italic |
| `__underline__` | Underline |
| `\*` | Literal `*` |
| `\_` | Literal `_` |
| `\\` | Literal backslash |

Unmatched formatting markers are retained as literal text rather than destroying content.

## Markers

Markers are first-class reusable colors and are created before other imported objects. Their color is an exact six-digit hex value.

```json
"markers": [
  {
    "id": "marker:character",
    "name": "Character",
    "description": "Character work",
    "color": "#2f8f6b"
  }
]
```

Marker names and colors must be unique within the project. Cards, connectors, and Tasks can use a Marker by reference. Where a Card or connector also supports `color`, use **either** `marker` **or** `color`, never both.

## Manuscript

Tabs are flat. Exactly one tab is the Main Manuscript. If no tabs are provided, or tabs are provided without a Main Manuscript, Breadmaker creates an empty `Manuscript` tab automatically.

```json
"manuscript": {
  "tabs": [
    {
      "id": "tab:manuscript",
      "name": "Manuscript",
      "description": "",
      "main": true,
      "content": "Optional prose outside explicit Chapters.",
      "chapters": [
        {
          "id": "chapter:opening",
          "title": "Opening",
          "description": "",
          "content": "First paragraph.\nSecond *italic* paragraph.",
          "scenes": [
            {
              "id": "scene:arrival",
              "title": "Arrival",
              "description": "",
              "content": "Scene prose."
            }
          ]
        }
      ]
    }
  ]
}
```

`content` on a Tab represents prose outside explicit Chapters. Breadmaker internally handles the persistence detail; JSON authors should treat it simply as Tab-level prose.

Chapters and Scenes appear in array order. Empty Chapters and Scenes are valid.

## Generic links

Tabs, Chapters, Scenes, Canvases, Canvas nodes, Maps, Map lines/items, Tasks, and subtasks can contain `links`. Markers are styling references and are not generic navigation-link targets. Each link chooses exactly one destination: either `target` or `url`, never both.

A simple reference:

```json
"links": ["chapter:opening"]
```

A relationship:

```json
"links": [
  {"target": "chapter:opening", "relationship": "edits"}
]
```

An external link:

```json
"links": [
  {"url": "https://example.com/reference", "relationship": "research"}
]
```

Prefer links over duplicating the same information in multiple Breadmaker objects.

## Canvases

Supported Canvas types are `normal` and `developmental`. Canvases can be nested to arbitrary depth with `parent`.

```json
{
  "id": "canvas:characters",
  "name": "Characters",
  "type": "normal",
  "parent": "canvas:story",
  "layout": "compact",
  "nodes": [],
  "connectors": []
}
```

### Canvas nodes

Project JSON 1.0 supports Cards, Figures, Indexes, and Routing Points. **Images are intentionally not supported.**

Card:

```json
{
  "id": "card:cinna-want",
  "type": "card",
  "title": "Want",
  "body": "Keep everything from changing.",
  "display": "full",
  "shape": "card",
  "marker": "marker:character",
  "linked_to": "chapter:opening"
}
```

`display` is `full`, `header`, or `body`. Hiding a Card header/body is presentation-only; content remains stored.

`text_align` is `left`, `center`, or `right` and affects Card title/body text; the Card icon remains independently anchored.

`shape` is `card` or `text`.

Instead of `marker`, a Card can use an exact custom `color` such as `#aa6644`. `text_color` is also optional.

A Card may set `linked_to` to a Chapter or Scene. Generic `links` are separate and may point to other supported objects.

Figure:

```json
{"id":"figure:act-one","type":"figure","shape":"triangle","stroke_color":"#505050","border_width":2,"layer":"back"}
```

`shape` is `rect`, `circle`, `triangle`, `oval`, or `line`. Optional `fill_color` and `stroke_color` are exact custom colors; omitting them keeps Breadmaker's theme-owned defaults. `border_width` is the Figure line width from `0.5` to `12`, and `layer` is `back` or `front`.

A `line` Figure may persist free-angle endpoints with normalized `line_x1`, `line_y1`, `line_x2`, and `line_y2` values from `0` to `1` inside its `position` bounds. These endpoint fields are not needed for other Figure shapes.

Index:

```json
{"id":"index:one","type":"index","value":"1"}
```

An Index contains digits or one character.

Routing Point:

```json
{"id":"routing:branch","type":"routing"}
```

### Positions and layouts

Any Canvas node may have:

```json
"position": {"x": 120, "y": 180, "width": 220, "height": 130}
```

`x` and `y` are optional unless manual placement is desired. Breadmaker should normally lay generated projects out itself.

Simple whole-Canvas layout:

```json
"layout": "compact"
```

Supported modes:

- `compact`
- `horizontal`
- `vertical`
- `circular`
- `manual`

For mixed layouts, use `layouts`:

```json
"layouts": [
  {
    "mode": "circular",
    "nodes": ["card:cinna", "card:lemon", "card:eugene"],
    "origin": {"x": 100, "y": 100},
    "gap": 32
  },
  {
    "mode": "horizontal",
    "nodes": ["card:beat-one", "card:beat-two"]
  }
]
```

A node can appear in only one explicit layout group. Nodes not explicitly assigned are arranged compactly. Explicit `x`/`y` positions take precedence.

### Connectors

```json
{
  "from": "card:cause",
  "to": "card:effect",
  "from_port": "right",
  "to_port": "left",
  "label": "causes",
  "marker": "marker:plot",
  "stroke": "solid",
  "end": "arrow",
  "width": 1.5,
  "route": "straight"
}
```

Defaults are right → left, solid, arrow end, width `1.5`, straight route.

Ports: `top`, `right`, `bottom`, `left`, `top_left`, `top_right`, `bottom_left`, `bottom_right`, `center`.

Stroke: `solid`, `dashed`, `dotted`.

End: `arrow`, `none`, `circle`, `diamond`.

Route: `straight`, `curved`.

Use `marker` or custom `color`, not both. Both the line and its end marker use the selected color in Breadmaker.

### Developmental Canvas

A Developmental Canvas can use `developmental_scope: "chapters"` or `"scenes"` and an explicit Structure Order:

```json
{
  "id": "canvas:development",
  "name": "Development",
  "type": "developmental",
  "developmental_scope": "chapters",
  "structure": ["chapter:opening", "chapter:decision", "chapter:finale"],
  "nodes": [
    {"id":"card:note","type":"card","title":"Revision note"}
  ]
}
```

Structure references must come from the **Main Manuscript** because that is the source used by Breadmaker Developmental Canvases. The free Canvas under the Structure Order may contain normal Project JSON Canvas nodes/connectors.

Import creates one initial Structure Version. Project JSON 1.0 does not serialize version history.

## Maps

Map names are unique case-insensitively within a Project JSON file, and Map Line names are unique case-insensitively within their Map. This keeps human-readable Fragment selectors unambiguous later.

```json
{
  "id": "map:relationships",
  "name": "Relationships",
  "orientation": "horizontal",
  "layout_mode": "scaled",
  "label_mode": "minimal",
  "ruler": "chapters",
  "background": "#f3f3f1",
  "lines": [
    {
      "id": "map-line:cinna",
      "name": "Cinna",
      "color": "#6b7f73",
      "visible": true,
      "items": [
        {
          "id": "map-item:opening",
          "type": "stop",
          "title": "Opening",
          "anchor": "chapter:opening"
        }
      ]
    }
  ]
}
```

Orientations: `horizontal`, `vertical`.

Layouts: `scaled`, `schematic`.

Label modes: `minimal`, `full`, `hidden`.

Rulers: `none`, `chapters`, `scenes`, `percent`, `pages`.

Map item types are `stop` and `span`. Numeric `start`/`end` positions range from `0` to `1`. `anchor` and `end_anchor` can reference Chapters or Scenes. Use `end`/`end_anchor` only for spans.

Junctions connect Map lines/items:

```json
"junctions": [
  {
    "kind": "branch",
    "from": {"line":"map-line:main", "item":"map-item:split", "edge":"point"},
    "to": {"line":"map-line:secondary", "edge":"start"}
  }
]
```

Junction kind: `branch` or `merge`. Edge: `point`, `start`, `end`.

## Tasks

Breadmaker's existing milestone/subtask model is exposed as `subtasks`:

```json
{
  "id": "task:rewrite-opening",
  "title": "Rewrite opening",
  "description": "Strengthen the first page.",
  "status": "open",
  "marker": "marker:plot",
  "subtasks": [
    {
      "id": "subtask:read-aloud",
      "title": "Read aloud",
      "description": "Listen for rhythm.",
      "completed": false
    }
  ],
  "links": [
    {"target":"chapter:opening", "relationship":"edits"}
  ]
}
```

Statuses: `open`, `later`, `done`, `archived`.


## Lossless export boundary

**Export JSON Project** refuses to emit a file when the live Manuscript uses an arrangement that Project JSON 1.0 cannot represent without changing meaning or order. In particular, a Scene must contain prose, stay inside one explicit Chapter, and remain contiguous; unassigned Chapter prose cannot appear after Scene prose. Breadmaker reports the unsupported arrangement instead of silently reordering, duplicating, or dropping text.

This restriction concerns exporting unusual hand-edited Manuscripts. Projects created from valid Project JSON already follow the representable ordering model and round-trip normally.

## Deliberately excluded from schema 1.0

Project JSON 1.0 does not serialize:

- images or media
- Checkpoints
- Undo/History
- bookmarks
- multiple Developmental versions
- current selected tab/Canvas
- zoom/pan/collapsed-panel state
- theme or other user UI preferences
- filesystem paths
- internal database IDs

## Import validation and warnings

Hard failures include duplicate IDs, missing references, invalid Canvas parents/cycles, connector endpoints in another Canvas, invalid Developmental Structure references, malformed colors, and unsupported schema versions.

Unknown optional fields are ignored with a preflight warning. Harmless content warnings do not create partial projects.

Breadmaker shows a pre-import summary and only creates the project after confirmation.

## Full compact example

```json
{
  "format": "breadmaker-project",
  "schema_version": "1.0",
  "name": "Example Novel",
  "markers": [
    {"id":"marker:plot","name":"Plot","color":"#7a5cff"}
  ],
  "manuscript": {
    "tabs": [
      {
        "id":"tab:manuscript",
        "name":"Manuscript",
        "main":true,
        "chapters":[
          {"id":"chapter:opening","title":"Opening","content":"A **bold** beginning."}
        ]
      },
      {
        "id":"tab:outline",
        "name":"Outline",
        "chapters":[
          {"id":"chapter:outline-one","title":"Beat One","content":"Planning notes."}
        ]
      }
    ]
  },
  "canvases": [
    {
      "id":"canvas:plot",
      "name":"Plot",
      "type":"normal",
      "layout":"horizontal",
      "nodes":[
        {"id":"card:opening","type":"card","title":"Opening","body":"Establish the problem.","marker":"marker:plot","linked_to":"chapter:opening"},
        {"id":"card:decision","type":"card","title":"Decision","body":"The protagonist commits."}
      ],
      "connectors":[
        {"from":"card:opening","to":"card:decision","label":"then","route":"straight"}
      ]
    }
  ],
  "maps": [],
  "tasks": [
    {"id":"task:opening","title":"Review opening","status":"open","links":["chapter:opening"]}
  ]
}
```
