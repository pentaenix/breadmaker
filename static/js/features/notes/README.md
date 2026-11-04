# Notes frontend ownership

`comments.js` owns the Manuscript Notes/comments workspace, gutter markers, composer, thread navigation, replies, resolution, editing, deletion, and note-focus highlighting.

Annotation primitives used by Notes continue to live with the Manuscript frontend owners. Do not duplicate Notes behavior in `static/app.js`.

List retrieval accepts the native array or hosted `threads` envelope through `apiList`. Mutations return a complete thread (including replies, anchors and first-message metadata). Refresh the manuscript revision after a follow-up, as after other Notes mutations, so continued writing cannot use a stale hosted generation.
