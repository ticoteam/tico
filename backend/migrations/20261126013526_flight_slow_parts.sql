-- A slow request names where its time went outside SQL: {part: ms} for the routes that time their parts.
ALTER TABLE flight_slow ADD COLUMN parts TEXT;
