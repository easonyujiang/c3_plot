-- Seed standard mission definitions
INSERT OR IGNORE INTO standard_missions (label, c3, dv, group_name, sort_order) VALUES
('GTO (220×35786)',  -16.35, 2451, 'Earth Orbit',    1),
('TLI (Lunar)',       -2.01, 3130, 'Earth Orbit',    2),
('Earth Escape',       0.0,  3221, 'Interplanetary', 3),
('Venus Transfer',     5.2,  3455, 'Interplanetary', 4),
('Mars Transfer',      8.7,  3610, 'Interplanetary', 5),
('Asteroid Belt',     15.0,  3883, 'Interplanetary', 6),
('Jupiter Transfer',  55.0,  5488, 'Outer Planets',  7),
('Saturn Transfer',   50.0,  5298, 'Outer Planets',  8),
('Mercury Transfer',  60.0,  5675, 'Outer Planets',  9),
('Uranus Transfer',   80.0,  6210, 'Outer Planets', 10),
('Neptune Transfer',  90.0,  6540, 'Outer Planets', 11),
('Pluto Transfer',   100.0,  6840, 'Outer Planets', 12);
