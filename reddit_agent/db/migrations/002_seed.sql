-- 002_seed.sql
-- Seed the kill switch (single row, starts disabled)
INSERT INTO kill_switch (id, enabled, updated_at)
VALUES (1, FALSE, NOW())
ON CONFLICT (id) DO NOTHING;