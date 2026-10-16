---
name: breadmaker-project-builder
description: Create valid Breadmaker Project JSON 1.0 or additive Breadmaker Fragment JSON 1.0 files for Breadmaker 6.1+ from manuscripts, outlines, notes, planning material, or user instructions. Use for whole projects or smaller imports such as Markers, Tabs, Canvases, Cards, Maps, Map Lines, Tasks, Cuts, and related planning material.
---

# Breadmaker Project Builder

Create a `.breadmaker.json` file that Breadmaker can import through **Project → Import → Import JSON…**. The output is a semantic Breadmaker description, not a database dump.

Choose one output mode:

- **Project JSON** (`format: "breadmaker-project"`) when the user wants a complete new project. Import always creates a new project.
- **Fragment JSON** (`format: "breadmaker-fragment"`) when the user wants to add smaller reusable pieces to an existing project: Markers, Tabs, Chapters/Scenes, Canvases/Cards/connectors, Maps/Lines/stations, Tasks/subtasks, Cuts, or combinations of these. Fragment import is additive and Undoable.

## Output rule

When the user asks for a Breadmaker project, produce the actual `.breadmaker.json` artifact rather than pasting a huge JSON document into chat unless they explicitly ask to see the JSON.

Before returning it, validate the entire object against the contract below. Every symbolic reference must resolve. Never invent Breadmaker database IDs or local filesystem paths.

## Root schema

```text
BreadmakerProject {
  format: "breadmaker-project"                    REQUIRED
  schema_version: "1.0"                           REQUIRED
  name: string                                     REQUIRED
  markers?: Marker[]
  manuscript?: { tabs?: Tab[] }
  canvases?: Canvas[]
  maps?: Map[]
  tasks?: Task[]
}
```

If no Main Manuscript tab is supplied, Breadmaker creates an empty `Manuscript` tab. Do not add artificial project categories such as `planning_project`; use the same schema for every kind of project.

## Fragment JSON 1.0

Use Fragment JSON when the requested output belongs inside an existing Breadmaker project instead of creating a whole project. Reuse every Project JSON object schema below exactly; do not invent alternate Card, Map, Task, Marker, or prose formats.

```text
BreadmakerFragment {
  format: "breadmaker-fragment"              REQUIRED
  schema_version: "1.0"                     REQUIRED
  name: string                               REQUIRED; preview label only
  markers?: Marker[]
  manuscript?: { tabs?: Tab[] }
  canvases?: Canvas[]
  maps?: Map[]
  tasks?: Task[]
  cuts?: Cut[]
  mounts?: Mount[]
}
```

A Fragment never changes which Tab is the Main Manuscript. Never emit `main:true` in Fragment Tabs. Fragment JSON 1.0 is additive: do not represent deleting, replacing, or rewriting an existing object.

### Marker reuse

Fragment Markers still require an exact `#RRGGBB` color. If a project already contains the same Marker name with the same exact color, Breadmaker reuses it. Therefore it is safe and preferred to include the Marker definitions that a generated Fragment needs. Same-name/different-color or same-color/different-name combinations are conflicts. A Fragment must contain at least one real addition; never emit an empty/no-op Fragment.

### Mounts

Mounts add smaller pieces to an existing container.

```text
Mount {
  target: Target                            REQUIRED

  // target type tab
  chapters?: Chapter[]

  // target type chapter
  scenes?: Scene[]

  // target type canvas
  nodes?: CanvasNode[]
  connectors?: Connector[]
  layout?: LayoutMode
  layouts?: LayoutGroup[]
  canvases?: Canvas[]                      nested under target

  // target type map
  lines?: MapLine[]
  junctions?: Junction[]

  // target type map_line
  items?: MapItem[]

  // target type task
  subtasks?: Subtask[]
}
```

Target forms:

```json
{"type":"canvas","current":true}
{"type":"canvas","path":["Story","Characters"]}
{"type":"tab","name":"Outline"}
{"type":"chapter","tab":"Outline","name":"Act I"}
{"type":"map","name":"Relationships"}
{"type":"map_line","map":"Relationships","name":"Cinna"}
{"type":"task","name":"Rewrite opening"}
```

