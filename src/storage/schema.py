SCHEMA_VERSION = 42

SCHEMA_SQL = r'''CREATE TABLE annotations(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
                chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
                annotation_type TEXT NOT NULL DEFAULT 'highlight',
                label_id INTEGER REFERENCES markup_labels(id) ON DELETE SET NULL,
                start_offset INTEGER NOT NULL DEFAULT 0,
                end_offset INTEGER NOT NULL DEFAULT 0,
                selected_text TEXT NOT NULL DEFAULT '',
                prefix_context TEXT NOT NULL DEFAULT '',
                suffix_context TEXT NOT NULL DEFAULT '',
                thread_id INTEGER,
                body TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                stale INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE "bookmarks"(project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,target_type TEXT NOT NULL CHECK(target_type IN ('chapter','scene','range')),chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,scene_id INTEGER REFERENCES scenes(id) ON DELETE CASCADE,start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,start_offset INTEGER NOT NULL DEFAULT 0,end_offset INTEGER NOT NULL DEFAULT 0,start_selected_text TEXT NOT NULL DEFAULT '',start_prefix_context TEXT NOT NULL DEFAULT '',start_suffix_context TEXT NOT NULL DEFAULT '',end_selected_text TEXT NOT NULL DEFAULT '',end_prefix_context TEXT NOT NULL DEFAULT '',end_suffix_context TEXT NOT NULL DEFAULT '',stale INTEGER NOT NULL DEFAULT 0 CHECK(stale IN (0,1)),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);

CREATE TABLE canvas_edges(
                id INTEGER PRIMARY KEY,
                canvas_id INTEGER NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
                from_node_id INTEGER NOT NULL REFERENCES canvas_nodes(id) ON DELETE CASCADE,
                to_node_id INTEGER NOT NULL REFERENCES canvas_nodes(id) ON DELETE CASCADE,
                from_port TEXT NOT NULL DEFAULT 'right',
                to_port TEXT NOT NULL DEFAULT 'left',
                label TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '',
                marker_id INTEGER REFERENCES markup_labels(id) ON DELETE SET NULL,
                line_style TEXT NOT NULL DEFAULT 'solid',
                end_style TEXT NOT NULL DEFAULT 'arrow',
                line_width REAL NOT NULL DEFAULT 1.5,
                route_style TEXT NOT NULL DEFAULT 'curved',
                developmental_hidden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

CREATE TABLE canvas_nodes(
                id INTEGER PRIMARY KEY,
                canvas_id INTEGER NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
                node_type TEXT NOT NULL DEFAULT 'free',
                shape TEXT NOT NULL DEFAULT 'rect',
                icon TEXT NOT NULL DEFAULT '',
                x REAL NOT NULL DEFAULT 100,
                y REAL NOT NULL DEFAULT 100,
                lane_position INTEGER,
                w REAL NOT NULL DEFAULT 220,
                h REAL NOT NULL DEFAULT 130,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                display_mode TEXT NOT NULL DEFAULT 'full',
                text_align TEXT NOT NULL DEFAULT 'left',
                color TEXT NOT NULL DEFAULT '',
                text_color TEXT NOT NULL DEFAULT '',
                figure_fill_color TEXT NOT NULL DEFAULT '',
                figure_stroke_color TEXT NOT NULL DEFAULT '',
                figure_border_width REAL NOT NULL DEFAULT 1.0,
                figure_layer TEXT NOT NULL DEFAULT 'back',
                figure_line_x1 REAL NOT NULL DEFAULT 0.02,
                figure_line_y1 REAL NOT NULL DEFAULT 0.5,
                figure_line_x2 REAL NOT NULL DEFAULT 0.98,
                figure_line_y2 REAL NOT NULL DEFAULT 0.5,
                label_id INTEGER REFERENCES markup_labels(id) ON DELETE SET NULL,
                chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
                scene_id INTEGER REFERENCES scenes(id) ON DELETE SET NULL,
                image_path TEXT NOT NULL DEFAULT '',
                icon_marker_id INTEGER REFERENCES icon_markers(id) ON DELETE SET NULL,
                developmental_hidden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE canvas_version_items(
                id INTEGER PRIMARY KEY,
                version_id INTEGER NOT NULL REFERENCES canvas_versions(id) ON DELETE CASCADE,
                entity_type TEXT NOT NULL CHECK(entity_type IN ('chapter','scene')),
                entity_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                x REAL NOT NULL DEFAULT 0,
                y REAL NOT NULL DEFAULT 0,
                lane_position INTEGER,
                placed INTEGER NOT NULL DEFAULT 1 CHECK(placed IN (0,1)),
                created_at TEXT NOT NULL
            );

CREATE TABLE canvas_versions(
                id INTEGER PRIMARY KEY,
                canvas_id INTEGER NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                name TEXT NOT NULL,
                workspace_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE canvases(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                parent_id INTEGER REFERENCES canvases(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                canvas_type TEXT NOT NULL DEFAULT 'blank',
                developmental_scope TEXT NOT NULL DEFAULT 'chapters' CHECK(developmental_scope IN ('chapters','scenes')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE chapters(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                tab_id INTEGER REFERENCES document_tabs(id) ON DELETE SET NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_implicit INTEGER NOT NULL DEFAULT 0,
                content_html TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE checkpoints(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                scope TEXT NOT NULL CHECK(scope IN ('chapter','book')),
                name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                source_tab_id INTEGER,
                source_tab_title TEXT NOT NULL DEFAULT '',
                source_tab_created_at TEXT NOT NULL DEFAULT '',
                source_chapter_id INTEGER,
                source_chapter_title TEXT NOT NULL DEFAULT '',
                source_chapter_created_at TEXT NOT NULL DEFAULT '',
                source_chapter_position INTEGER,
                current_source_chapter_id INTEGER,
                current_source_chapter_created_at TEXT NOT NULL DEFAULT '',
                snapshot_blob BLOB NOT NULL,
                snapshot_hash TEXT NOT NULL,
                word_count INTEGER NOT NULL DEFAULT 0,
                is_safety INTEGER NOT NULL DEFAULT 0 CHECK(is_safety IN (0,1)),
                include_scenes INTEGER NOT NULL DEFAULT 1 CHECK(include_scenes IN (0,1)),
                include_markup INTEGER NOT NULL DEFAULT 1 CHECK(include_markup IN (0,1)),
                include_notes INTEGER NOT NULL DEFAULT 1 CHECK(include_notes IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE comment_messages(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                thread_id INTEGER NOT NULL REFERENCES comment_threads(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE comment_threads(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE cuts(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content_html TEXT NOT NULL,
                source_chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            , note TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '', source_project TEXT NOT NULL DEFAULT '', source_chapter TEXT NOT NULL DEFAULT '', source_scene TEXT NOT NULL DEFAULT '');

CREATE TABLE document_tabs(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                parent_id INTEGER REFERENCES document_tabs(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_manuscript INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE icon_markers(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,name)
            );

CREATE TABLE manuscript_blocks(
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                tab_id INTEGER REFERENCES document_tabs(id) ON DELETE SET NULL,
                chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
                scene_id INTEGER REFERENCES scenes(id) ON DELETE SET NULL,
                position REAL NOT NULL,
                block_type TEXT NOT NULL DEFAULT 'paragraph',
                semantic_style TEXT NOT NULL DEFAULT 'body',
                content_html TEXT NOT NULL DEFAULT '',
                text_content TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                image_width INTEGER NOT NULL DEFAULT 100 CHECK(image_width BETWEEN 5 AND 100),
                image_footer TEXT NOT NULL DEFAULT '',
                image_description TEXT NOT NULL DEFAULT '',
                image_row_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE map_items(
                id INTEGER PRIMARY KEY,
                line_id INTEGER NOT NULL REFERENCES map_lines(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                item_type TEXT NOT NULL DEFAULT 'stop' CHECK(item_type IN ('stop','span')),
                title TEXT NOT NULL,
                short_label TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                planned_start REAL,
                planned_end REAL,
                schematic_start REAL,
                schematic_end REAL,
                start_anchor_type TEXT CHECK(start_anchor_type IN ('scene','chapter','manuscript_range') OR start_anchor_type IS NULL),
                start_anchor_id TEXT,
                start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,
                start_offset INTEGER NOT NULL DEFAULT 0,
                end_anchor_type TEXT CHECK(end_anchor_type IN ('scene','chapter','manuscript_range') OR end_anchor_type IS NULL),
                end_anchor_id TEXT,
                end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,
                end_offset INTEGER NOT NULL DEFAULT 0,
                excerpt TEXT NOT NULL DEFAULT '',
                label_offset REAL NOT NULL DEFAULT 0,
                label_pinned INTEGER NOT NULL DEFAULT 0,
                break_after INTEGER NOT NULL DEFAULT 0,
                source_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE map_junctions(
                id INTEGER PRIMARY KEY,
                map_id INTEGER NOT NULL REFERENCES story_maps(id) ON DELETE CASCADE,
                from_line_id INTEGER NOT NULL REFERENCES map_lines(id) ON DELETE CASCADE,
                from_item_id INTEGER REFERENCES map_items(id) ON DELETE SET NULL,
                from_edge TEXT NOT NULL DEFAULT 'point' CHECK(from_edge IN ('point','start','end')),
                from_planned_position REAL,
                from_schematic_position REAL,
                to_line_id INTEGER NOT NULL REFERENCES map_lines(id) ON DELETE CASCADE,
                to_item_id INTEGER REFERENCES map_items(id) ON DELETE SET NULL,
                to_edge TEXT NOT NULL DEFAULT 'point' CHECK(to_edge IN ('point','start','end')),
                to_planned_position REAL,
                to_schematic_position REAL,
                kind TEXT NOT NULL CHECK(kind IN ('branch','merge')),
                created_at TEXT NOT NULL
            );

CREATE TABLE map_lines(
                id INTEGER PRIMARY KEY,
                map_id INTEGER NOT NULL REFERENCES story_maps(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL,
                visible INTEGER NOT NULL DEFAULT 1,
                provenance TEXT NOT NULL DEFAULT 'manual' CHECK(provenance IN ('manual','source')),
                source_template_id TEXT,
                layout_locked INTEGER NOT NULL DEFAULT 0,
                lane_offset REAL NOT NULL DEFAULT 0,
                planned_start REAL NOT NULL DEFAULT 0.0,
                planned_end REAL NOT NULL DEFAULT 1.0,
                schematic_start REAL NOT NULL DEFAULT 0.0,
                schematic_end REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(map_id,name)
            );

CREATE TABLE markup_labels(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,name)
            );

CREATE TABLE review_marker_labels(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,name)
            );

CREATE TABLE projects(
                id INTEGER PRIMARY KEY,
                project_uid TEXT NOT NULL UNIQUE CHECK(length(project_uid)=32 AND project_uid NOT GLOB '*[^0-9a-f]*'),
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                project_generation INTEGER NOT NULL DEFAULT 1 CHECK(project_generation>=0)
            );


CREATE TABLE reviews(
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0),
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','open','paused','closed','archived')),
                reviewer_visibility TEXT NOT NULL DEFAULT 'private' CHECK(reviewer_visibility IN ('private','shared')),
                started_at TEXT,
                closed_at TEXT,
                scope_type TEXT NOT NULL DEFAULT 'manuscript' CHECK(scope_type IN ('manuscript','chapters','scenes')),
                permission_read INTEGER NOT NULL DEFAULT 1 CHECK(permission_read IN (0,1)),
                permission_comment INTEGER NOT NULL DEFAULT 0 CHECK(permission_comment IN (0,1)),
                permission_suggest INTEGER NOT NULL DEFAULT 0 CHECK(permission_suggest IN (0,1)),
                permission_feedback INTEGER NOT NULL DEFAULT 0 CHECK(permission_feedback IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(permission_read=1 OR (permission_comment=0 AND permission_suggest=0))
            );

CREATE TABLE review_participants(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                display_name TEXT COLLATE NOCASE NOT NULL,
                status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN ('not_started','in_progress','completed','revoked')),
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(review_id,display_name),
                UNIQUE(review_id,id)
            );

CREATE TABLE references_map(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,
                end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE SET NULL,
                start_offset INTEGER NOT NULL DEFAULT 0,
                end_offset INTEGER NOT NULL DEFAULT 0,
                excerpt TEXT NOT NULL DEFAULT '',
                relationship TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

CREATE TABLE scenes(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                tab_id INTEGER REFERENCES document_tabs(id) ON DELETE CASCADE,
                position REAL NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE review_scope_chapters(
                id INTEGER PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                UNIQUE(review_id,chapter_id)
            );

CREATE TABLE review_scope_scenes(
                id INTEGER PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                UNIQUE(review_id,scene_id)
            );

CREATE TABLE review_markers(
                id INTEGER PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                marker_id INTEGER NOT NULL REFERENCES review_marker_labels(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0),
                created_at TEXT NOT NULL,
                UNIQUE(review_id,marker_id)
            );


CREATE TABLE review_snapshots(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                round_number INTEGER NOT NULL CHECK(round_number>=1),
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paused','closed')),
                source_generation INTEGER NOT NULL CHECK(source_generation>=0),
                scope_type TEXT NOT NULL CHECK(scope_type IN ('manuscript','chapters','scenes')),
                permission_read INTEGER NOT NULL CHECK(permission_read IN (0,1)),
                permission_comment INTEGER NOT NULL CHECK(permission_comment IN (0,1)),
                permission_suggest INTEGER NOT NULL CHECK(permission_suggest IN (0,1)),
                permission_feedback INTEGER NOT NULL CHECK(permission_feedback IN (0,1)),
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paused_at TEXT,
                closed_at TEXT,
                CHECK(permission_read=1 OR (permission_comment=0 AND permission_suggest=0)),
                UNIQUE(review_id,round_number),
                UNIQUE(review_id,id)
            );

CREATE TABLE review_snapshot_checkpoints(
                id INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                checkpoint_id INTEGER REFERENCES checkpoints(id) ON DELETE SET NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal>=0),
                source_chapter_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,ordinal),
                UNIQUE(checkpoint_id)
            );

CREATE TABLE review_snapshot_markers(
                id INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                source_marker_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,source_marker_id)
            );

CREATE TABLE review_snapshot_chapters(
                id INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                source_chapter_id INTEGER NOT NULL,
                source_tab_id INTEGER,
                ordinal INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                is_implicit INTEGER NOT NULL DEFAULT 0 CHECK(is_implicit IN (0,1)),
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,source_chapter_id)
            );

CREATE TABLE review_snapshot_scenes(
                id INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                source_scene_id INTEGER NOT NULL,
                source_tab_id INTEGER,
                ordinal INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,source_scene_id)
            );

CREATE TABLE review_snapshot_blocks(
                id INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                source_block_id TEXT NOT NULL,
                source_tab_id INTEGER,
                source_chapter_id INTEGER NOT NULL,
                source_scene_id INTEGER,
                ordinal INTEGER NOT NULL,
                position REAL NOT NULL,
                block_type TEXT NOT NULL,
                semantic_style TEXT NOT NULL,
                content_html TEXT NOT NULL DEFAULT '',
                text_content TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                image_width INTEGER NOT NULL DEFAULT 100 CHECK(image_width BETWEEN 5 AND 100),
                image_footer TEXT NOT NULL DEFAULT '',
                image_description TEXT NOT NULL DEFAULT '',
                image_row_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,source_block_id)
            );


CREATE TABLE review_anchors(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                snapshot_id TEXT NOT NULL,
                start_block_id TEXT NOT NULL,
                end_block_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL CHECK(start_offset>=0),
                end_offset INTEGER NOT NULL CHECK(end_offset>=0),
                selected_text TEXT NOT NULL DEFAULT '',
                prefix_context TEXT NOT NULL DEFAULT '',
                suffix_context TEXT NOT NULL DEFAULT '',
                context_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(review_id,id),
                FOREIGN KEY(review_id,snapshot_id) REFERENCES review_snapshots(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(snapshot_id,start_block_id) REFERENCES review_snapshot_blocks(snapshot_id,source_block_id) ON DELETE CASCADE,
                FOREIGN KEY(snapshot_id,end_block_id) REFERENCES review_snapshot_blocks(snapshot_id,source_block_id) ON DELETE CASCADE
            );

CREATE TABLE review_highlights(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL,
                anchor_id TEXT NOT NULL,
                tag TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,anchor_id) REFERENCES review_anchors(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE review_comments(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL,
                anchor_id TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                tag TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                UNIQUE(review_id,id),
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,anchor_id) REFERENCES review_anchors(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE review_comment_replies(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                comment_id TEXT NOT NULL,
                author_kind TEXT NOT NULL CHECK(author_kind IN ('writer','participant')),
                participant_id TEXT,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(review_id,comment_id) REFERENCES review_comments(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE,
                CHECK((author_kind='writer' AND participant_id IS NULL) OR (author_kind='participant' AND participant_id IS NOT NULL))
            );

CREATE TABLE review_suggestions(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL,
                anchor_id TEXT NOT NULL,
                operation TEXT NOT NULL CHECK(operation IN ('insert','delete','replace')),
                original_text TEXT NOT NULL DEFAULT '',
                proposed_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','accepted','rejected','task_created','resolved')),
                applied_generation INTEGER CHECK(applied_generation IS NULL OR applied_generation>=0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,anchor_id) REFERENCES review_anchors(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE questionnaires(
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT COLLATE NOCASE NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,name)
            );

CREATE TABLE questionnaire_questions(
                id TEXT PRIMARY KEY,
                questionnaire_id TEXT NOT NULL REFERENCES questionnaires(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                prompt TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(questionnaire_id,id)
            );

CREATE TABLE review_questionnaires(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                questionnaire_id TEXT NOT NULL REFERENCES questionnaires(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(review_id,questionnaire_id)
            );

CREATE TABLE review_snapshot_questions(
                id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                source_questionnaire_id TEXT NOT NULL,
                source_question_id TEXT NOT NULL,
                questionnaire_name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                prompt TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,id)
            );

CREATE TABLE review_question_answers(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                question_id TEXT NOT NULL REFERENCES review_snapshot_questions(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL,
                answer_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(snapshot_id,question_id,participant_id),
                FOREIGN KEY(review_id,snapshot_id) REFERENCES review_snapshots(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(snapshot_id,question_id) REFERENCES review_snapshot_questions(snapshot_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE review_general_feedback(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(snapshot_id,participant_id),
                FOREIGN KEY(review_id,snapshot_id) REFERENCES review_snapshots(review_id,id) ON DELETE CASCADE,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE review_events(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                participant_id TEXT,
                origin TEXT NOT NULL DEFAULT 'local' CHECK(origin IN ('local','portable','remote')),
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE review_imports(
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                participant_id TEXT,
                package_id TEXT NOT NULL,
                format_version INTEGER NOT NULL CHECK(format_version>=1),
                payload_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(review_id,package_id),
                FOREIGN KEY(review_id,participant_id) REFERENCES review_participants(review_id,id) ON DELETE CASCADE
            );

CREATE TABLE story_maps(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                orientation TEXT NOT NULL DEFAULT 'horizontal' CHECK(orientation IN ('horizontal','vertical')),
                layout_mode TEXT NOT NULL DEFAULT 'scaled' CHECK(layout_mode IN ('schematic','scaled')),
                label_mode TEXT NOT NULL DEFAULT 'minimal' CHECK(label_mode IN ('minimal','full','hidden')),
                ruler TEXT NOT NULL DEFAULT 'chapters' CHECK(ruler IN ('none','chapters','scenes','percent','pages')),
                background TEXT NOT NULL DEFAULT '#f3f3f1',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,name)
            );

CREATE TABLE task_milestones(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE tasks(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                archived_from_status TEXT,
                marker_id INTEGER REFERENCES markup_labels(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE ui_state(
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id,key)
            );

CREATE INDEX idx_annotations_block ON annotations(block_id);

CREATE INDEX idx_annotations_chapter ON annotations(chapter_id);

CREATE INDEX idx_blocks_chapter ON manuscript_blocks(chapter_id, position);

CREATE INDEX idx_blocks_scene ON manuscript_blocks(scene_id, position);

CREATE UNIQUE INDEX idx_canvas_version_entity ON canvas_version_items(version_id,entity_type,entity_id);

CREATE UNIQUE INDEX idx_canvas_versions_position ON canvas_versions(canvas_id,position);

CREATE INDEX idx_checkpoints_current_source_chapter ON checkpoints(project_id,current_source_chapter_id,created_at DESC,id DESC);

CREATE INDEX idx_checkpoints_project ON checkpoints(project_id,created_at DESC,id DESC);

CREATE INDEX idx_checkpoints_source_chapter ON checkpoints(project_id,source_chapter_id,created_at DESC,id DESC);

CREATE INDEX idx_comment_messages_thread ON comment_messages(thread_id,id);

CREATE INDEX idx_comment_threads_project ON comment_threads(project_id,status,created_at);

CREATE INDEX idx_reviews_project ON reviews(project_id,position,id);

CREATE INDEX idx_review_participants_review ON review_participants(review_id,display_name,id);

CREATE INDEX idx_review_scope_chapters_review ON review_scope_chapters(review_id,chapter_id);

CREATE INDEX idx_review_scope_scenes_review ON review_scope_scenes(review_id,scene_id);
CREATE INDEX idx_review_marker_labels_project ON review_marker_labels(project_id,created_at,id);

CREATE INDEX idx_review_markers_review ON review_markers(review_id,position,id);


CREATE UNIQUE INDEX idx_review_snapshots_round ON review_snapshots(review_id,round_number);

CREATE INDEX idx_review_snapshot_chapters_snapshot ON review_snapshot_chapters(snapshot_id,ordinal,id);

CREATE INDEX idx_review_snapshot_scenes_snapshot ON review_snapshot_scenes(snapshot_id,ordinal,id);

CREATE INDEX idx_review_snapshot_blocks_snapshot ON review_snapshot_blocks(snapshot_id,ordinal,id);


CREATE INDEX idx_review_anchors_review ON review_anchors(review_id,snapshot_id,start_block_id,end_block_id,id);

CREATE INDEX idx_review_highlights_review ON review_highlights(review_id,participant_id,status,id);

CREATE INDEX idx_review_comments_review ON review_comments(review_id,participant_id,status,id);

CREATE INDEX idx_review_comment_replies_comment ON review_comment_replies(comment_id,created_at,id);

CREATE INDEX idx_review_suggestions_review ON review_suggestions(review_id,participant_id,status,id);

CREATE INDEX idx_questionnaires_project ON questionnaires(project_id,name,id);

CREATE INDEX idx_questionnaire_questions_questionnaire ON questionnaire_questions(questionnaire_id,position,id);

CREATE INDEX idx_review_questionnaires_review ON review_questionnaires(review_id,position,id);

CREATE INDEX idx_review_snapshot_questions_snapshot ON review_snapshot_questions(snapshot_id,position,id);

CREATE INDEX idx_review_snapshot_checkpoints_snapshot ON review_snapshot_checkpoints(snapshot_id,ordinal,id);
CREATE INDEX idx_review_snapshot_markers_snapshot ON review_snapshot_markers(snapshot_id,position,id);

CREATE INDEX idx_review_question_answers_participant ON review_question_answers(participant_id,snapshot_id,question_id,id);

CREATE INDEX idx_review_general_feedback_review ON review_general_feedback(review_id,participant_id,id);

CREATE INDEX idx_review_events_review ON review_events(review_id,created_at,id);

CREATE INDEX idx_review_imports_review ON review_imports(review_id,imported_at,id);

CREATE INDEX idx_map_items_line ON map_items(line_id,position,id);

CREATE UNIQUE INDEX idx_map_junction_unique ON map_junctions(
                map_id,from_line_id,COALESCE(from_item_id,-1),from_edge,COALESCE(from_planned_position,-1),COALESCE(from_schematic_position,-1),
                to_line_id,COALESCE(to_item_id,-1),to_edge,COALESCE(to_planned_position,-1),COALESCE(to_schematic_position,-1),kind
            );

CREATE INDEX idx_map_junctions_lines ON map_junctions(from_line_id,to_line_id);

CREATE INDEX idx_map_junctions_map ON map_junctions(map_id,id);

CREATE INDEX idx_map_lines_map ON map_lines(map_id,position,id);

CREATE INDEX idx_references_source ON references_map(project_id,source_type,source_id);

CREATE INDEX idx_references_target ON references_map(project_id,target_type,target_id);

CREATE INDEX idx_scenes_tab ON scenes(project_id,tab_id,position,id);

CREATE INDEX idx_story_maps_project ON story_maps(project_id,position,id);

CREATE INDEX idx_task_milestones_task ON task_milestones(task_id,position,id);

CREATE UNIQUE INDEX ux_document_tabs_one_main ON document_tabs(project_id) WHERE is_manuscript=1;

CREATE TRIGGER cleanup_comment_thread_after_anchor_delete
            AFTER DELETE ON annotations WHEN OLD.thread_id IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM annotations WHERE thread_id=OLD.thread_id)
            BEGIN DELETE FROM comment_threads WHERE id=OLD.thread_id; END;

CREATE TRIGGER projects_generation_never_decreases
            BEFORE UPDATE OF project_generation ON projects
            WHEN NEW.project_generation < OLD.project_generation
            BEGIN SELECT RAISE(ABORT,'PROJECT_GENERATION_DECREASE_FORBIDDEN'); END;

CREATE TABLE storage_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE checkpoint_assets(
    hash TEXT PRIMARY KEY,
    payload BLOB NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size>=0),
    created_at TEXT NOT NULL
);

CREATE TABLE checkpoint_asset_refs(
    checkpoint_id INTEGER NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
    asset_path TEXT NOT NULL,
    asset_hash TEXT NOT NULL REFERENCES checkpoint_assets(hash) ON DELETE RESTRICT,
    PRIMARY KEY(checkpoint_id,asset_path)
);

CREATE INDEX idx_checkpoint_asset_refs_hash ON checkpoint_asset_refs(asset_hash);

'''
