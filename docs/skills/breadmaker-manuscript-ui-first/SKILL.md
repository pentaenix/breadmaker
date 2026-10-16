---
name: breadmaker-manuscript-ui-first
description: Develop or modify Breadmaker Editor features while treating the current Manuscript workspace as the canonical UI/UX design system. Before implementing any user-facing change, inspect the current Manuscript implementation, identify the closest existing interaction pattern, reuse it, and regression-test the result instead of inventing a parallel UI language.
---

# Breadmaker: Manuscript UI First

Use this skill whenever continuing development of **Breadmaker Editor**, especially when creating, changing, or reviewing user-facing UI, interactions, panels, toolbars, inspectors, menus, hover descriptions, drag behavior, or navigation.

## Core rule

**The current Manuscript workspace is Breadmaker's primary UI/UX reference.**

Before changing a user-facing feature, inspect how Manuscript currently solves the closest equivalent problem. Do not rely on memory, an old handoff, a previous assistant summary, or an older Breadmaker version when the current source is available.

The objective is not merely to make new UI look visually similar. New work should use the same **interaction language, density, spacing, control hierarchy, hover behavior, panel lifecycle, active states, scrolling behavior, accessibility semantics, and discovery model** as Manuscript unless there is a concrete product reason to diverge.

A new feature should feel as though it had always been part of Breadmaker.

---

## Non-negotiable pre-work gate

Before writing code for any user-facing Breadmaker change:

1. **Open the current Breadmaker source.**
   - Inspect `static/index.html` for the current Manuscript shell and controls.
   - Inspect `static/styles.css` for the actual Manuscript classes, dimensions, spacing, overflow rules, responsive behavior, hover states, and panel layout.
   - Inspect `static/app.js` for the actual interaction lifecycle, timers, positioning helpers, active-state handling, keyboard behavior, panel toggling, and cleanup.
   - Inspect `ARCHITECTURE.md` for the current product contract.
   - Inspect the relevant Manuscript browser/regression tests when they exist.

2. **Find the closest Manuscript analogue.** Examples:
   - toolbar icon button
   - toolbar text action
   - delayed hover description
   - toggle/switch
   - right-side panel
   - compact list row
   - row overflow `⋮` menu
   - add `+` action
   - inspector section
   - context menu
   - modal
   - empty state
   - selectable/active row
   - drag interaction
   - horizontally constrained toolbar

3. **Identify what existing implementation can be reused.** Prefer existing helpers, CSS classes, DOM structures, timing constants, positioning functions, icons, and accessibility patterns over creating near-duplicates.

4. **Only then implement the new behavior.**

If no exact analogue exists, combine the smallest number of existing Breadmaker patterns needed. Do not invent an entirely new design system for one feature.

### Required mental check before coding

Be able to answer all four:

- What Manuscript feature is the closest reference?
- Which current CSS/JS behavior are we reusing?
- What, if anything, must intentionally differ?
- How will we regression-test both the new feature and the Manuscript convention it is supposed to follow?

If those answers are unclear, the UI audit is not finished.

---

## Current Manuscript conventions to verify and follow

These are known Breadmaker conventions, but **always inspect the current source first because Manuscript may evolve**.

### 1. Hover descriptions: no unnecessary info icons

Breadmaker generally explains discoverable controls through delayed hover descriptions rather than permanent `i` buttons.

Current Manuscript patterns use approximately **300 ms delayed hover** for contextual descriptions such as Chapter/Scene details, Markers, Checkpoints, Tasks, Notes, and context-menu help.

Rules:

- Do **not** add a dedicated `i`, `?`, or help icon when an existing interactive control is already the natural hover target.
- Trigger the description from the **actual intended control**, not a large surrounding section merely because it is easier to wire.
- A label should not display a control's description unless Manuscript does the same for that pattern.
- Remove the flyer immediately on pointer leave and whenever its owning panel/view is closed or rerendered.
- Use the current Manuscript positioning logic: prefer the intended nearby side, then flip/clamp to remain inside the viewport.
- Do not rely on native `title` tooltips when Breadmaker already has a styled delayed-hover pattern.
- A disabled control may still need a wrapper-based hover target if the browser cannot emit pointer events on the disabled input itself; preserve the visual/semantic target as narrowly as possible.

Do not merely copy the number `300`; inspect the relevant current Manuscript helper and reuse its lifecycle.

### 2. Toolbars do not compress controls

