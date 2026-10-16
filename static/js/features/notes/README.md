# Notes frontend ownership

`comments.js` owns the Manuscript Notes/comments workspace, gutter markers, composer, thread navigation, replies, resolution, editing, deletion, and note-focus highlighting.

Annotation primitives used by Notes continue to live with the Manuscript frontend owners. Do not duplicate Notes behavior in `static/app.js`.
