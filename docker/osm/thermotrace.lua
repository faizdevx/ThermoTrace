-- ThermoTrace osm2pgsql flex style.
--
-- Selects industrial-related OpenStreetMap objects into ONE raw table,
-- osm_industrial_raw. The sync worker (industrial-data/src/sync) turns that
-- raw table into osm_industries + industrial_sites. The raw table is owned by
-- osm2pgsql: it is kept current by `osm2pgsql-replication update` (append mode),
-- so it always mirrors the selected OSM objects of the loaded extract.
--
-- Selection rules (any one is enough):
--   landuse=industrial            industrial land-use area (NOT a verified factory)
--   industrial=*                  tagged industrial facility / area
--   building=industrial           industrial building
--   man_made=works                factory / works
--   man_made=wastewater_plant|water_works   utility plants
--   power=plant                   power station (thermal relevance)
--
-- Data (c) OpenStreetMap contributors, ODbL 1.0.

local raw = osm2pgsql.define_table({
    name = 'osm_industrial_raw',
    ids = { type = 'any', type_column = 'osm_type', id_column = 'osm_id' },
    columns = {
        { column = 'version',       type = 'int4' },
        { column = 'changeset',     type = 'int8' },
        { column = 'osm_timestamp', sql_type = 'timestamptz' },
        { column = 'tags',          type = 'jsonb' },
        { column = 'geom',          type = 'geometry', projection = 4326, not_null = true },
    },
})

local man_made_values = { works = true, wastewater_plant = true, water_works = true }

local function is_industrial(tags)
    if tags.landuse == 'industrial' then return true end
    if tags.industrial ~= nil then return true end
    if tags.building == 'industrial' then return true end
    if tags.man_made ~= nil and man_made_values[tags.man_made] then return true end
    if tags.power == 'plant' then return true end
    return false
end

local function ts(object)
    if object.timestamp == nil then return nil end
    return os.date('!%Y-%m-%dT%H:%M:%SZ', object.timestamp)
end

local function insert(object, geom)
    if geom == nil or geom:is_null() then return end
    raw:insert({
        version = object.version,
        changeset = object.changeset,
        osm_timestamp = ts(object),
        tags = object.tags,
        geom = geom,
    })
end

function osm2pgsql.process_node(object)
    if is_industrial(object.tags) then
        insert(object, object:as_point())
    end
end

function osm2pgsql.process_way(object)
    if is_industrial(object.tags) and object.is_closed and object.tags.area ~= 'no' then
        insert(object, object:as_polygon())
    end
end

function osm2pgsql.process_relation(object)
    if object.tags.type == 'multipolygon' and is_industrial(object.tags) then
        insert(object, object:as_multipolygon())
    end
end