Prefer `current:true` when the user intends to import from the contextual menu of the Canvas/Tab/Map they are working in. `current:true` is strict: without a compatible current object Breadmaker will require a destination choice rather than guess. Prefer readable names/paths when the destination is explicitly known. Do not emit Breadmaker database IDs. If a target is ambiguous, Breadmaker asks the user during preview.

For Canvas mounts, automatic layouts are placed after existing objects so imported Cards do not overlap the current workspace. Manual x/y positions remain literal. Connectors in a mount connect nodes created by that Fragment.

For Fragment Developmental Canvases, leave `structure` empty. Fragment JSON 1.0 deliberately keeps Structure Order human-controlled after import.

### Cuts

```text
Cut {
  id: ref-id                               REQUIRED
  title: string                            REQUIRED
  content?: markup-text
  note?: string
  source_label?: string
  source_project?: string
  source_chapter?: string
  source_scene?: string
}
```

A Cut import preserves supplied material in Cuts; it never deletes corresponding Manuscript prose.

### Fragment choice examples

- "Make me 12 character/plot/worldbuilding Markers" -> Fragment containing only `markers`.
- "Create a Canvas for Cinna's arc for my current project" -> Fragment containing a whole `canvas`, or a Canvas mount when the user explicitly wants it nested in the current Canvas.
- "Give me ten Cards for this Canvas" -> one Canvas mount with `target.current:true`, `nodes`, and an automatic layout.
- "Add a relationship line to my Relationships Map" -> one Map mount containing a `MapLine`.
- "Make these discarded paragraphs into Cuts" -> Fragment `cuts`.
- "Build a complete new novel workspace" -> Project JSON, not Fragment JSON.

## Reference IDs

Every addressable object has a globally unique symbolic `id`. Recommended form:

```text
marker:character
tab:manuscript
chapter:opening
scene:arrival
canvas:characters
card:cinna-want
figure:act-one
index:one
routing:branch
map:relationships
map-line:cinna
map-item:eugene
task:rewrite-opening
subtask:read-aloud
```

Use lowercase namespaces and kebab-case tokens. References are strings containing one of these IDs. Check them all before output.

## Markup schema

Prose fields are plain text with a deliberately small inline markup language:

```text
**bold**       -> bold
*italic*       -> italic
__underline__  -> underline
\*             -> literal *
\_             -> literal _
\\             -> literal backslash
```

Escape literal formatting punctuation. Do not emit HTML.

## Marker schema

```text
Marker {
  id: ref-id                     REQUIRED
  name: string                   REQUIRED
  description?: string
  color: "#RRGGBB"              REQUIRED
}
```

Markers are created by the importer with the exact supplied hex color. Marker names and colors must be unique. Prefer Markers for reusable semantic colors. Cards, connectors, and Tasks may reference them.

## Manuscript schema

```text
Tab {
  id: ref-id                     REQUIRED
  name: string                   REQUIRED
  description?: string
  main?: boolean                 default false
  content?: markup-text          prose outside Chapters
  chapters?: Chapter[]
  links?: Link[]
}

Chapter {
  id: ref-id                     REQUIRED
  title: string                  REQUIRED
  description?: string
  content?: markup-text
  scenes?: Scene[]
  links?: Link[]
}

Scene {
  id: ref-id                     REQUIRED
  title: string                  REQUIRED
  description?: string
  content?: markup-text
  links?: Link[]
}
```

Tabs are flat. Use array order for Tabs, Chapters, and Scenes. Exactly one tab should normally have `main:true`. Empty Chapters/Scenes are allowed.

Use Manuscript for actual prose. Use additional Tabs for material the user wants represented as manuscript-style linear content (outline, notes, alternate draft, etc.). Do not assume an Outline tab unless the user asks for one or the requested organization clearly benefits from one.

## Links schema

Simple internal link:

```json
"links": ["chapter:opening"]
```

Internal relationship:

```json
"links": [{"target":"chapter:opening","relationship":"edits"}]
```

External URL:

```json
"links": [{"url":"https://example.com","relationship":"research"}]
```

Prefer linking related Breadmaker objects over copying the same material into every surface.

## Canvas schema

