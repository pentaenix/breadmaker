# Breadmaker Fragment JSON 1.0

Breadmaker Fragment JSON is the additive companion to Project JSON.
## Contextual Canvas and Map imports

Breadmaker 6.2.2 exposes collection-level `Import Canvas…` and `Import Map…` actions in the Canvas/Maps rail menus. These entries deliberately accept Fragment JSON only. `Import Canvas…` requires at least one top-level Canvas and may include supporting Markers; `Import Map…` requires at least one top-level Map and may include supporting Markers. Mixed Fragments should be imported through the general `Import JSON…` workflow instead.

- `breadmaker-project` creates a new project.
- `breadmaker-fragment` adds content to the currently open project.
- Fragment import never replaces the Main Manuscript, deletes prose, or overwrites existing objects.
- The entire fragment is validated first and imported as one Undoable action.

## Root

```json
{
  "format": "breadmaker-fragment",
  "schema_version": "1.0",
  "name": "Character Planning Pack",
  "markers": [],
  "manuscript": {"tabs": []},
  "canvases": [],
  "maps": [],
  "tasks": [],
  "cuts": [],
  "mounts": []
}
```

`name` is required and is only the human-readable name shown in the import preview. A Fragment must contain at least one actual addition; an otherwise empty Fragment is rejected instead of succeeding as a no-op.

Whole Markers, Tabs, Canvases, Maps and Tasks use the exact same object definitions as Breadmaker Project JSON 1.0. Prose uses the same markup: `**bold**`, `*italic*`, `__underline__`, with `\*`, `\_`, and `\\` for literal formatting characters.

## Markers

Markers are project-wide. A fragment Marker requires a symbolic `id`, unique `name`, and exact six-digit hex `color`.

If the current project already contains a Marker with the same name **and the same exact color**, Breadmaker reuses it. The fragment symbolic ID is bound to that existing Marker. Same-name/different-color or same-color/different-name conflicts fail during preview.

```json
{
  "format":"breadmaker-fragment",
  "schema_version":"1.0",
  "name":"Revision Markers",
  "markers":[
    {"id":"marker:character","name":"Character","color":"#A86E72"},
    {"id":"marker:plot","name":"Plot","color":"#7189A8"}
  ]
}
```

## Whole objects

A fragment can add complete objects with the same schemas used by Project JSON:

- `manuscript.tabs[]` adds new flat Tabs. A fragment cannot set `main:true`.
- `canvases[]` adds root Canvases, including nested Canvases whose parents are also created by the fragment.
- `maps[]` adds complete Maps. Map names are unique case-insensitively within a project; use a Map mount when adding Lines to an existing Map. Map Line names are likewise unique within their Map.
- `tasks[]` adds Tasks and their subtasks.
- `cuts[]` adds non-destructive Cuts.

Fragment JSON 1.0 can create a Developmental Canvas, but does not set Structure Order. Structure remains human-controlled after import.

## Mounts: adding smaller pieces

`mounts` add content inside an existing container. Targets may use the current UI context or a readable selector. Breadmaker shows the resolved destination in the preview and asks the user when more than one candidate exists.

### Target forms

Current Canvas:

```json
{"type":"canvas","current":true}
```

Canvas by hierarchy path:

```json
{"type":"canvas","path":["Story","Characters"]}
```

Tab by name:

```json
{"type":"tab","name":"Outline"}
```

Chapter by Tab + title:

```json
{"type":"chapter","tab":"Outline","name":"Act I"}
```

Map by name:

```json
{"type":"map","name":"Relationships"}
```

Map Line by Map + line name:

```json
{"type":"map_line","map":"Relationships","name":"Cinna"}
```

Task by title:

```json
{"type":"task","name":"Rewrite opening"}
```

If no selector is supplied, Breadmaker offers compatible destinations. `current:true` is ideal for fragments meant to be imported from a Tab/Canvas/Map context menu. `current:true` is strict: if there is no compatible current object (or it no longer exists), Breadmaker asks the user to choose a destination instead of silently guessing.

### What each target accepts

| Target | Accepted additions |
| --- | --- |
| `tab` | `chapters` |
| `chapter` | `scenes` |
| `canvas` | `nodes`, `connectors`, `layout`/`layouts`, nested `canvases` |
| `map` | `lines`, `junctions` |
| `map_line` | `items` (stations/spans) |
| `task` | `subtasks` |

A mount may contain only fields compatible with its target type.

### Add Cards to the current Canvas

```json
{
  "format":"breadmaker-fragment",
  "schema_version":"1.0",
  "name":"Possible Motivations",
  "mounts":[{
    "target":{"type":"canvas","current":true},
    "nodes":[
      {"id":"card:motivation-one","type":"card","title":"Protect the family","body":"Fear of losing the remaining family."},
      {"id":"card:motivation-two","type":"card","title":"Preserve the bakery","body":"The bakery represents continuity."}
    ],
    "layout":"compact"
  }]
}
```

Automatic Canvas layouts are positioned after existing Canvas content. Explicit manual x/y positions remain literal.

Canvas fragments use the same Card/Figure node fields as Project JSON. Cards may set `text_align` to `left`, `center`, or `right`. Figures may use `rect`, `circle`, `triangle`, `oval`, or `line`, plus optional `fill_color`, `stroke_color`, `border_width`, and `layer`. Free-angle Line Figures may also provide normalized `line_x1`, `line_y1`, `line_x2`, and `line_y2` endpoint coordinates.

### Add a Line to an existing Map

```json
{
  "format":"breadmaker-fragment",
  "schema_version":"1.0",
  "name":"Cinna Relationship Line",
  "mounts":[{
    "target":{"type":"map","name":"Relationships"},
    "lines":[{
      "id":"map-line:cinna",
      "name":"Cinna",
      "color":"#7189A8",
      "items":[
        {"id":"map-item:finn","type":"stop","title":"Finn"},
        {"id":"map-item:eugene","type":"stop","title":"Eugene"}
      ]
    }]
  }]
}
```

### Add stations/spans to an existing Map Line

Use a `map_line` target with `items`.

### Add Chapters or Scenes

Use a `tab` target with `chapters`, or a `chapter` target with `scenes`. Imported prose uses the same tiny markup language as Project JSON.

## Cuts

Cuts are additive preserved material. Importing a Cut does **not** remove matching text from the Manuscript.

```json
{"id":"cut:alternate-opening","title":"Alternate opening","content":"Old **opening** text.","note":"Keep for comparison"}
```

Optional provenance strings are `source_label`, `source_project`, `source_chapter`, and `source_scene`.

## References and limits

Symbolic IDs are globally unique inside the fragment. Internal links, Canvas connector endpoints, Map anchors, and Marker references may refer to objects created anywhere in the same fragment. Generic `links` may target persistable navigation objects (Tabs, Chapters, Scenes, Canvases/nodes, Maps/lines/items, Tasks/subtasks) or HTTP(S) URLs; Markers are styling references rather than navigation targets. Existing project database IDs are never exposed in the file.

Fragment JSON 1.0 intentionally does not provide arbitrary destructive mutation. It does not delete, replace, or rewrite existing objects. It also does not import images, History, Checkpoints, Bookmarks, UI state, or additional Developmental Structure Versions.

## Import UX

Use **Project → Import → Import JSON…** for either Project JSON or Fragment JSON. Tabs, Canvases, Maps, and Map Lines also expose **Import JSON…** in their contextual menus so `current:true` targets are easy to use.

Breadmaker preflights the fragment, displays counts and destinations, then applies it as one structural Undo action.
