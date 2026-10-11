-- Indexes for stages and rockets (parallel column is ensured by Python migration to support older SQLite)
CREATE INDEX IF NOT EXISTS idx_rockets_slug ON rockets(slug);
CREATE INDEX IF NOT EXISTS idx_rockets_folder ON rockets(folder_id);
CREATE INDEX IF NOT EXISTS idx_stages_rocket ON stages(rocket_id, stage_index);
CREATE INDEX IF NOT EXISTS idx_stages_multi_ignition ON stages(multi_ignition);
CREATE INDEX IF NOT EXISTS idx_stages_parallel ON stages(parallel);
