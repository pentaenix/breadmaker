# Frontend CSS ownership

## 10.3.9 architecture freeze

The 22-file CSS ownership/cascade boundary is unchanged in 10.3.9 and is now architecture-frozen alongside the JavaScript ownership tree. Do not regroup or merge stylesheets merely to reduce file count; relative order remains runtime behavior. New UI should reuse the canonical shared component grammar in `shared/COMPONENTS.md`, and new feature-specific CSS should extend the narrowest existing owner.

## 10.3.8 shared-component contract

The ownership/cascade boundary remains stable, but shared UI reuse is now explicit. `shared/COMPONENTS.md` is the canonical reuse-before-create catalog for buttons and other shared controls. Feature code should compose canonical shared classes with feature-specific hooks/modifiers instead of copying shared declarations. The first concrete correction promotes the cross-workspace compact toolbar icon from the misleading historical `manuscript-tool-button` name to the neutral `tool-icon-button` primitive used by Manuscript, Canvas, Maps, Reviews, and Reviewer Mode.

This is a naming/ownership correction, not permission to flatten feature-specific components. A control is shared only when its interaction role and state grammar are shared; superficial visual similarity is insufficient.

## 10.3.7 cleanup checkpoint

The 10.3.6 ownership split is green and is now the stable CSS boundary. 10.3.7 performs a conservative cascade cleanup without changing the 22-file order. It removes an earlier declaration only when a later top-level rule with the exact same selector repeats the exact same property, value, and `!important` state. That rule removed 205 redundant declarations and 24 rules that became empty (4,525 source bytes) while preserving the final cascade.

Do **not** treat source absence as proof that a selector is dead: several Breadmaker classes are assembled dynamically. Likewise, do not remove `!important`, merge historical selector layers, introduce `@layer`, or move declarations merely to reduce counts unless the resulting cascade has a concrete behavioral contract and verification coverage. Cleanup should prefer mechanically provable redundancy over aesthetic simplification.

10.3.6 replaces the historical single `static/styles.css` runtime stylesheet with an ordered, build-step-free CSS ownership tree. The browser still loads plain CSS directly from `static/index.html`; there is no preprocessor, bundler, generated CSS, or runtime style loader.

## Cascade contract

**Stylesheet order is runtime behavior.** 10.3.6 was produced by cutting the 10.3.5 stylesheet at ownership boundaries without regrouping or reordering its rules. Concatenating the CSS files declared by `static/index.html` reproduces the 10.3.5 rule sequence (apart from the release-version asset stamp).

`static/styles.css` remains the global foundation owner for theme tokens, scrollbars, the reset, and earliest application-shell primitives. It is not a compatibility facade and must not grow feature-specific rules.

Historical source-contract tests must use `tests.source_contracts.frontend_styles_source()` (or `frontend_styles_source_from_styles_path()` for tests that historically start from `static/styles.css`) rather than reading one physical stylesheet. Browser-contract tests that inline CSS must inline that complete ordered boundary.

Do not use `@import` to hide dependencies. Add a stylesheet to `static/index.html` explicitly at the point where its cascade precedence belongs.

## Runtime order and owners

1. `../styles.css` — global theme/foundation and initial application shell.
2. `shared/workspace-foundations.css` — early cross-workspace primitives and interaction foundations.
3. `canvas/navigation-and-legend.css` — Canvas navigation/legend foundations.
4. `projects/center-interactions.css` — Project Center interaction layer.
5. `canvas/object-presentation.css` — Canvas object/card/connector presentation.
6. `maps/workspace.css` — original Maps workspace/Metro presentation.
7. `manuscript/editor-tools.css` — Editor's Eye, Outline, and nearby Manuscript tools.
8. `projects/history-verification-export.css` — Project History, verification, and export presentation.
9. `canvas/configuration.css` — Canvas configuration typography seed.
10. `projects/metadata-danger.css` — Project metadata and destructive-action presentation.
11. `manuscript/workspace.css` — accumulated Manuscript workspace/tooling presentation through the 4.x line.
12. `projects/settings.css` — Project menu/settings presentation.
13. `canvas/workspace.css` — accumulated Canvas workspace presentation through the 5.x line.
14. `projects/import-browse.css` — public Project import and Browse All Projects presentation.
15. `maps/presentation.css` — Maps inspector, ruler, selector, and presentation controls.
16. `shared/application-controls.css` — application language, shared control grammar, focus/search, and cross-workspace polish.
17. `projects/recovery-backups.css` — Recovery, backup, and destructive-reset presentation.
18. `reviews/workspace.css` — Reviews/Reviewer Mode presentation.
19. `manuscript/media-and-split.css` — Manuscript media and Split View additions.
20. `reviews/manuscript-layer.css` — Manuscript Review working-layer presentation.
21. `profile/local-profile.css` — local profile/account presentation.
22. `shared/startup-hydration.css` — final startup-hydration chrome guard.

Some features intentionally have more than one stylesheet because later historical rules must remain later in the cascade. Those separate files remain intentional where their relative position carries cascade meaning. Consolidation or reordering now requires a concrete behavior-preserving proof rather than a lower file count.

## Ownership rules

- Put new feature-specific styles in the narrowest existing feature owner.
- Put genuinely cross-workspace component grammar in `shared/`, not in whichever feature first needs it.
- Preserve explicit stylesheet order when moving an existing rule; a move that changes precedence is a behavior change.
- Do not duplicate a selector merely to avoid choosing an owner.
- Do not add feature CSS back to `static/styles.css`.
- Prefer mechanically provable redundancy removal over heuristic dead-selector or specificity cleanup.
