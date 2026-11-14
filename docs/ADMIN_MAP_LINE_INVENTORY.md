# Current map-line inventory: default-off source

Counts extant line roots under existing story-map roots in active cloud projects.
This does not count stations, stops, spans, cards, history, or lifetime creations.
No title, line field, item, task state, manuscript text or asset is retrieved.

Contract: cloud_map_line_inventory_v1, schema_version1, coverage with start/end,
completeness complete|unknown and consistency non_atomic. Metrics are exactly
current_cloud_projects,current_story_maps,current_map_lines. Complete values are
nonnegative integers. Current projects<=100, maps<=100 across the whole fleet,
and lines<=10000 per map (1000000 total). Unknown means all three values null;
empty populations require successful provider timestamp evidence, never a guess.
Freshness900s and collection60s match existing inventory conventions. Every RPC
is capped by both5s and the caller's remaining absolute deadline.

Read scope: active project status/generation and opaque project identifiers;
name-only story-map projections via select([]); direct bounded COUNTs on those
map roots and their lines subcollections. Parent scans are repeated, and both
active-root generation maps must match. This excludes orphan line descendants of
deleted map parents, and excludes inactive/staged project roots. Plain identity
validation and unexpected projection fields fail closed. No full-document fallback
or collection-group query exists. Existing projects need no text backfill.

This is not a transaction-wide snapshot. Matching observations detect observable
changes between projections, not every concurrent or external unversioned write.
A same-population mutation after the last relevant projection may evade its COUNT
fence. Canonical generation-advancing write paths remain required.

Maximum producer RPCs:504 =4 root RPCs +4*100 project-parent RPCs +100 line COUNTs.
Successful scans contain at most100 map parents;101 is an overflow sentinel.
Conditional planning bound (one matching index entry per record):202 root reads,
up to402 parent document/minimum-empty query reads,202 root/parent aggregate read
units and1100 line aggregate read units =1906 units before operational overhead.
This conservatively combines sentinel and successful limits that may not occur
together. COUNT limit precedes aggregation; billing depends on the actual index
plan and location. This is NOT a hard dollar budget, and failed attempts may cost.
The deployed shared30/month admission,15-minute cooldown and50-second App deadline
must be reused by future composition, not duplicated into another allowance.
Under that shared cap,30 worst-case line attempts imply57180 estimated units plus
existing control/cache/caller overhead, not30 additional attempts.

No new client, credentials, operational document, cache, route, scheduler, runtime
hook or live permission is configured by this source. Future private composition
must verify this producer and its inherited product_inventory implementation
fingerprints, preserve shared admission/transport limits, and forward only totals.

Approval scope before activation: discover story-map identifiers inside active
projects and count line records, without reading names or content; send/cache only
fleet totals in owner-only Admin using existing shared refresh limits. Existing
structural or recency approval is not treated as approval for this new category.

Synthetic tests exercise names-only requests, parent/existence fences, exact504RPC
bound, fleet-wide map limit, zero vs unknown, duplicate/changing parents,
provider/iteration errors, bad aggregate timestamps/counts, closed streams,
absolute deadlines and stale snapshots. No live data or local heavy suite used.
