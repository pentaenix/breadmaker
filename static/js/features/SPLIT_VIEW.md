# Split View frontend ownership

`split_view.js` owns the shared secondary-pane layout and chooser used to display Manuscript content, Tasks, Canvas, Maps, Checkpoints, and History side by side.

It is intentionally cross-feature presentation infrastructure, so it lives at `features/` rather than under any single product domain.
