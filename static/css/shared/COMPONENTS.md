# Shared frontend component contract

Breadmaker is build-step-free, so shared components are expressed as canonical DOM/class patterns rather than framework widgets. **Reuse before create** is the default rule for every frontend change.

Before adding a new button, input, close control, modal action, menu row, panel action, or toolbar control:

1. Check this catalog and existing shared selectors first.
2. Reuse the canonical base class/markup when the semantics match.
3. Add a feature class only for feature behavior, state, or a genuinely different modifier.
4. If two unrelated workspaces need the same new presentation grammar, promote that grammar to `static/css/shared/` instead of copying it into both feature owners.
5. Do not copy declarations from a shared component into a feature stylesheet to create a lookalike.
6. Create a new shared primitive only when the existing primitives cannot express the control without changing their semantics.

Feature classes remain useful as JavaScript hooks and narrow modifiers. A class such as `review-toolbar-button` may alter active-state behavior, but it should sit on the shared button stack rather than reimplement the stack.

## Canonical controls

| Intent | Canonical classes / structure | Notes |
| --- | --- | --- |
| Standard toolbar/action button | `toolbar-button` | Bordered surface button. Add a feature hook only when needed. |
| Compact toolbar icon | `toolbar-button icon-only tool-icon-button` | Shared 33×30 centered icon control. `tool-icon-button` replaced the misleading historical `manuscript-tool-button` name in 10.3.8. Feature classes may alter the icon or active state. |
| Borderless action | `bare-button` | Use for lightweight text/icon actions where bordered chrome would add noise. |
| General icon action | `icon-button` | Borderless icon grammar used outside compact document/toolbars. |
| Close affordance | `bare-button ui-close-button` | Canonical × control for modals, panels, Split View, find, drawers, and inspectors. Do not create feature-specific close geometry. |
| Regular body action | `soft-button` | Standard bordered action in cards/body content. |
| Low-emphasis body action | `quiet-button` | Muted action; feature/layout rules may narrow width when needed. |
| Full-width soft action | `full-soft` | Use when the action intentionally fills its container. |
| Destructive semantic modifier | `danger` | Color/semantic modifier, not a standalone button geometry. Pair it with the appropriate base control or an existing container-provided action style. |

## Canonical composition examples

Toolbar icon with a feature hook:

```html
<button class="toolbar-button icon-only tool-icon-button review-toolbar-button"
        type="button"
        aria-label="Participants">
  …
</button>
```

Panel/modal close:

```html
<button class="bare-button ui-close-button" type="button" aria-label="Close">×</button>
```

Feature-specific behavior does **not** justify duplicating shared geometry:

```html
<button class="quiet-button recovery-backup-restore" type="button">Restore</button>
```

Here `quiet-button` owns the common control presentation and `recovery-backup-restore` exists for Recovery-specific behavior/layout.

## Shared structures and helpers

| Intent | Canonical structure/helper | Notes |
| --- | --- | --- |
| Form field | `.field` containing a `label` plus `input`, `textarea`, or `select` | Reuse the shared field grammar instead of restyling inputs per feature. |
| Modal/dialog | `showModal()` / `showNestedModal()` in `core/ui.js` | Reuse the shared modal shell and action row; do not hand-roll an overlay/card for an ordinary dialog. |
| Toast/status message | `toast()` in `core/ui.js` | Shared transient feedback path. |
| Context menu | `openContextMenu()` in `core/ui.js` | Features own their menu item definitions; shared UI owns rendering, submenus, positioning, disabled/danger states, and hover help. |
| Compact collection rail label | `compactRailLabel()` in `core/collections.js` | Shared abbreviation/parser used by feature-specific rail-label wrappers; do not clone its parsing rules in Canvas/Maps/Reviews. |
| Collection quick-add row | `appendCollectionQuickAdd()` in `core/collections.js` | Shared `+` affordance/DOM grammar for collection rails. Features supply only the localized label and action. |
| Panel/modal close | `bare-button ui-close-button` | One close geometry across shared containers. |

## Specialized controls

Some controls are intentionally feature-owned because their visual grammar carries feature semantics rather than generic button semantics. Examples include `review-command-button`, Canvas mode controls, project action cards, and menu/popover rows. Do not promote one merely because another feature has a superficially similar rectangle. Promote only when the semantics, states, interaction shape, and presentation are actually shared.

A specialized control should still reuse a shared base whenever that base is compatible. If it cannot, document the reason beside the feature CSS instead of silently cloning an existing shared component.

## Agent decision rule

When implementing a requested control, answer these questions in order:

- Does an existing Breadmaker control already perform the same interaction role?
- Can its canonical class stack be reused with only a feature hook/modifier?
- If not, is the difference semantic/behavioral or merely visual?
- If the difference is merely visual, extend/promote the shared component instead of cloning it.
- If the difference is truly feature-specific, keep the new grammar in the narrow feature owner.

**A visually similar copied component is considered architecture debt even if it passes tests.**