```text
Canvas {
  id: ref-id                     REQUIRED
  name: string                   REQUIRED
  description?: string
  type?: "normal" | "developmental"       default normal
  parent?: canvas-ref
  developmental_scope?: "chapters" | "scenes"
  structure?: chapter-ref[] | scene-ref[]
  nodes?: CanvasNode[]
  connectors?: Connector[]
  layout?: LayoutMode
  layouts?: LayoutGroup[]
  links?: Link[]
}
```

Canvases may nest to arbitrary depth. Never create a parent cycle.

### Card

```text
Card {
  id: ref-id                     REQUIRED
  type: "card"                  REQUIRED
  title?: string
  body?: string
  display?: "full" | "header" | "body"    default full
  shape?: "card" | "text"                   default card
  marker?: marker-ref
  color?: "#RRGGBB"
  text_color?: "#RRGGBB"
  linked_to?: chapter-ref | scene-ref
  position?: Position
  links?: Link[]
}
```

Use either `marker` or `color`, not both. `display` only controls visibility; never delete hidden header/body content.

### Figure

```text
Figure {
  id: ref-id
  type: "figure"
  shape?: "rect" | "circle"
  position?: Position
  links?: Link[]
}
```

### Index

```text
Index {
  id: ref-id
  type: "index"
  value: digits | one-character string
  position?: Position
  links?: Link[]
}
```

### Routing Point

```text
RoutingPoint {
  id: ref-id
  type: "routing"
  position?: Position
  links?: Link[]
}
```

Images are not part of Project JSON 1.0. Never output an image node or a path to a user file.

### Positions and layouts

```text
Position {
  x?: number
  y?: number
  width?: number
  height?: number
}

LayoutMode = "compact" | "horizontal" | "vertical" | "circular" | "manual"

LayoutGroup {
  mode: LayoutMode
  nodes?: node-ref[]
  origin?: {x?: number, y?: number}
  gap?: number
}
```

Prefer automatic layouts. Use manual coordinates only when the user requests precise spatial composition or when semantic grouping cannot be expressed clearly with automatic layouts.

`compact` is the default general layout. Use `horizontal` for sequences, `vertical` for lists/flows, `circular` for peer relationships. Use multiple `layouts` for distinct groups on one Canvas. A node must not appear in more than one explicit layout group.

### Connector

```text
Connector {
  from: node-ref                  REQUIRED
  to: node-ref                    REQUIRED
  from_port?: Port               default right
  to_port?: Port                 default left
  label?: string
  marker?: marker-ref
  color?: "#RRGGBB"
  stroke?: "solid" | "dashed" | "dotted"    default solid
  end?: "arrow" | "none" | "circle" | "diamond" default arrow
  width?: number                 0.5..8, default 1.5
  route?: "straight" | "curved"              default straight
}
```

Ports: `top`, `right`, `bottom`, `left`, `top_left`, `top_right`, `bottom_left`, `bottom_right`, `center`.

Connector endpoints must belong to the same Canvas. Use either Marker or custom color. Default to straight right-output → left-input connectors for generated sequential layouts unless another route better communicates the relationship.

### Developmental Canvas

A Developmental Canvas uses the Main Manuscript as its Structure source.

```text
{
  type: "developmental",
  developmental_scope: "chapters" | "scenes",
  structure: [ordered Main-Manuscript Chapter/Scene refs],
  nodes: [free workspace objects]
}
```

Only Main Manuscript Chapters/Scenes may appear in `structure`. Import creates one initial Structure Version. Do not generate version history.

## Map schema

```text
Map {
  id: ref-id                     REQUIRED
  name: string                   REQUIRED
  description?: string
  orientation?: "horizontal" | "vertical"     default horizontal
  layout_mode?: "scaled" | "schematic"        default scaled
  label_mode?: "minimal" | "full" | "hidden" default minimal
  ruler?: "none" | "chapters" | "scenes" | "percent" | "pages"
  background?: "#RRGGBB"
  lines?: MapLine[]
  junctions?: Junction[]
  links?: Link[]
}

MapLine {
  id: ref-id
  name: string
  description?: string
  color?: "#RRGGBB"
  visible?: boolean
  items?: MapItem[]
  links?: Link[]
}

MapItem {
  id: ref-id
  type?: "stop" | "span"
  title: string
  short_label?: string
  description?: string
  start?: number                 0..1
  end?: number                   0..1, spans only
  anchor?: chapter-ref | scene-ref
  end_anchor?: chapter-ref | scene-ref, spans only
  excerpt?: string
  break_after?: boolean
  links?: Link[]
}

Junction {
  kind?: "branch" | "merge"
  from: JunctionSide
  to: JunctionSide
}

JunctionSide {
  line: map-line-ref
  item?: map-item-ref
  edge?: "point" | "start" | "end"
}
```

