# Scene and tab read latency

This change preserves the Manuscript controls and editor lifecycle. It reduces redundant work beneath them:

- Full Text requests the existing bounded verified-bundle path for small tabs as well as large ones. An eligible small tab uses a bundled read plus final authority validation instead of a manifest, separate chapter-body reads, and final validation. Missing, oversized or unavailable bundles still fall back to bounded chapter paging.
- Independent tab, chapter and scene metadata reads run concurrently with separate copied request contexts. Existing membership checks and the final generation fence remain mandatory.
- Opening a scene already present in the complete Full Text cache projects that verified snapshot only after current server authorization confirms the exact revision. No stale prose is painted pending validation. Actor, project, edit and read-epoch cancellation still apply. A scene moved out of the cached tab falls back to its endpoint.
- Bundled results populate the existing read-only Full Text preview while final validation is pending. This does not publish partial editable state.

The request-count and concurrency assertions are in `tests/test_v10596_scene_tab_latency.py`. Existing bounded-loader, chapter-cache and authorization tests remain applicable. Request-count savings are not a production latency benchmark, and no end-to-end speed percentage is claimed.

Focused validation passed on the current-main integration: five cache/scene safety tests, eight Full Text preview and cached-navigation browser cases, and one actual hosted scene-rendering case. The scene case verifies that three correct paragraphs render after one authority request without another prose download. An earlier local startup timeout cleared when machine load returned to normal; no test deadline was changed. The ordinary complete release gate is still required before deployment.
