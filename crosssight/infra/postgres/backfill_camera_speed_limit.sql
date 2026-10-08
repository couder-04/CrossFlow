-- Optional follow-up after speed_limit_kmh exists.
-- Existing cameras stay NULL so ClonedPlateRule keeps the urban threshold
-- until an operator sets a limit on a highway-edge camera.
SELECT id
FROM cameras
WHERE speed_limit_kmh IS NULL;