Prefer semantic ordering/anchors and let Breadmaker lay Maps out. Use explicit numeric positions only when meaningful.

## Task schema

```text
Task {
  id: ref-id
  title: string
  description?: string
  status?: "open" | "later" | "done" | "archived"
  marker?: marker-ref
  subtasks?: Subtask[]
  links?: Link[]
}

Subtask {
  id: ref-id
  title: string
  description?: string
  completed?: boolean
  links?: Link[]
}
```

Breadmaker implements these subtasks using its existing Task milestone/subtask model.

## Excluded state

Never generate these in schema 1.0:

- images/media or filesystem paths
- Checkpoints
- Undo/History
- bookmarks
- multiple Developmental versions
- current tab/Canvas or selection
- zoom/pan/panel state
- theme/UI preferences
- internal database IDs

## Generation strategy

1. Understand the user's desired project, but do not invent a special project type field.
2. Decide which supplied material belongs in Manuscript/Tabs, Canvas, Map, and Tasks.
3. Assign every addressable entity a unique symbolic ID.
4. Define reusable Markers first, including exact requested hex colors.
5. Build Manuscript/Tabs/Chapters/Scenes in desired order.
6. Build Canvases and their hierarchy. Prefer automatic layouts.
7. Build Maps for information that benefits from lanes/stations/spans.
8. Build Tasks for actionable work, not duplicated story notes.
9. Add cross-links where they add navigation value.
10. Validate every ID/ref, Canvas parent, connector endpoint, Developmental Structure ref, Marker reference, Map reference, and layout group.
11. Emit UTF-8 JSON with `schema_version:"1.0"` and the correct format: `breadmaker-project` for a whole new project, or `breadmaker-fragment` for additive content in an existing project.
12. Save it as a descriptive `.breadmaker.json` file.

## Validation checklist

Before delivery verify all of the following:

- `name` exists and is non-empty.
- IDs are globally unique, including Markers and subtasks.
- All references exist. Generic navigation `links` do not target Markers, and each link uses exactly one of `target` or `url`.
- No object contains both `marker` and `color` where they are mutually exclusive.
- Marker colors are exact six-digit hex values and no two Markers reuse the same name/color.
- For Project JSON, at most one supplied Tab is `main:true`; if none is supplied, the automatic empty Manuscript behavior is intentional. Fragment Tabs never use `main:true`.
- Chapter/Scene order matches the user's intent.
- Literal `*`, `_`, and `\` in prose are escaped when they could be interpreted as markup.
- Canvas nesting contains no cycle.
- Layout nodes belong to their Canvas and are not repeated across layout groups.
- Connector endpoints belong to the same Canvas and do not connect a node to itself.
- Developmental Structure refs belong to the Main Manuscript and match its chapter/scene scope.
- Map names are unique case-insensitively; Map Line names are unique within each Map.
- Map junction lines/items belong to that Map, and any Junction item belongs to the line named on the same Junction side.
- `stop` items do not rely on span-only `end` fields.
- No images, media paths, history, checkpoints, or UI/session state are present.

When discussing **Export JSON Project**, remember that schema 1.0 deliberately refuses lossy manuscript exports: a Scene must have prose, stay within one explicit Chapter, and be contiguous, and unassigned prose cannot follow Scene prose inside that Chapter. Never suggest that Breadmaker silently flattens or reorders those cases.

For exhaustive field constraints, use the matching authoritative contract: `docs/breadmaker-project.schema.json` + `docs/BREADMAKER_PROJECT_JSON.md` for whole projects, or `docs/breadmaker-fragment.schema.json` + `docs/BREADMAKER_FRAGMENT_JSON.md` for fragments.