Manuscript toolbars preserve control size.

- Icon buttons should not shrink to fit available space.
- Opening a side panel should reduce available workspace rather than squeeze toolbar buttons into distorted sizes.
- When the toolbar becomes too narrow, it should become **horizontally scrollable** so every command remains reachable.
- Keep frequent actions as compact icon controls when Manuscript does so.
- Preserve grouping/order: general workspace actions should remain together; specialized feature controls should not arbitrarily interrupt established groups.
- Active/toggled tool buttons must keep their visual state and accessibility state synchronized with the panel/tool they control.

### 3. Side panels follow the Manuscript shell

Do not create a parallel panel language for Canvas, Maps, Structure, or future workspaces.

Before adding a panel, inspect current Manuscript panels such as **Checkpoints, Notes, Markup**, and whichever current panel most closely matches the feature.

Match the current conventions for:

- desktop push-panel vs. mobile overlay behavior
- width and panel footprint
- header height and close behavior
- section rhythm and separators
- internal scrolling ownership
- compact rows/cards
- active-row treatment
- contextual `⋮` actions
- `+` creation affordances
- empty states
- toolbar-button active state while the panel is open
- cleanup when switching to sibling panels

If a side panel causes toolbar buttons to shrink, overlays content unexpectedly, or invents its own navigation grammar, treat that as a design regression.

### 4. Prefer list selection over unnecessary lateral navigation

When managing named saved states or objects, follow the current **Checkpoints-style list model** when appropriate:

- compact rows
- click row to select/open
- clear active-row state
- `⋮` for contextual actions
- compact `+` for creation

Do not add Previous/Next arrows or lateral-navigation controls just because the underlying data is sequential if the rest of Breadmaker uses direct list selection for that kind of object.

### 5. Toggles should look and behave like Breadmaker toggles

When the user is changing an on/off presentation state, use the current Breadmaker switch/toggle pattern rather than a checkbox disguised as a setting.

Inspect the current implementation for:

- dimensions
- transition
- active state
- focus state
- `role="switch"` / `aria-checked` or the current accessibility convention
- disabled state
- delayed description behavior

Do not introduce a new toggle style locally.

### 6. Avoid redundant explanatory prose

Breadmaker aims to be discoverable without turning every workspace into an instruction sheet.

If an interaction is already communicated by:

- its control label,
- hover description,
- cursor/drag behavior,
- selected state,
- existing Breadmaker convention,

then do not add permanent prose that repeats it.

Prefer a quiet delayed description when explanation is still useful.

### 7. Inspectors should expose one clear path per action

Do not create duplicate concepts such as separate controls for operations already represented by an existing relationship or action system.

Examples of the general rule:

- Relationship management should use the established **Add link** hierarchy rather than separate per-type link systems when possible.
- Creation actions that belong to a relationship category should live **inside that category**, not as unrelated first-level peer choices.
- Object-specific controls belong in the object's inspector; workspace/system controls belong in the workspace panel.

Before adding a new inspector section, ask whether Manuscript or an existing Breadmaker inspector already has a canonical home for that operation.

### 8. Contextual actions use the existing overflow/menu language

Use the same `⋮` / right-click menu builders and presentation conventions when actions are contextual.

Avoid stacks of permanent full-width buttons for Rename/Delete/Update/etc. when Manuscript would put them behind a row overflow menu.

When both right-click and `⋮` expose the same object actions, they should call the same action builder rather than drift into parallel implementations.

### 9. Automatic contrast and Marker-derived appearance should reuse the existing rule

When a background color is writer-controlled and Breadmaker already has a contrast rule, use the same current contrast helper rather than creating a second threshold or manual foreground-color requirement.

Do not permanently explain automatic contrast in the inspector if the behavior is already obvious and consistent.

### 10. Responsive behavior must be tested, not assumed

A desktop UI that only works with the side panel closed is not finished.

At minimum, verify the feature with:

- ordinary desktop width
- a narrower desktop width with the side panel open
- the current mobile breakpoint when the feature is available there

Check for:

- shrinking toolbar controls
- clipped buttons
- hidden overflow
- overlapping labels/tool controls
- side panels covering controls unexpectedly
- hover cards leaving the viewport
- fixed-size items unintentionally scaling or overflowing

---

## Manuscript-first workflow for every UI change

### Phase 1 — Audit

Do not modify code yet.

