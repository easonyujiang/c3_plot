-- Deduplicate standard missions and enforce uniqueness
DELETE FROM standard_missions
WHERE id NOT IN (
    SELECT MIN(id) FROM standard_missions GROUP BY label, c3
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_standard_missions_unique ON standard_missions(label, c3);
