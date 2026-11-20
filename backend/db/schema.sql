-- GCC Publyq schema. Source tables mirror the Ward 173 CSVs (read-only to the
-- app). `actions` and `turns` are the only tables the app writes.

-- ── source: GCC records ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS complaints (
  id                 text PRIMARY KEY,
  complaint_date     timestamp NOT NULL,          -- IST, as recorded by GCC
  complainant        text,
  complaint_type     text NOT NULL,
  title              text,
  details_en         text,
  details_ta         text,
  specific_location  text,
  locality           text,
  street             text,
  ward_no            int,
  zone_name          text,
  status             text NOT NULL,               -- GCC status code label
  status_group       text NOT NULL,               -- open | resolved | rejected
  viewed_by          text,                        -- GCC holder code, e.g. AEDN173
  holder_post_id     text,                        -- viewed_by mapped to officials.post_id
  sla_days           numeric,
  is_overdue         boolean NOT NULL DEFAULT false,
  is_safety_risk     boolean NOT NULL DEFAULT false,
  safety_reasons     text,
  urgency_label      text,
  urgency_score      int,
  issue              text,
  issue_group        text,
  department         text,
  whats_failed       text,
  whats_to_resolve   text,
  hotspot_tier       text,
  repeat_cluster_id  int,
  repeat_count       int,
  recurred_after_fix boolean,
  officer_message_count int,
  last_activity_at   timestamp,
  tweet_url          text,
  source_url         text,
  last_fetched_at    timestamptz,
  raw                jsonb NOT NULL,
  search             tsvector GENERATED ALWAYS AS (
    to_tsvector('english',
      coalesce(id,'') || ' ' || coalesce(complaint_type,'') || ' ' || coalesce(details_en,'') || ' ' ||
      coalesce(specific_location,'') || ' ' || coalesce(locality,'') || ' ' || coalesce(street,'') || ' ' ||
      coalesce(issue,'') || ' ' || coalesce(issue_group,''))
  ) STORED
);
CREATE INDEX IF NOT EXISTS complaints_search_idx ON complaints USING gin (search);
CREATE INDEX IF NOT EXISTS complaints_open_idx ON complaints (status_group, is_safety_risk, is_overdue);

CREATE TABLE IF NOT EXISTS complaint_events (
  complaint_id  text NOT NULL REFERENCES complaints(id),
  seq           int  NOT NULL,
  author        text,
  author_type   text,          -- officer | qa
  role          text,
  at            timestamp,
  message_en    text,
  message_ta    text,
  forwarded_to  text,
  PRIMARY KEY (complaint_id, seq)
);

CREATE TABLE IF NOT EXISTS media (
  id            serial PRIMARY KEY,
  complaint_id  text NOT NULL REFERENCES complaints(id),
  file          text NOT NULL UNIQUE,   -- path relative to MEDIA_DIR
  kind          text NOT NULL,          -- photo | video
  uploader      text NOT NULL,          -- complainant | officer
  stage         text,                   -- before | after (officer uploads)
  event_seq     int,
  author        text,
  source_url    text,
  bytes         bigint,
  duplicate_of  text
);
CREATE INDEX IF NOT EXISTS media_complaint_idx ON media (complaint_id);

-- ── source: GCC structure (posts, not people) ──────────────────────────────
CREATE TABLE IF NOT EXISTS officials (
  post_id            text PRIMARY KEY,
  post               text NOT NULL,
  role_code          text,
  role               text NOT NULL,
  department_line    text,
  level              text,
  region             text,
  zone_no            int,
  zone_name          text,
  ward_no            int,
  reports_to_post_id text,
  reports_to_post    text,
  complaints_all     int,
  open_now           int,
  open_overdue       int,
  open_safety_risk   int,
  resolved           int,
  avg_days_to_resolve numeric,
  typical_days_to_resolve numeric,
  typical_hours_to_first_action numeric,
  top_complaint_categories text,
  evidence           text
);
CREATE INDEX IF NOT EXISTS officials_scope_idx ON officials (zone_no, ward_no, role);

CREATE TABLE IF NOT EXISTS reporting_lines (
  from_post_id   text NOT NULL,
  to_post_id     text NOT NULL,
  handoffs       int,
  share_pct      numeric,
  is_primary     boolean,
  PRIMARY KEY (from_post_id, to_post_id)
);

CREATE TABLE IF NOT EXISTS ward_map (
  ward_no                int PRIMARY KEY,
  zone_no                int,
  zone_name              text,
  region                 text,
  assistant_engineer     text,
  sanitary_inspector     text,
  electrical_ae          text,
  ward_committee_officer text,
  mla                    text,
  party                  text,
  constituency           text,
  raw                    jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS sops (
  id               serial PRIMARY KEY,
  issue_group      text NOT NULL,
  sop_type         text NOT NULL UNIQUE,
  owner_role       text NOT NULL,
  approver_role    text,
  priority_tier    text,
  tier_meaning     text,
  safety_override  text,
  diagnose         text[] NOT NULL DEFAULT '{}',
  carry            text[] NOT NULL DEFAULT '{}',
  fix              text,
  not_ours_if      text,
  sop_source       text,
  citywide_complaints int,
  avg_days_to_resolve numeric,
  typical_days_to_resolve numeric,
  comes_back_after_fix_pct numeric,
  who_acted_in_data text
);

-- GCC complaint type -> SOP row, matched within the same issue_group at seed time
CREATE TABLE IF NOT EXISTS type_sop_map (
  complaint_type text PRIMARY KEY,
  sop_id         int REFERENCES sops(id),
  score          numeric NOT NULL
);

-- ── workspace: what this app writes ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS actions (
  id             bigserial PRIMARY KEY,
  complaint_id   text REFERENCES complaints(id),
  kind           text NOT NULL CHECK (kind IN ('escalate','assign','note','follow_up')),
  status         text NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed','confirmed','cancelled','done')),
  target_post_id text REFERENCES officials(post_id),
  note           text,
  due_at         timestamptz,
  payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
  session_id     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  confirmed_at   timestamptz,
  closed_at      timestamptz
);
CREATE INDEX IF NOT EXISTS actions_complaint_idx ON actions (complaint_id);
CREATE INDEX IF NOT EXISTS actions_due_idx ON actions (status, due_at);

CREATE TABLE IF NOT EXISTS turns (
  id          bigserial PRIMARY KEY,
  session_id  text NOT NULL,
  channel     text NOT NULL,          -- web | convoai
  text        text NOT NULL,
  route       text NOT NULL,          -- fast | llm | fallback
  intent      text,
  tools       jsonb NOT NULL DEFAULT '[]'::jsonb,
  reply       text,
  latency_ms  int,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turns_session_idx ON turns (session_id, id);
