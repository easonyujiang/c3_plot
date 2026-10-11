/* NULL parent IDs represent the same root level. Existing duplicates
   require explicit user reconciliation; never silently delete folders. */
CREATE UNIQUE INDEX IF NOT EXISTS idx_folder_sibling_name
ON folders(name, COALESCE(parent_id, -1));
