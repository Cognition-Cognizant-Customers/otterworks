-- Consecutive lockout cycles, so repeated brute-force bursts back off exponentially
ALTER TABLE users ADD COLUMN lockout_cycles INT NOT NULL DEFAULT 0;