- Run or inspect the current app.
- Navigate through Manuscript.
- Open the relevant Manuscript panels and menus.
- Hover the comparable controls long enough to observe delayed behavior.
- Resize the workspace.
- Open a side panel and observe what happens to the toolbar.
- Inspect active, disabled, empty, selected, hover, and narrow-width states.
- Read the source implementing those behaviors.

When practical, inspect the rendered browser behavior in addition to reading code. CSS names alone are not proof that two interactions behave the same.

### Phase 2 — Map

Write down internally:

| New UI need | Manuscript reference | Reuse/intent |
| --- | --- | --- |
| Example: saved-version list | Checkpoints | compact rows + active row + `⋮` + `+` |
| Example: feature description | delayed Manuscript hover flyer | 300 ms on actual control, no info icon |
| Example: workspace panel | Notes/Checkpoints shell | same push/overlay and scrolling model |

Do this before creating new CSS classes or DOM structures.

### Phase 3 — Implement minimally

- Reuse existing helpers/classes where safe.
- If a shared helper can support both Manuscript and the new feature without changing Manuscript behavior, prefer that over copy/paste.
- If reuse would risk Manuscript, create the smallest compatible extension while preserving its interaction contract.
- Do not refactor unrelated Manuscript code merely to make the new feature easier unless the change is separately justified and tested.

### Phase 4 — Regression test behavior, not strings

Browser tests should prove the actual interaction contract.

Examples:

- button remains the same physical size after a side panel opens
- toolbar becomes horizontally scrollable at narrow width
- hover description does **not** exist before the delay and does after it
- hovering the section label does not trigger a description meant for the actual input
- panel button active state follows panel visibility
- list row activation changes the correct saved state
- `⋮` actions remain contextual
- tooltip/flyer geometry stays inside the viewport

Avoid tests that merely assert implementation strings or obsolete labels when behavior is the real contract.

### Phase 5 — Visual review

Before packaging a UI change:

- inspect it next to Manuscript
- compare density, control sizes, typography, borders, hover states, panel width, and spacing
- test light and dark themes when relevant
- check narrow width with panels open
- verify there is no redundant explanatory copy
- verify there are no one-off `i` icons, custom toggles, or special navigation controls that Manuscript would not use

If the new feature visibly looks like it came from another application, the work is not finished.

---

## Important precedence rules

When guidance conflicts, use this order:

1. **The user's latest explicit instruction**
2. **Current Breadmaker Manuscript behavior/source**
3. Current `ARCHITECTURE.md` product contracts
4. Existing behavior in the target workspace
5. Older tests, handoffs, screenshots, or previous assistant summaries

Older tests must be updated when they encode an intentionally superseded UI pattern. Do not regress the product merely to satisfy a stale string/selector expectation.

Conversely, do not call a failure “stale” simply because it is inconvenient. First verify the current Manuscript pattern and the user's approved change.

---

## Things to avoid

Do **not**:

- invent a new UI pattern before checking Manuscript
- add `i` icons for descriptions when a control can own delayed hover help
- put descriptions on oversized hover targets just because they are easy to wire
- shrink toolbar buttons to fit a side panel
- create a fixed toolbar that clips commands instead of scrolling
- create a custom side-panel layout when Manuscript already has one
- use lateral Previous/Next navigation where a Checkpoints-style list is the established pattern
- add permanent explanatory prose for behavior already communicated through interaction
- expose the same operation in multiple competing inspector sections
- invent different toggle, button, border, active-row, or menu styling for a single workspace
- rely only on screenshots without inspecting the actual interaction code
- rely only on code without checking rendered behavior when browser testing is available
- assume a previous Breadmaker version still represents the current UI standard

---

## Definition of done for a Breadmaker UI change

A user-facing change is not ready merely because its feature works.

It is ready when:

- the closest Manuscript analogue was inspected first
- the new UI follows the current Breadmaker interaction language
- intentional deviations are minimal and justified by the feature itself
- toolbar/panel behavior survives constrained widths
- hover/help behavior matches the current delay and target conventions
- active/disabled/accessibility states are correct
- redundant controls and explanatory copy have been removed
- browser regressions cover the actual interaction behavior
- existing Manuscript behavior remains unchanged unless the user explicitly requested otherwise
- a final visual comparison does not reveal that the feature was designed as a separate system

**Breadmaker should have one UI language. Manuscript defines that language unless the product has a specific reason to do something different.**
