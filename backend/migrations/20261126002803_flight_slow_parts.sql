-- A slow request's named parts (backend/flight.py `part`): {name: ms} as JSON, and `rest`, the time no part names.
-- NULL: a route that names none, or a sample from before.
ALTER TABLE flight_slow ADD COLUMN parts TEXT;
